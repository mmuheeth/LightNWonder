"""Crop a named region out of a captured frame.

A game config declares its regions as four fractions --
``"cash_meter": [0.0, 0.844468, 1.0, 0.884554]`` is left, top, right and bottom,
each in ``0.0..1.0``. Fractions rather than pixels is the whole point: the same
four numbers describe the same part of the picture whether OBS wrote 1280x720 or
3840x2160, so a change of capture size costs nobody a re-measurement.

The fractions are of a rectangle, and **which** rectangle is the caller's to
choose. :func:`crop` and :meth:`Roi.to_box` take the whole image, which is right
when the image is all picture. A window capture of a portrait game inside a
landscape canvas is not: it has black down both sides whose width depends on the
shape of the game window at that moment, so a region measured against the canvas
comes unaimed the moment the window is resized. For that,
:meth:`Roi.to_box_within` resolves the same four numbers against the part of the
frame the game fills -- :mod:`app.utils.letterbox` is what finds it.

Nothing here knows about game configs or capture runs. It takes an image and
four fractions and hands back the pixels inside them; where the fractions came
from is the caller's business.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

__all__ = ["Roi", "RoiError", "crop", "crop_file", "named_roi"]


class RoiError(ValueError):
    """A region is malformed, unusable, or names nothing that is configured."""


@dataclass(frozen=True)
class Roi:
    """One rectangle, as fractions of the rectangle it will be resolved against.

    Usually the frame, via :meth:`to_box`; a letterboxed capture resolves against
    the part of the frame the game fills instead, via :meth:`to_box_within`.

    Edges are half-open in the same sense as Pillow's own box: ``right`` and
    ``bottom`` are one past the last pixel kept.
    """

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        """Reject a rectangle that no image could have a region at."""
        for name, value in (
            ("left", self.left),
            ("top", self.top),
            ("right", self.right),
            ("bottom", self.bottom),
        ):
            # Also catches NaN, for which every comparison is false.
            if not 0.0 <= value <= 1.0:
                raise RoiError(
                    f"{name} must be a fraction between 0 and 1, got {value!r}"
                )
        if self.left >= self.right:
            raise RoiError(f"left ({self.left}) must be less than right ({self.right})")
        if self.top >= self.bottom:
            raise RoiError(f"top ({self.top}) must be less than bottom ({self.bottom})")

    @classmethod
    def from_sequence(cls, values: Any, *, where: str = "roi") -> Roi:
        """Read a region from decoded JSON: ``[left, top, right, bottom]``.

        An existing :class:`Roi` passes through unchanged, so a caller can take
        either form without first asking which it was given.

        Args:
            values: The decoded value. Untrusted -- it may be any JSON shape.
            where: What is being read, used to make the error locatable.
        """
        if isinstance(values, cls):
            return values
        edges = _four_numbers(values, where=where)
        try:
            return cls(*edges)
        except RoiError as exc:
            # The range errors name an edge but not the region it came from.
            raise RoiError(f"{where}: {exc}") from exc

    @classmethod
    def from_pixels(
        cls, box: Sequence[float], *, width: int, height: int, where: str = "roi"
    ) -> Roi:
        """Convert a pixel box measured on a frame of a known size.

        A region gets measured by opening one screenshot in an image editor and
        reading coordinates off it. This turns that reading into the
        resolution-independent form the rest of the module works in, so the
        conversion happens once instead of at every use.
        """
        if width <= 0 or height <= 0:
            raise RoiError("the frame must have a non-zero width and height")
        left, top, right, bottom = _four_numbers(box, where=where)
        try:
            return cls(
                left=left / width,
                top=top / height,
                right=right / width,
                bottom=bottom / height,
            )
        except RoiError as exc:
            raise RoiError(
                f"{where} does not fit a {width}x{height} frame: {exc}"
            ) from exc

    def to_box(self, width: int, height: int) -> tuple[int, int, int, int]:
        """Pixel box for a frame of this size, in the order Pillow wants it.

        The fractions are of the whole frame. Where the frame is a canvas with
        the picture letterboxed inside it, :meth:`to_box_within` is the one to
        use instead.

        Raises:
            RoiError: if the frame has no area to take a region from.
        """
        if width <= 0 or height <= 0:
            raise RoiError("the frame must have a non-zero width and height")
        return self.to_box_within((0, 0, width, height))

    def to_box_within(
        self, within: tuple[int, int, int, int]
    ) -> tuple[int, int, int, int]:
        """Pixel box for a rectangle *inside* a frame, in the frame's own pixels.

        The fractions are resolved against ``within`` and the result is offset
        back to the frame, so the caller crops the frame it already has rather
        than cropping twice. A region at ``0.0`` starts at the rectangle's left
        edge and one at ``1.0`` ends at its right, whatever the frame around it.

        This is what makes a region survive the game window being resized: the
        canvas OBS writes never changes size, but the part of it the game fills
        does, and a region is aimed at the game.

        Args:
            within: ``(left, top, right, bottom)`` in pixels of the frame --
                :attr:`app.utils.letterbox.ContentBox.box`, in practice.

        Raises:
            RoiError: if that rectangle has no area to take a region from.
        """
        left_edge, top_edge, right_edge, bottom_edge = within
        width, height = right_edge - left_edge, bottom_edge - top_edge
        if width <= 0 or height <= 0:
            raise RoiError("the content box must have a non-zero width and height")
        left, right = _edges(self.left, self.right, width)
        top, bottom = _edges(self.top, self.bottom, height)
        return (
            left_edge + left,
            top_edge + top,
            left_edge + right,
            top_edge + bottom,
        )


def _four_numbers(values: Any, *, where: str) -> tuple[float, float, float, float]:
    """Narrow a decoded JSON value to the four edges of a box.

    Shared by both constructors so a bad config and a bad pixel measurement are
    rejected by the same reading of what "four numbers" means.
    """
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise RoiError(f"{where} must be an array of four numbers")
    if len(values) != 4:
        raise RoiError(
            f"{where} must be [left, top, right, bottom], got {len(values)} numbers"
        )

    edges: list[float] = []
    for value in values:
        # `bool` is an `int`, and `True` as a coordinate is a mistake, not a 1.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RoiError(f"{where} must contain only numbers, got {value!r}")
        edges.append(float(value))
    left, top, right, bottom = edges
    return left, top, right, bottom


def _edges(start: float, end: float, size: int) -> tuple[int, int]:
    """Round one axis of a region to pixel edges, keeping at least one pixel.

    Rounding rather than truncating is what keeps a crop proportional across
    resolutions: at twice the size every edge lands within half a pixel of twice
    its old position, where truncation drifts each edge inwards by up to a whole
    pixel and the region quietly shrinks.

    A region thinner than one pixel is a real possibility on a small frame, and
    a zero-width crop would surface as an empty image far from where the mistake
    was made. One pixel is kept instead.
    """
    low = min(max(round(start * size), 0), size)
    high = min(max(round(end * size), 0), size)
    if high <= low:
        low, high = (low, low + 1) if low < size else (size - 1, size)
    return low, high


def crop(image: Image.Image, roi: Roi | Sequence[float]) -> Image.Image:
    """Return the part of ``image`` inside ``roi``.

    The source is untouched: Pillow's ``crop`` builds a new image.

    Args:
        image: An open image, of any size. Its own dimensions are what the
            fractions are resolved against.
        roi: A :class:`Roi`, or the ``[left, top, right, bottom]`` fractions
            straight out of a config.

    Raises:
        RoiError: if ``roi`` is malformed, or ``image`` has no area.
    """
    region = Roi.from_sequence(roi)
    return image.crop(region.to_box(image.width, image.height))


def crop_file(
    source: Path | str,
    roi: Roi | Sequence[float],
    *,
    destination: Path | None = None,
) -> Image.Image:
    """Crop a region out of an image file, optionally writing it back out.

    The file is read fully and closed before the crop is returned, so the caller
    ends up holding an image rather than a handle on a file that something else
    may still be writing.

    Args:
        source: Path to the image.
        roi: The region, in the same forms :func:`crop` accepts.
        destination: Where to write the crop, if it should be written at all.
            Missing parent directories are created; the format comes from the
            suffix, as Pillow does it.

    Returns:
        The cropped image, whether or not it was also written.

    Raises:
        RoiError: if the file is missing, is not an image, cannot be written, or
            the region is malformed. Pillow's ``UnidentifiedImageError`` is an
            ``OSError``, so "not an image" and "unreadable" arrive by one door.
    """
    try:
        with Image.open(source) as image:
            image.load()
            cropped = crop(image, roi)
    except OSError as exc:
        raise RoiError(f"{source} could not be read as an image: {exc}") from exc

    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            cropped.save(destination)
        except OSError as exc:
            raise RoiError(f"{destination} could not be written: {exc}") from exc
    return cropped


def named_roi(regions: Mapping[str, Any], name: str) -> Roi:
    """Look one region up by name in a config's ``roi`` block.

    That block is passed through by the config loader unvalidated, so this is
    where a typo in a region name or in its numbers becomes a sentence saying
    which region it was and what was wrong with it.
    """
    try:
        raw = regions[name]
    except KeyError:
        known = ", ".join(sorted(regions)) or "none"
        raise RoiError(
            f"no region named {name!r} is configured (configured regions: {known})"
        ) from None
    return Roi.from_sequence(raw, where=f"roi.{name}")
