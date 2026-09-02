"""Turn a sequence of pictures into one short video per reel tile.

A foreign-format module like every other one here: it knows how to write a file
a player can open and nothing about why the pictures were taken. It is the only
module in the tree that imports ``cv2``.

Three things about it are deliberate.

**The frames are held and the file is written at the end**, because the frame
rate is not known until the capture is over. Frames are grabbed as fast as OBS
will answer rather than on a clock -- there is no way to ask it for a stream --
so writing at the rate that was *asked* for would make a slow capture play fast
and misreport how long a win presentation took. The cost is one tile-sized
array per tile per frame (about 55MB for five seconds of a fifteen-tile grid at
ten frames a second) and the gain is a clip whose duration is the wall clock it
covered.

**The codec is chosen by trying it, once, and then used for every tile.**
OpenCV's Windows wheels bundle their own ffmpeg, so which encoders exist is a
property of the wheel rather than of the machine, and ``VideoWriter.isOpened()``
is not on its own proof that the encoder behind it initialised -- an H.264
writer whose openh264 library failed to load still reports open. So
:func:`resolve_codec` writes one throwaway frame and keeps the first candidate
that leaves a non-empty file behind. The default order puts WebM/VP8 first
because it is the only one of the four a browser plays back without a plugin,
which is what makes these clips readable from the dashboard rather than only
from Explorer.

One known cosmetic cost of that default, so nobody goes looking for a bug in
it: writing VP8 puts ``OpenCV: FFMPEG: tag 0x30385056/'VP80' is not supported
with codec id 139 and format 'webm / WebM'`` on stderr once per file. WebM
identifies its codec by id rather than by a fourcc tag, so ffmpeg says it is
dropping the tag it was handed and then writes a perfectly good clip. The line
is an unconditional ``fprintf`` inside OpenCV's ffmpeg backend -- neither
``cv2.utils.logging.setLogLevel`` nor ``OPENCV_FFMPEG_LOGLEVEL`` silences it --
and the only way to be rid of it is to give up browser playback by setting
``ANALYZE_SPIN_TILE_CLIP_CODEC=mp4v``.

**Every tile of one grid is the same pixel size**, which
:meth:`app.utils.reel_grid.ReelGrid.place` already guarantees and this relies
on: a video's frame size is fixed at the writer, so a tile whose crop came back
a pixel short would be silently padded or refused. A cell that does not match
the size the writer was opened with is dropped rather than resized.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

__all__ = [
    "CODECS",
    "Codec",
    "TileClipTarget",
    "TileRecorder",
    "TileVideoError",
    "WrittenClip",
    "codec_named",
    "resolve_codec",
]


class TileVideoError(RuntimeError):
    """No encoder would write, or a file could not be produced."""


@dataclass(frozen=True)
class Codec:
    """One way of writing a video, named the three ways it has to be named: to
    OpenCV, to the filesystem, and to a browser."""

    fourcc: str
    """The four-character code ``cv2.VideoWriter.fourcc`` wants."""

    suffix: str
    """File extension, which also picks the container ffmpeg muxes into."""

    content_type: str
    """What the endpoint serving the file says it is."""


# Tried in this order. VP8 in WebM first because it is the only one a browser
# plays without help; MJPG last because it is the one that is always there.
CODECS: tuple[Codec, ...] = (
    Codec(fourcc="VP80", suffix=".webm", content_type="video/webm"),
    Codec(fourcc="avc1", suffix=".mp4", content_type="video/mp4"),
    Codec(fourcc="mp4v", suffix=".mp4", content_type="video/mp4"),
    Codec(fourcc="MJPG", suffix=".avi", content_type="video/x-msvideo"),
)

# Below one frame a second a container's timebase starts rounding to nothing,
# and a capture that managed two frames should still produce a playable file.
_MIN_FPS = 1.0


def codec_named(name: str) -> Codec:
    """The candidate a setting names, matched on its four-character code.

    Case-insensitive, because a fourcc is written both ways in the wild
    (``avc1`` and ``AVC1``) and a settings file should not have to know which.
    """
    wanted = name.strip().lower()
    for codec in CODECS:
        if codec.fourcc.lower() == wanted:
            return codec
    known = ", ".join(codec.fourcc for codec in CODECS)
    raise TileVideoError(f"{name!r} is not a codec this writes; known: {known}")


def _open(path: Path, codec: Codec, fps: float, size: tuple[int, int]) -> Any:
    """One writer, or the error naming which codec would not open."""
    # `VideoWriter.fourcc`, not the module-level `cv2.VideoWriter_fourcc`: the
    # latter still exists in OpenCV 5 but is gone from the stubs, and this is a
    # strictly typed package.
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter.fourcc(*codec.fourcc), max(fps, _MIN_FPS), size
    )
    if not writer.isOpened():
        writer.release()
        raise TileVideoError(
            f"OpenCV would not open a {codec.fourcc} writer for {path.name}"
        )
    return writer


def resolve_codec(
    fps: float, size: tuple[int, int], *, preferred: Codec | None = None
) -> Codec:
    """The first candidate that actually writes a file at this size.

    Probed rather than assumed: ``isOpened()`` is true for an encoder that then
    fails to initialise, so the only reliable test is to write a frame and look
    at what is on disk. One throwaway file, in the system temp directory rather
    than beside the clips, so a probe never leaves a partial tile behind.

    ``preferred`` still gets probed. A codec named in a setting that this wheel
    cannot write is worth failing over from rather than failing on -- the point
    of the setting is to prefer one, not to forbid the rest.
    """
    candidates = CODECS if preferred is None else (preferred, *CODECS)
    frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    tried: list[str] = []
    with tempfile.TemporaryDirectory(prefix="tile-clip-probe-") as scratch:
        for index, codec in enumerate(candidates):
            if codec.fourcc in tried:
                continue
            tried.append(codec.fourcc)
            path = Path(scratch) / f"probe-{index}{codec.suffix}"
            try:
                writer = _open(path, codec, fps, size)
            except TileVideoError:
                continue
            writer.write(frame)
            writer.release()
            if path.is_file() and path.stat().st_size > 0:
                return codec
    raise TileVideoError(
        "None of the video encoders this build of OpenCV ships would write a "
        f"{size[0]}x{size[1]} clip (tried {', '.join(tried)})"
    )


@dataclass(frozen=True)
class TileClipTarget:
    """One tile to cut out of every frame, in the pixels of the crop the frames
    are handed in as."""

    name: str
    """``r1c1``: the same name the split's own tile file carries."""

    row: int
    column: int

    box: tuple[int, int, int, int]
    """``(left, top, right, bottom)`` inside the crop."""


@dataclass(frozen=True)
class WrittenClip:
    """One tile's finished video."""

    name: str
    row: int
    column: int
    file_name: str
    width: int
    height: int
    frames: int
    bytes_written: int


class TileRecorder:
    """Tiles cut out of frame after frame, held until there is a frame rate.

    Deliberately not a context manager: there is nothing to release until
    :meth:`write` runs, so a caller that never gets that far -- a cancelled run
    -- simply drops the buffers.
    """

    def __init__(self, targets: Sequence[TileClipTarget]) -> None:
        if not targets:
            raise TileVideoError("a clip recorder needs at least one tile")
        self._targets = tuple(targets)
        self._buffers: dict[str, list[np.ndarray[Any, Any]]] = {
            target.name: [] for target in self._targets
        }
        self._frames = 0

    @property
    def frames(self) -> int:
        """How many pictures have been taken in."""
        return self._frames

    def add(self, crop: Image.Image) -> None:
        """Cut every tile out of one reels crop and keep it.

        The cut is copied rather than kept as a view, which is the whole point:
        a slice of the frame would hold the frame alive, and five seconds of
        whole frames is an order of magnitude more memory than five seconds of
        tiles.
        """
        rgb = np.asarray(crop.convert("RGB"))
        for target in self._targets:
            left, top, right, bottom = target.box
            # BGR, because that is the order OpenCV writes; Pillow is RGB.
            self._buffers[target.name].append(rgb[top:bottom, left:right, ::-1].copy())
        self._frames += 1

    def write(
        self, directory: Path, *, fps: float, preferred: Codec | None = None
    ) -> tuple[Codec, list[WrittenClip]]:
        """Write every tile's clip, and say which codec did it.

        One codec for the whole grid, resolved once off the first tile's size --
        every tile of a grid is the same size, so a second probe could only
        reach a different answer by being flaky.
        """
        if self._frames == 0:
            raise TileVideoError(
                "no frames were captured, so there is nothing to write"
            )

        first = self._buffers[self._targets[0].name][0]
        height, width = int(first.shape[0]), int(first.shape[1])
        size = (width, height)
        codec = resolve_codec(fps, size, preferred=preferred)

        directory.mkdir(parents=True, exist_ok=True)
        written: list[WrittenClip] = []
        for target in self._targets:
            cells = [
                cell
                for cell in self._buffers[target.name]
                if cell.shape[0] == height and cell.shape[1] == width
            ]
            if not cells:
                continue
            path = directory / f"{target.name}{codec.suffix}"
            writer = _open(path, codec, fps, size)
            try:
                for cell in cells:
                    writer.write(cell)
            finally:
                writer.release()
            if not path.is_file() or path.stat().st_size == 0:
                raise TileVideoError(
                    f"{codec.fourcc} wrote nothing to {path.name}, so this "
                    "spin's clips are unusable"
                )
            written.append(
                WrittenClip(
                    name=target.name,
                    row=target.row,
                    column=target.column,
                    file_name=path.name,
                    width=width,
                    height=height,
                    frames=len(cells),
                    bytes_written=path.stat().st_size,
                )
            )
        return codec, written
