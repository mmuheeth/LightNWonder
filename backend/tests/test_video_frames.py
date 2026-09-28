"""Reading a clip back as still frames.

Every test writes its own clip rather than shipping one: the point of the
module is the arithmetic between a frame rate, an interval and a frame index,
and a fixture video is the only way to know what the right answer is.

The encoder is whichever one this build of OpenCV will actually write, resolved
the way ``utils/tile_video.py`` resolves it -- a hardcoded fourcc is the thing
that makes a video test fail on someone else's machine.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.utils import tile_video, video_frames

SIZE = (64, 48)
FPS = 10.0


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    """A 30-frame clip at 10fps -- three seconds, each frame a distinct shade so
    a sampled frame can be told from its neighbours."""
    codec = tile_video.resolve_codec(FPS, SIZE)
    path = tmp_path / f"clip{codec.suffix}"
    writer = tile_video._open(path, codec, FPS, SIZE)
    for index in range(30):
        frame = np.full((SIZE[1], SIZE[0], 3), index * 8, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    assert path.is_file() and path.stat().st_size > 0
    return path


def test_a_clip_describes_itself_without_decoding(clip: Path) -> None:
    info = video_frames.probe(clip)
    assert (info.width, info.height) == SIZE
    assert info.fps == pytest.approx(FPS)
    assert info.frame_count == 30
    assert info.duration_seconds == pytest.approx(3.0)


def test_the_interval_is_rounded_to_whole_frames(clip: Path) -> None:
    """0.5s at 10fps is every fifth frame, starting at the first."""
    frames = list(video_frames.sample(clip, interval_seconds=0.5))
    assert [frame.index for frame in frames] == [0, 5, 10, 15, 20, 25]


def test_each_frame_carries_the_time_it_really_lands_on(clip: Path) -> None:
    """Not the time the interval asked for: a clip records at whatever rate it
    managed, and the index over the file's own rate is the honest answer."""
    frames = list(video_frames.sample(clip, interval_seconds=0.7))
    # 0.7s at 10fps rounds to every 7th frame, so 0.0s, 0.7s, 1.4s ...
    assert [frame.index for frame in frames] == [0, 7, 14, 21, 28]
    assert [round(frame.at_seconds, 2) for frame in frames] == [0.0, 0.7, 1.4, 2.1, 2.8]


def test_an_interval_below_one_frame_yields_every_frame(clip: Path) -> None:
    """Rounded up to one rather than down to zero, which would divide by it."""
    frames = list(video_frames.sample(clip, interval_seconds=0.001))
    assert [frame.index for frame in frames] == list(range(30))


def test_frames_come_back_as_rgb_images_ready_to_crop(clip: Path) -> None:
    frame = next(iter(video_frames.sample(clip, interval_seconds=1.0)))
    assert isinstance(frame.image, Image.Image)
    assert frame.image.mode == "RGB"
    assert frame.image.size == SIZE


def test_sampling_is_lazy(clip: Path) -> None:
    """A generator, not a list: 180 frames of a 1080p clip held at once is over
    a gigabyte, and the caller only wants a crop out of each."""
    frames = video_frames.sample(clip, interval_seconds=1.0)
    assert next(iter(frames)).index == 0


def test_a_negative_interval_is_refused(clip: Path) -> None:
    with pytest.raises(video_frames.VideoFrameError, match="has to be positive"):
        list(video_frames.sample(clip, interval_seconds=0))


def test_a_file_that_is_not_a_video_says_so(tmp_path: Path) -> None:
    """cv2 reports this by handing back a capture that is merely not opened, so
    a caller that does not check gets an empty reading instead of an error."""
    impostor = tmp_path / "not-a-clip.mp4"
    impostor.write_bytes(b"certainly not an mp4")
    with pytest.raises(video_frames.VideoFrameError, match="could not be opened"):
        video_frames.probe(impostor)


def test_a_missing_file_says_so(tmp_path: Path) -> None:
    with pytest.raises(video_frames.VideoFrameError, match="could not be opened"):
        list(video_frames.sample(tmp_path / "nope.mp4", interval_seconds=1.0))
