"""Read a recorded clip back as still frames, one every so often.

The counterpart of :mod:`app.utils.tile_video`, which writes video; this reads
it, and is the second module in the codebase to import cv2 for that reason.

Two decisions are load-bearing and neither is the obvious one:

* **Frames are reached by decoding forward, never by seeking.**
  ``cap.set(CAP_PROP_POS_FRAMES, n)`` looks like the direct route and is not:
  it lands on the keyframe before ``n`` and decodes forward from there anyway,
  so sampling a 90s clip re-decodes most of it once per sample. Walking the
  stream once with ``grab()`` -- which advances without unpacking a frame --
  and calling ``retrieve()`` only on the wanted ones is both faster and exact.
* **The interval is honoured in whole frames, and the rate is read from the
  file.** A clip records at whatever OBS achieved, so an interval is rounded to
  a frame count once and each sample carries the timestamp it actually landed
  on rather than the one that was asked for.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
from PIL import Image

__all__ = [
    "SampledFrame",
    "VideoFrameError",
    "VideoInfo",
    "probe",
    "sample",
]


class VideoFrameError(RuntimeError):
    """The file will not open, or reports no usable frame rate."""


@dataclass(frozen=True)
class VideoInfo:
    """What the container says about itself, before any frame is read."""

    width: int
    height: int
    fps: float
    frame_count: int

    @property
    def duration_seconds(self) -> float:
        """Length as the header describes it. Reported rather than trusted --
        a container that under-reports its frame count still decodes past it."""
        return self.frame_count / self.fps if self.fps > 0 else 0.0


@dataclass(frozen=True)
class SampledFrame:
    """One decoded frame, and where in the clip it came from."""

    index: int
    """0-based position in the stream."""

    at_seconds: float
    """Offset into the clip, from the index and the file's own rate -- the time
    the frame really is, not the one the interval asked for."""

    image: Image.Image
    """The frame in RGB, ready for the same cropping any screenshot gets."""


def _open(path: Path) -> cv2.VideoCapture:
    """Open a clip, or say why not. cv2 reports failure by returning a capture
    that is merely not opened, so the check is not optional."""
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        raise VideoFrameError(f"{path.name} could not be opened as a video")
    return capture


def _info(capture: cv2.VideoCapture, path: Path) -> VideoInfo:
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    if not fps > 0:
        raise VideoFrameError(
            f"{path.name} reports no frame rate, so it cannot be sampled"
        )
    return VideoInfo(
        width=int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        height=int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        fps=fps,
        frame_count=int(capture.get(cv2.CAP_PROP_FRAME_COUNT)),
    )


def probe(path: Path) -> VideoInfo:
    """Describe a clip without decoding any of it."""
    capture = _open(path)
    try:
        return _info(capture, path)
    finally:
        capture.release()


def sample(path: Path, *, interval_seconds: float) -> Iterator[SampledFrame]:
    """Yield one frame every ``interval_seconds``, starting with the first.

    A generator rather than a list: a 90s clip at 0.5s is 180 full-size frames,
    and the caller crops each one to a caption before doing anything with it.
    """
    if interval_seconds <= 0:
        raise VideoFrameError(
            f"A sampling interval has to be positive, not {interval_seconds}"
        )

    capture = _open(path)
    try:
        info = _info(capture, path)
        # Rounded once, to at least one frame: an interval below a frame's own
        # length means every frame, not a division by zero.
        step = max(1, round(info.fps * interval_seconds))
        index = 0
        while True:
            # grab() advances the stream without unpacking a frame, so the ones
            # between samples cost only their decode and never a conversion.
            if not capture.grab():
                return
            if index % step == 0:
                ok, frame = capture.retrieve()
                if not ok:
                    return
                yield SampledFrame(
                    index=index,
                    at_seconds=index / info.fps,
                    image=Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)),
                )
            index += 1
    finally:
        capture.release()
