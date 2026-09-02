"""Record one short video per reel position, straight off OBS.

The reel grid already cuts a *screenshot* into tiles, and that is the reading
everything is graded by. This answers the question a still cannot: what one
symbol did after the reels stopped. A winning spin lights the positions that
paid and leaves the rest alone, so fifteen small clips laid out as the grid say
which cells the game thought had won -- evidence about the presentation, next to
the classifier's evidence about the symbols.

Four things about it are worth knowing.

**There is no video stream to subscribe to.** obs-websocket offers screenshots
and a recording, and nothing in between, so a clip is built by asking for
screenshots as fast as OBS will answer and cutting each one up. The frames are
JPEG rather than PNG for exactly that reason -- these are video frames, and
PNG-encoding a 1080p canvas ten times a second is the slowest part of the loop
by a distance.

**The geometry is resolved once, from the first frame.** Finding the content box
of a letterboxed frame means scanning the whole picture, and the game window
does not move or change shape between two frames of one spin, so
:func:`app.services.grid.place_on` is called once and its boxes reused. That
also guarantees every clip is the same size, which the encoder requires.

**Nothing here raises.** A clip is a record, never a reading anything is graded
by, so a machine whose OBS dropped the connection halfway through gets the
frames it managed and an ``error`` saying why there are no more -- and the spin
that was being analysed is unaffected. The one thing a caller must do with the
returned ``error`` is surface it.

**It holds no state**, like :mod:`app.services.roi` and
:mod:`app.services.grid`, so there is no ``reset()`` and nothing in
``conftest.py``.
"""

from __future__ import annotations

import asyncio
import base64
import io
import math
import time
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from app.core.config import settings
from app.core.logging import get_logger
from app.exceptions.base import AppException
from app.schemas.obs import ScreenshotRequest
from app.schemas.tile_clips import TileClip, TileClipSet
from app.services import grid as grid_service
from app.services import obs as obs_service
from app.utils import tile_video

logger = get_logger("tile_clips")

# The floor the settings' own validators do not enforce, since a caller may pass
# its own numbers: a clip of no length is not a clip.
_MIN_SECONDS = 0.1
_MIN_FPS = 1.0

# How much slack the frame budget gets over what was asked for, so a loop that
# runs slightly fast still cannot grow without bound. Multiplicative rather than
# a constant, so raising the two settings raises the ceiling with them.
_FRAME_HEADROOM = 1.5


def _decode(data_uri: str) -> Image.Image:
    """Turn one of OBS's base64 data URIs into an open image.

    The same three lines :mod:`app.services.ocr` has, and deliberately not
    shared with it: OCR raises its own read failure and this raises nothing,
    so what they have in common is the two-line body rather than the behaviour.
    """
    _, _, encoded = data_uri.rpartition(",")
    raw = base64.b64decode(encoded, validate=True)
    image = Image.open(io.BytesIO(raw))
    image.load()
    return image


async def _grab(source_name: str | None) -> Image.Image:
    """One frame of whatever OBS is showing, never written to disk."""
    result = await obs_service.take_screenshot(
        ScreenshotRequest(
            source_name=source_name,
            image_format=settings.ANALYZE_SPIN_TILE_CLIP_IMAGE_FORMAT,
            quality=settings.ANALYZE_SPIN_TILE_CLIP_QUALITY,
        )
    )
    if not result.image_data:
        raise tile_video.TileVideoError("OBS returned a frame with no image data")
    return await asyncio.to_thread(_decode, result.image_data)


def _targets(
    placement: grid_service.FramePlacement,
) -> list[tile_video.TileClipTarget]:
    """The grid's tiles as the encoder wants them -- boxes inside the crop."""
    return [
        tile_video.TileClipTarget(
            name=tile.name, row=tile.row, column=tile.column, box=tile.box
        )
        for tile in placement.tiles
    ]


def _take(
    recorder: tile_video.TileRecorder,
    image: Image.Image,
    box: tuple[int, int, int, int],
) -> None:
    """Crop the reels out of one frame and cut it into tiles. Off the event
    loop: this is a numpy copy per tile per frame, several times a second, in a
    process that is also driving OBS and the i-deck."""
    recorder.add(image.crop(box))


def _preferred() -> tile_video.Codec | None:
    """The codec the settings ask for, or ``None`` to take the first that works."""
    name = settings.ANALYZE_SPIN_TILE_CLIP_CODEC.strip()
    if not name:
        return None
    try:
        return tile_video.codec_named(name)
    except tile_video.TileVideoError as exc:
        # Not fatal, and not worth failing a capture over: the probe below tries
        # every candidate anyway, so a mistyped setting costs the preference and
        # nothing else.
        logger.warning("Ignoring ANALYZE_SPIN_TILE_CLIP_CODEC: %s", exc)
        return None


async def record(
    directory: Path,
    *,
    seconds: float,
    fps: float,
    source_name: str | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> TileClipSet:
    """Grab frames for ``seconds`` and write one clip per tile into ``directory``.

    ``should_stop`` is polled between frames, so a cooperative cancel ends the
    capture at the next one rather than being waited out -- and whatever was
    grabbed before it is still written, since half a clip is more use than none.

    Never raises. Whatever went wrong comes back as ``error`` on the set, with
    the frames that were captured before it beside.
    """
    requested = max(fps, _MIN_FPS)
    window = max(seconds, _MIN_SECONDS)
    budget = math.ceil(window * requested * _FRAME_HEADROOM) + 1
    interval = 1.0 / requested

    recorder: tile_video.TileRecorder | None = None
    placement: grid_service.FramePlacement | None = None
    stamps: list[float] = []
    error: str | None = None

    deadline = time.monotonic() + window
    due = time.monotonic()
    while time.monotonic() < deadline and len(stamps) < budget:
        if should_stop is not None and should_stop():
            break
        try:
            image = await _grab(source_name)
            if placement is None:
                # Once, off the first frame: see the module docstring.
                placement = await asyncio.to_thread(grid_service.place_on, image)
                recorder = tile_video.TileRecorder(_targets(placement))
            if recorder is None:  # pragma: no cover - built with the placement
                break
            await asyncio.to_thread(_take, recorder, image, placement.box)
        except AppException as exc:
            # An OBS drop, an unconfigured grid, an unreadable region: named by
            # whichever service noticed, and kept rather than raised.
            error = exc.message
            break
        except (tile_video.TileVideoError, OSError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            break
        stamps.append(time.monotonic())

        due += interval
        remaining = due - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(remaining, interval))
        else:
            # Behind the clock already: reset the cadence rather than trying to
            # catch up, or a slow stretch would be chased with a burst.
            due = time.monotonic()

    if recorder is None or not stamps:
        return TileClipSet(
            requested_fps=requested,
            error=error or "No frames were captured, so there are no clips",
        )

    span = stamps[-1] - stamps[0]
    # The sampling rate, so the clip plays back over the wall clock it covered
    # rather than over the wall clock that was asked for.
    measured = (len(stamps) - 1) / span if len(stamps) > 1 and span > 0 else requested

    try:
        codec, written = await asyncio.to_thread(
            recorder.write, directory, fps=measured, preferred=_preferred()
        )
    except (tile_video.TileVideoError, OSError) as exc:
        return TileClipSet(
            rows=placement.rows if placement is not None else 0,
            columns=placement.columns if placement is not None else 0,
            frames=len(stamps),
            requested_fps=requested,
            duration_ms=max(0, int(span * 1000)),
            error=f"The clips could not be written: {exc}",
        )

    logger.info(
        "Wrote %d tile clip(s) of %.1fs at %.1f fps (%s) into %s",
        len(written),
        span,
        measured,
        codec.fourcc,
        directory,
    )
    return TileClipSet(
        directory=str(directory),
        rows=placement.rows if placement is not None else 0,
        columns=placement.columns if placement is not None else 0,
        frames=len(stamps),
        fps=round(measured, 3),
        requested_fps=requested,
        duration_ms=max(0, int(span * 1000)),
        codec=codec.fourcc,
        content_type=codec.content_type,
        clips=[
            TileClip(
                name=clip.name,
                row=clip.row,
                column=clip.column,
                file_name=clip.file_name,
                width=clip.width,
                height=clip.height,
                frames=clip.frames,
                bytes_written=clip.bytes_written,
            )
            for clip in written
        ],
        error=error,
    )
