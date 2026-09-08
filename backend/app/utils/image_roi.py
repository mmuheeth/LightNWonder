"""Crop a named region out of a captured frame."""

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
    """One rectangle, as fractions of the rectangle it will be resolved against (usually
    the frame via :meth:`to_box`, or the content box via :meth:`to_box_within`)."""

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
        """Read a region from decoded JSON: ``[left, top, right, bottom]``. An
        existing :class:`Roi` passes through unchanged."""
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
        """Convert a pixel box (measured off a screenshot) to the
        resolution-independent fraction form the rest of the module uses."""
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
        """Pixel box for a frame of this size, in the order Pillow wants it."""
        if width <= 0 or height <= 0:
            raise RoiError("the frame must have a non-zero width and height")
        return self.to_box_within((0, 0, width, height))

    def to_box_within(
        self, within: tuple[int, int, int, int]
    ) -> tuple[int, int, int, int]:
        """Pixel box for a rectangle inside a frame, offset back to the frame's own
        pixels -- what lets a region survive the window being resized."""
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
    """Narrow a decoded JSON value to the four edges of a box."""
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
    """Round one axis of a region to pixel edges, keeping at least one pixel."""
    low = min(max(round(start * size), 0), size)
    high = min(max(round(end * size), 0), size)
    if high <= low:
        low, high = (low, low + 1) if low < size else (size - 1, size)
    return low, high


def crop(image: Image.Image, roi: Roi | Sequence[float]) -> Image.Image:
    """Return the part of ``image`` inside ``roi``. The source is untouched --
    Pillow's ``crop`` builds a new image."""
    region = Roi.from_sequence(roi)
    return image.crop(region.to_box(image.width, image.height))


def crop_file(
    source: Path | str,
    roi: Roi | Sequence[float],
    *,
    destination: Path | None = None,
) -> Image.Image:
    """Crop a region out of an image file, optionally writing it back out."""
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
    """Look one region up by name in a config's ``roi`` block, which is passed
    through unvalidated by the loader."""
    try:
        raw = regions[name]
    except KeyError:
        known = ", ".join(sorted(regions)) or "none"
        raise RoiError(
            f"no region named {name!r} is configured (configured regions: {known})"
        ) from None
    return Roi.from_sequence(raw, where=f"roi.{name}")
