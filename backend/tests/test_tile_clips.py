"""Filming each reel position on its own, for the seconds after a win lands.

Three layers, and they are tested apart because they fail apart:

* :mod:`app.utils.tile_video` turns pictures into files. Every test of it reads
  the file back with OpenCV rather than checking that one exists, because a
  writer whose encoder never initialised leaves a zero-byte file behind and
  reports success -- which is the whole reason the module probes its codec.
* :func:`app.services.grid.place_on` says where a tile is on a frame that was
  never written. The tests paint a colour per position, the way
  ``tests/test_grid.py`` does, so a placement that transposed the matrix or
  resolved the bounds against the frame instead of against the crop is caught by
  the *pixels* rather than by the tile count.
* :func:`app.services.tile_clips.record` is the loop over OBS. It is exercised
  with a fake screenshot call, so nothing here needs OBS, a game, or a window.

And one thing about Analyze Spin: the clips are **not a step**. A test asserts
that, because the sequence is what the dashboard renders as a checklist and
adding a fourteenth row to it is exactly the kind of change that gets made by
accident.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

import cv2
import pytest
from httpx import AsyncClient
from PIL import Image

from app.config.game_config import save_active_game
from app.core.config import settings
from app.exceptions.base import ObsConnectionError
from app.schemas.obs import ScreenshotResult
from app.services import analyze_spin as spin_service
from app.services import grid as grid_service
from app.services import obs as obs_service
from app.services import tile_clips as tile_clips_service
from app.utils import tile_video

API = "/api/analyze-spin"

# The same shape ``tests/test_grid.py`` uses: a 400x400 frame whose reels are the
# bottom-middle 200x300 block, dividing exactly into five 40px columns and three
# 100px rows so no assertion needs a rounding allowance.
FRAME_SIZE = (400, 400)
REELS = [0.25, 0.25, 0.75, 1.0]
REELS_BOX = (100, 100, 300, 400)
ROWS, COLUMNS = 3, 5

BACKGROUND = (10, 20, 30)

# VP8 is lossy and a 40x100 block of flat colour still moves a few levels, so
# every pixel assertion is "near", not "equal".
COLOUR_TOLERANCE = 24


def tile_colour(row: int, column: int) -> tuple[int, int, int]:
    """A colour no other tile has, from the 1-indexed matrix position."""
    return (row * 60, column * 40, 200)


def paint_frame(
    *, rows: int = ROWS, columns: int = COLUMNS, shift: int = 0
) -> Image.Image:
    """A frame whose reels region is painted one colour per tile.

    ``shift`` moves every tile's colour by a constant, which is how a test tells
    one frame of a clip from the next without changing what tile is what.
    """
    image = Image.new("RGB", FRAME_SIZE, BACKGROUND)
    left, top, right, bottom = REELS_BOX
    width, height = right - left, bottom - top
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            box = (
                left + round((column - 1) / columns * width),
                top + round((row - 1) / rows * height),
                left + round(column / columns * width),
                top + round(row / rows * height),
            )
            red, green, blue = tile_colour(row, column)
            image.paste(
                Image.new(
                    "RGB",
                    (box[2] - box[0], box[3] - box[1]),
                    (red, green, min(255, blue - shift)),
                ),
                box,
            )
    return image


def data_uri(image: Image.Image, image_format: str = "JPEG") -> str:
    """One picture the way OBS hands a screenshot back."""
    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    suffix = "jpeg" if image_format == "JPEG" else image_format.lower()
    return f"data:image/{suffix};base64,{encoded}"


def read_back(path: Path) -> list[Any]:
    """Every frame of a written clip, as OpenCV reads them (BGR)."""
    capture = cv2.VideoCapture(str(path))
    frames = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(frame)
    finally:
        capture.release()
    return frames


def centre_rgb(frame: Any) -> tuple[int, int, int]:
    """The middle pixel of an OpenCV frame, in RGB. The middle because a lossy
    codec smears the edges of a flat block and leaves its centre alone."""
    blue, green, red = frame[frame.shape[0] // 2, frame.shape[1] // 2]
    return (int(red), int(green), int(blue))


def near(left: tuple[int, int, int], right: tuple[int, int, int]) -> bool:
    """Whether two colours are the same one, allowing for the encoder."""
    return all(abs(a - b) <= COLOUR_TOLERANCE for a, b in zip(left, right, strict=True))


@pytest.fixture
def captures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the capture directory, below which a run's clips are written."""
    directory = tmp_path / "capture"
    monkeypatch.setattr(
        type(settings), "obs_capture_dir", property(lambda _self: directory)
    )
    return directory


@pytest.fixture
def active_game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A game declaring a reels region and the bounds to divide it by."""
    document = {
        "name": "FortuneOx",
        "process": "FortuneOx.exe",
        "roi": {"reels": REELS},
        "reel_bounds": {"rows": ROWS, "columns": COLUMNS},
    }
    directory = tmp_path / "games"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "FortuneOx.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    selection = tmp_path / "active_game.json"
    save_active_game(selection, "FortuneOx")
    monkeypatch.setattr(
        type(settings), "ideck_game_config_dir", property(lambda _self: directory)
    )
    monkeypatch.setattr(
        type(settings), "ideck_active_game_path", property(lambda _self: selection)
    )
    return path


# --- the encoder ----------------------------------------------------------


def targets(placement: grid_service.FramePlacement) -> list[tile_video.TileClipTarget]:
    """The placement's tiles, as the encoder wants them."""
    return [
        tile_video.TileClipTarget(
            name=tile.name, row=tile.row, column=tile.column, box=tile.box
        )
        for tile in placement.tiles
    ]


def test_a_clip_is_written_for_every_tile(tmp_path: Path, active_game: Path) -> None:
    frame = paint_frame()
    placement = grid_service.place_on(frame)
    recorder = tile_video.TileRecorder(targets(placement))
    for shift in range(6):
        recorder.add(paint_frame(shift=shift * 5).crop(placement.box))

    codec, written = recorder.write(tmp_path / "clips", fps=10.0)

    assert len(written) == ROWS * COLUMNS
    assert {clip.name for clip in written} == {
        f"r{row}c{column}"
        for row in range(1, ROWS + 1)
        for column in range(1, COLUMNS + 1)
    }
    for clip in written:
        path = tmp_path / "clips" / clip.file_name
        assert path.suffix == codec.suffix
        # Read back rather than merely present: an encoder that failed to
        # initialise still leaves a file, which is why the codec is probed.
        assert len(read_back(path)) == 6


def test_each_clip_holds_its_own_tile(tmp_path: Path, active_game: Path) -> None:
    """The assertion that catches a transposed matrix.

    A recorder that swapped rows for columns still writes fifteen correctly
    named files of the right size; only the colour inside one says which tile it
    actually cut.
    """
    frame = paint_frame()
    placement = grid_service.place_on(frame)
    recorder = tile_video.TileRecorder(targets(placement))
    recorder.add(frame.crop(placement.box))

    _, written = recorder.write(tmp_path / "clips", fps=10.0)

    for clip in written:
        frames = read_back(tmp_path / "clips" / clip.file_name)
        assert near(centre_rgb(frames[0]), tile_colour(clip.row, clip.column)), (
            clip.name
        )


def test_a_clip_keeps_the_frames_in_order(tmp_path: Path, active_game: Path) -> None:
    """Three frames of one tile, each a different shade, come back in the order
    they were added -- a buffer that dropped or reordered them would still write
    a playable clip."""
    placement = grid_service.place_on(paint_frame())
    recorder = tile_video.TileRecorder(targets(placement))
    shifts = (0, 40, 80)
    for shift in shifts:
        recorder.add(paint_frame(shift=shift).crop(placement.box))

    _, written = recorder.write(tmp_path / "clips", fps=10.0)

    first = next(clip for clip in written if clip.name == "r1c1")
    frames = read_back(tmp_path / "clips" / first.file_name)
    red, green, blue = tile_colour(1, 1)
    assert len(frames) == len(shifts)
    assert [
        near(centre_rgb(frame), (red, green, blue - shift))
        for frame, shift in zip(frames, shifts, strict=True)
    ] == [True, True, True]


def test_writing_nothing_is_refused(tmp_path: Path) -> None:
    recorder = tile_video.TileRecorder(
        [tile_video.TileClipTarget(name="r1c1", row=1, column=1, box=(0, 0, 8, 8))]
    )

    with pytest.raises(tile_video.TileVideoError, match="no frames"):
        recorder.write(tmp_path, fps=10.0)


def test_a_recorder_needs_at_least_one_tile() -> None:
    with pytest.raises(tile_video.TileVideoError, match="at least one tile"):
        tile_video.TileRecorder([])


def test_an_unknown_codec_name_is_refused() -> None:
    with pytest.raises(tile_video.TileVideoError, match="VP80"):
        tile_video.codec_named("h265")


def test_a_named_codec_is_matched_whatever_its_case() -> None:
    assert tile_video.codec_named("AVC1").suffix == ".mp4"


def test_the_probe_finds_something_this_build_can_write() -> None:
    """Not a test of *which* codec, deliberately: which encoders an OpenCV wheel
    ships is a property of the wheel. What must hold is that one of the four
    works, since the whole feature rests on it."""
    codec = tile_video.resolve_codec(10.0, (40, 100))

    assert codec in tile_video.CODECS


# --- placing the grid on a frame ------------------------------------------


def test_a_frame_is_placed_the_way_a_split_would_place_it(active_game: Path) -> None:
    placement = grid_service.place_on(paint_frame())

    assert placement.game == "FortuneOx"
    assert placement.box == REELS_BOX
    assert (placement.rows, placement.columns) == (ROWS, COLUMNS)
    assert (placement.tile_width, placement.tile_height) == (40, 100)
    assert len(placement.tiles) == ROWS * COLUMNS


def test_a_placed_tile_names_the_pixels_it_covers(active_game: Path) -> None:
    """The boxes are relative to the *crop*, not to the frame -- cropping the
    frame first and then the tile has to land on that tile's own colour."""
    frame = paint_frame()
    placement = grid_service.place_on(frame)
    crop = frame.crop(placement.box)

    tile = next(one for one in placement.tiles if one.name == "r2c3")
    cell = crop.crop(tile.box)

    assert cell.getpixel((cell.width // 2, cell.height // 2)) == tile_colour(2, 3)


def test_a_game_with_no_reel_bounds_cannot_be_placed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "games"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "Bare.json").write_text(
        json.dumps({"name": "Bare", "process": "Bare.exe", "roi": {"reels": REELS}}),
        encoding="utf-8",
    )
    selection = tmp_path / "active_game.json"
    save_active_game(selection, "Bare")
    monkeypatch.setattr(
        type(settings), "ideck_game_config_dir", property(lambda _self: directory)
    )
    monkeypatch.setattr(
        type(settings), "ideck_active_game_path", property(lambda _self: selection)
    )

    with pytest.raises(Exception, match="reel_bounds"):
        grid_service.place_on(paint_frame())


# --- the capture loop -----------------------------------------------------


def fake_screenshots(
    monkeypatch: pytest.MonkeyPatch, frames: list[Image.Image]
) -> list[int]:
    """Answer every screenshot request from ``frames``, cycling, and count them."""
    taken: list[int] = []

    async def take_screenshot(payload: Any) -> ScreenshotResult:
        index = len(taken)
        taken.append(index)
        return ScreenshotResult(
            source_name=payload.source_name or "Scene",
            image_format=payload.image_format,
            image_data=data_uri(frames[index % len(frames)]),
        )

    monkeypatch.setattr(obs_service, "take_screenshot", take_screenshot)
    return taken


async def test_recording_writes_a_clip_per_tile_and_measures_its_own_rate(
    tmp_path: Path, active_game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_screenshots(monkeypatch, [paint_frame(shift=shift) for shift in (0, 30, 60)])

    result = await tile_clips_service.record(tmp_path / "clips", seconds=0.35, fps=20.0)

    assert result.error is None
    assert result.frames >= 2
    assert len(result.clips) == ROWS * COLUMNS
    assert (result.rows, result.columns) == (ROWS, COLUMNS)
    assert result.requested_fps == 20.0
    # Measured, not requested: the loop goes as fast as OBS answers.
    assert result.fps > 0
    assert result.codec is not None
    assert result.content_type is not None
    assert result.directory == str(tmp_path / "clips")
    for clip in result.clips:
        assert (tmp_path / "clips" / clip.file_name).is_file()


async def test_a_clip_holds_the_tile_it_is_named_after(
    tmp_path: Path, active_game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_screenshots(monkeypatch, [paint_frame()])

    result = await tile_clips_service.record(tmp_path / "clips", seconds=0.3, fps=20.0)

    clip = next(one for one in result.clips if one.name == "r3c5")
    frames = read_back(tmp_path / "clips" / clip.file_name)
    assert near(centre_rgb(frames[0]), tile_colour(3, 5))


async def test_a_dropped_obs_connection_is_reported_not_raised(
    tmp_path: Path, active_game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole bargain of this service: a clip is a record, never a reading, so
    losing OBS halfway costs the clips and nothing else."""

    async def take_screenshot(payload: Any) -> ScreenshotResult:
        raise ObsConnectionError("OBS went away")

    monkeypatch.setattr(obs_service, "take_screenshot", take_screenshot)

    result = await tile_clips_service.record(tmp_path / "clips", seconds=0.3, fps=20.0)

    assert result.error == "OBS went away"
    assert result.clips == []
    assert result.directory is None


async def test_frames_taken_before_a_failure_are_still_written(
    tmp_path: Path, active_game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half a clip is more use than none, so a capture that dies on its third
    frame writes the two it had -- with the reason beside them."""
    frame = paint_frame()
    calls = {"n": 0}

    async def take_screenshot(payload: Any) -> ScreenshotResult:
        calls["n"] += 1
        if calls["n"] > 2:
            raise ObsConnectionError("OBS went away")
        return ScreenshotResult(
            source_name="Scene", image_format="jpg", image_data=data_uri(frame)
        )

    monkeypatch.setattr(obs_service, "take_screenshot", take_screenshot)

    result = await tile_clips_service.record(tmp_path / "clips", seconds=5.0, fps=20.0)

    assert result.frames == 2
    assert result.error == "OBS went away"
    assert len(result.clips) == ROWS * COLUMNS


async def test_a_cancel_ends_the_capture_at_its_next_frame(
    tmp_path: Path, active_game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = fake_screenshots(monkeypatch, [paint_frame()])
    stop = {"asked": False}

    def should_stop() -> bool:
        # Ask to stop once a couple of frames are in, so there is something to
        # write -- a cancelled capture still keeps what it had.
        if len(taken) >= 2:
            stop["asked"] = True
        return stop["asked"]

    result = await tile_clips_service.record(
        tmp_path / "clips", seconds=30.0, fps=20.0, should_stop=should_stop
    )

    assert stop["asked"]
    assert result.frames == 2
    assert len(result.clips) == ROWS * COLUMNS


async def test_an_unconfigured_grid_is_reported_on_the_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "games"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "Bare.json").write_text(
        json.dumps({"name": "Bare", "process": "Bare.exe"}), encoding="utf-8"
    )
    selection = tmp_path / "active_game.json"
    save_active_game(selection, "Bare")
    monkeypatch.setattr(
        type(settings), "ideck_game_config_dir", property(lambda _self: directory)
    )
    monkeypatch.setattr(
        type(settings), "ideck_active_game_path", property(lambda _self: selection)
    )
    fake_screenshots(monkeypatch, [paint_frame()])

    result = await tile_clips_service.record(tmp_path / "clips", seconds=0.3, fps=20.0)

    assert result.error is not None
    assert "reels" in result.error
    assert result.clips == []


async def test_a_mistyped_codec_setting_costs_the_preference_and_nothing_else(
    tmp_path: Path, active_game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ANALYZE_SPIN_TILE_CLIP_CODEC", "h265")
    fake_screenshots(monkeypatch, [paint_frame()])

    result = await tile_clips_service.record(tmp_path / "clips", seconds=0.3, fps=20.0)

    assert result.error is None
    assert result.codec in {codec.fourcc for codec in tile_video.CODECS}


# --- how Analyze Spin uses them -------------------------------------------


def test_filming_the_tiles_is_not_a_step_of_the_sequence() -> None:
    """The sequence is what the dashboard renders as a checklist, and these clips
    are not a stage of the spin: nothing is graded by them, so a failure belongs
    on `errors` rather than as a fourteenth row that can go red."""
    keys = [key for key, _ in spin_service._SEQUENCE]

    assert len(keys) == 13
    assert not [key for key in keys if "clip" in key or "tile" in key]


async def test_a_clip_is_served_raw_from_its_own_run(
    client: AsyncClient, captures: Path
) -> None:
    directory = (
        captures
        / settings.ANALYZE_SPIN_DIR_NAME
        / "2026-09-02_10-00-00"
        / settings.ANALYZE_SPIN_TILE_CLIP_DIR_NAME
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "r1c1.webm").write_bytes(b"not really a video, but it is a file")

    response = await client.get(f"{API}/runs/2026-09-02_10-00-00/clips/r1c1.webm")

    assert response.status_code == 200
    assert response.content.startswith(b"not really")


async def test_a_clip_that_was_never_filmed_is_a_404(
    client: AsyncClient, captures: Path
) -> None:
    response = await client.get(f"{API}/runs/2026-09-02_10-00-00/clips/r1c1.webm")

    assert response.status_code == 404


async def test_a_clip_name_cannot_escape_its_run(
    client: AsyncClient, captures: Path
) -> None:
    """Both halves of the URL came off the wire, so both go through the guards."""
    with pytest.raises(Exception, match="Invalid tile clip name"):
        spin_service.clip_path("2026-09-02_10-00-00", "../../secrets.txt")
