"""Cutting a configured region out of a captured frame.

Everything here runs off real image files in ``tmp_path``: the frames are small
Pillow images written to disk, and the screenshots directory is pointed at a
temporary one, so no test depends on what the developer's own
``obs-captured-files`` happens to contain.

The crop is checked by its *pixels*, not only by its reported size. A region is
four fractions of a frame, and the mistake worth catching is one that still
produces a correctly-sized rectangle taken from the wrong place -- so the frames
are built with a known block of colour inside the region and plain background
outside it, and the assertion is that the crop came back that colour.

Some frames here are letterboxed on purpose. OBS writes every frame at its
canvas size and fits the game window inside it, so resizing the simulator gives
a differently-shaped picture in an identically-sized file -- which is exactly the
case a region measured against the canvas fails at. Those frames are built with
pure black bars round a content box, and the region is painted relative to that
box rather than to the frame.
"""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from PIL import Image

from app.config.game_config import save_active_game
from app.config.runtime import settings
from tests.asserts import assert_failure, assert_success

API = "/api/roi"

# The cash meter as FortuneOx's shipped config declares it.
CASH_METER = [0.229264, 0.844468, 0.762349, 0.884554]
# A quarter of the frame, offset from the origin. Round fractions on a 400x200
# frame so the expected pixel box is exact and needs no rounding allowance.
QUARTER = [0.25, 0.5, 0.5, 1.0]

BACKGROUND = (10, 20, 30)
REGION_FILL = (200, 100, 50)
# Bars are pure black, the way OBS pads a canvas round a source. The background
# above is dim but well clear of the detection threshold, so a frame with no
# bars is all content.
BARS = (0, 0, 0)

# The two real captures of one FortuneOx window, resized: a 632x1080 simulator
# lands as a 421-wide strip of the 1280x720 canvas, and widening it lands as 643.
NARROW_CONTENT = (429, 0, 850, 720)
WIDE_CONTENT = (318, 0, 961, 720)


def write_game_config(directory: Path, document: dict[str, Any]) -> Path:
    """Write one game config into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{document['name']}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def write_frame(
    path: Path,
    *,
    size: tuple[int, int] = (400, 200),
    region: list[float] | None = None,
    content: tuple[int, int, int, int] | None = None,
) -> Path:
    """Write a frame with ``region`` filled in a colour the background is not.

    Filling the region is what lets a test assert the crop came from the right
    part of the picture rather than merely being the right shape.

    With ``content`` given, the frame is black outside that box and the region is
    painted relative to it -- a window capture letterboxed inside a canvas, which
    is what every real frame from OBS is.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, BARS if content is not None else BACKGROUND)
    left, top, right, bottom = content if content is not None else (0, 0, *size)
    if content is not None:
        image.paste(
            Image.new("RGB", (right - left, bottom - top), BACKGROUND), (left, top)
        )
    if region is not None:
        width, height = right - left, bottom - top
        box = (
            left + round(region[0] * width),
            top + round(region[1] * height),
            left + round(region[2] * width),
            top + round(region[3] * height),
        )
        image.paste(
            Image.new("RGB", (box[2] - box[0], box[3] - box[1]), REGION_FILL), box
        )
    image.save(path)
    return path


def corners_are_region(crop: Image.Image) -> bool:
    """Whether all four corners of a crop are the region's own colour.

    The assertion that a crop is the right *place* and not merely the right
    shape -- a box off by more than its own border catches background.
    """
    rgb = crop.convert("RGB")
    edge_x, edge_y = crop.width - 1, crop.height - 1
    return all(
        rgb.getpixel(corner) == REGION_FILL
        for corner in ((0, 0), (edge_x, 0), (0, edge_y), (edge_x, edge_y))
    )


def decode(image_data: str) -> Image.Image:
    """Open the crop the API returned as a data URI."""
    assert image_data.startswith("data:image/png;base64,")
    raw = base64.b64decode(image_data.split(",", 1)[1])
    image = Image.open(io.BytesIO(raw))
    image.load()
    return image


@pytest.fixture
def screenshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the screenshots directory at a temporary one.

    Patched on the derived property rather than on ``OBS_SCREENSHOT_DIR``, so a
    test never depends on the configured subdirectory name.
    """
    directory = tmp_path / "screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        type(settings),
        "obs_dashboard_screenshot_dir",
        property(lambda _self: directory),
    )
    return directory


@pytest.fixture
def active_game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A game config declaring two regions."""
    directory = tmp_path / "games"
    write_game_config(
        directory,
        {
            "name": "FortuneOx",
            "process": "FortuneOx.exe",
            "roi": {"cash_meter": CASH_METER, "quarter": QUARTER},
        },
    )
    selection = tmp_path / "active_game.json"
    save_active_game(selection, "FortuneOx")
    monkeypatch.setattr(
        type(settings), "ideck_game_config_dir", property(lambda _self: directory)
    )
    monkeypatch.setattr(
        type(settings), "ideck_active_game_path", property(lambda _self: selection)
    )
    return directory


# --- the catalog ----------------------------------------------------------


async def test_regions_lists_what_the_game_declares(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    response = await client.get(f"{API}/regions")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["game"] == "FortuneOx"
    # Sorted by name, and each carries the qualified label the dropdown shows.
    assert [region["region"] for region in data["regions"]] == [
        "cash_meter",
        "quarter",
    ]
    assert [region["label"] for region in data["regions"]] == [
        "roi.cash_meter",
        "roi.quarter",
    ]
    assert data["regions"][0]["roi"] == CASH_METER
    assert data["regions"][0]["error"] is None


async def test_regions_reports_no_frame_before_a_screenshot_is_taken(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    """An empty directory is the panel's empty state, not a failed request."""
    response = await client.get(f"{API}/regions")

    assert response.status_code == 200
    assert assert_success(response.json())["latest_frame"] is None


async def test_regions_reports_the_newest_frame(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    write_frame(screenshots / "older.png", size=(320, 180))
    newer = write_frame(screenshots / "newer.png", size=(400, 200))
    # Mtime, not name: 'older' sorts after 'newer' as a string.
    _make_newest(newer)

    response = await client.get(f"{API}/regions")

    frame = assert_success(response.json())["latest_frame"]
    assert frame["file_name"] == "newer.png"
    assert (frame["width"], frame["height"]) == (400, 200)


async def test_regions_ignores_files_that_are_not_images(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    """A stray log beside the frames is skipped rather than offered as one."""
    frame = write_frame(screenshots / "shot.png")
    notes = screenshots / "notes.txt"
    notes.write_text("not a frame", encoding="utf-8")
    _make_newest(notes)

    response = await client.get(f"{API}/regions")

    assert assert_success(response.json())["latest_frame"]["file_name"] == frame.name


async def test_regions_lists_a_malformed_region_with_its_reason(
    client: AsyncClient,
    tmp_path: Path,
    screenshots: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in the numbers is visible in the dropdown, not only on extraction."""
    directory = tmp_path / "games"
    write_game_config(
        directory,
        {"name": "Broken", "roi": {"bad": [0.5, 0.1, 0.2, 0.9], "ok": QUARTER}},
    )
    selection = tmp_path / "active_game.json"
    save_active_game(selection, "Broken")
    monkeypatch.setattr(
        type(settings), "ideck_game_config_dir", property(lambda _self: directory)
    )
    monkeypatch.setattr(
        type(settings), "ideck_active_game_path", property(lambda _self: selection)
    )

    response = await client.get(f"{API}/regions")

    assert response.status_code == 200
    regions = {
        region["region"]: region
        for region in assert_success(response.json())["regions"]
    }
    assert "left (0.5) must be less than right (0.2)" in regions["bad"]["error"]
    assert regions["ok"]["error"] is None


async def test_regions_404s_when_the_game_declares_none(
    client: AsyncClient,
    tmp_path: Path,
    screenshots: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "games"
    write_game_config(directory, {"name": "Bare", "process": "Bare.exe"})
    selection = tmp_path / "active_game.json"
    save_active_game(selection, "Bare")
    monkeypatch.setattr(
        type(settings), "ideck_game_config_dir", property(lambda _self: directory)
    )
    monkeypatch.setattr(
        type(settings), "ideck_active_game_path", property(lambda _self: selection)
    )

    response = await client.get(f"{API}/regions")

    assert response.status_code == 404
    assert_failure(response.json(), code="ROI_REGION_NOT_FOUND")


# --- extraction -----------------------------------------------------------


async def test_extract_crops_the_declared_region_of_the_newest_frame(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    """The crop is the right size *and* comes from the right part of the frame."""
    write_frame(screenshots / "shot.png", size=(400, 200), region=QUARTER)

    response = await client.post(f"{API}/extract", json={"region": "quarter"})

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["game"] == "FortuneOx"
    assert data["region"] == "quarter"
    assert data["roi"] == QUARTER
    assert data["source"]["file_name"] == "shot.png"
    # 0.25..0.5 of 400 and 0.5..1.0 of 200.
    assert data["box"] == [100, 100, 200, 200]
    assert (data["width"], data["height"]) == (100, 100)

    crop = decode(data["image_data"])
    assert crop.size == (100, 100)
    # Every corner is the region's colour, so the crop is not merely the right
    # shape taken from somewhere else.
    for corner in ((0, 0), (99, 0), (0, 99), (99, 99)):
        assert crop.convert("RGB").getpixel(corner) == REGION_FILL


async def test_extract_scales_the_same_fractions_to_any_frame_size(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    """The whole point of fractions: no re-measurement at a new capture size."""
    write_frame(screenshots / "small.png", size=(400, 200), region=QUARTER)
    response = await client.post(f"{API}/extract", json={"region": "quarter"})
    small = assert_success(response.json())

    big = write_frame(screenshots / "big.png", size=(1600, 800), region=QUARTER)
    _make_newest(big)
    response = await client.post(f"{API}/extract", json={"region": "quarter"})
    large = assert_success(response.json())

    assert small["box"] == [100, 100, 200, 200]
    assert large["box"] == [400, 400, 800, 800]
    # Four times the frame, four times the crop, same region of the picture.
    assert (large["width"], large["height"]) == (
        small["width"] * 4,
        small["height"] * 4,
    )


# --- the letterbox --------------------------------------------------------


async def test_extract_resolves_the_region_against_the_game_not_the_canvas(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    """A region is fractions of the picture, not of the black round it."""
    write_frame(
        screenshots / "shot.png",
        size=(400, 200),
        region=QUARTER,
        content=(100, 0, 300, 200),
    )

    response = await client.post(f"{API}/extract", json={"region": "quarter"})

    data = assert_success(response.json())
    # 0.25..0.5 of the 200-wide content box, offset back to the frame.
    assert data["box"] == [150, 100, 200, 200]
    assert data["content_box"] == [100, 0, 300, 200]
    assert data["letterboxed"] is True
    assert corners_are_region(decode(data["image_data"]))


async def test_extract_follows_the_game_when_the_window_is_resized(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    """The bug this exists for: one canvas size, two window shapes, one region.

    Both frames are 1280x720 because OBS's canvas never changes; only the part
    of it the game fills does. The same four fractions have to land on the same
    part of the game in both.
    """
    write_frame(
        screenshots / "narrow.png",
        size=(1280, 720),
        region=QUARTER,
        content=NARROW_CONTENT,
    )
    response = await client.post(f"{API}/extract", json={"region": "quarter"})
    narrow = assert_success(response.json())

    wide = write_frame(
        screenshots / "wide.png",
        size=(1280, 720),
        region=QUARTER,
        content=WIDE_CONTENT,
    )
    _make_newest(wide)
    response = await client.post(f"{API}/extract", json={"region": "quarter"})
    widened = assert_success(response.json())

    assert narrow["content_box"] == list(NARROW_CONTENT)
    assert widened["content_box"] == list(WIDE_CONTENT)
    # Different pixels, because the game moved and grew...
    assert narrow["box"] != widened["box"]
    assert widened["width"] > narrow["width"]
    # ...and the same part of the game in both, which is the whole point.
    assert corners_are_region(decode(narrow["image_data"]))
    assert corners_are_region(decode(widened["image_data"]))


async def test_extract_reports_an_unletterboxed_frame_as_the_whole_frame(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    write_frame(screenshots / "shot.png", size=(400, 200), region=QUARTER)

    response = await client.post(f"{API}/extract", json={"region": "quarter"})

    data = assert_success(response.json())
    assert data["content_box"] == [0, 0, 400, 200]
    assert data["letterboxed"] is False
    # And the region resolved exactly as fractions of the canvas.
    assert data["box"] == [100, 100, 200, 200]


async def test_extract_ignores_the_letterbox_when_trimming_is_off(
    client: AsyncClient,
    active_game: Path,
    screenshots: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``FRAME_LETTERBOX_TRIM=false`` puts every region back on the canvas."""
    monkeypatch.setattr(settings, "FRAME_LETTERBOX_TRIM", False)
    write_frame(
        screenshots / "shot.png",
        size=(400, 200),
        region=QUARTER,
        content=(100, 0, 300, 200),
    )

    response = await client.post(f"{API}/extract", json={"region": "quarter"})

    data = assert_success(response.json())
    assert data["content_box"] == [0, 0, 400, 200]
    assert data["letterboxed"] is False
    assert data["box"] == [100, 100, 200, 200]


async def test_extract_uses_the_whole_frame_of_a_black_shot(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    """A shot caught between scenes is a black frame, not a zero-size window."""
    path = screenshots / "black.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 200), BARS).save(path)

    response = await client.post(f"{API}/extract", json={"region": "quarter"})

    data = assert_success(response.json())
    assert data["content_box"] == [0, 0, 400, 200]
    assert data["box"] == [100, 100, 200, 200]


async def test_extract_can_name_an_older_frame(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    """Naming a frame is how a region is checked against a shot from before."""
    write_frame(screenshots / "older.png", size=(400, 200), region=QUARTER)
    newest = write_frame(screenshots / "newest.png", size=(800, 400), region=QUARTER)
    _make_newest(newest)

    response = await client.post(
        f"{API}/extract", json={"region": "quarter", "file_name": "older.png"}
    )

    data = assert_success(response.json())
    assert data["source"]["file_name"] == "older.png"
    assert (data["source"]["width"], data["source"]["height"]) == (400, 200)


async def test_extract_404s_when_no_screenshot_has_been_taken(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    response = await client.post(f"{API}/extract", json={"region": "quarter"})

    assert response.status_code == 404
    payload = response.json()
    assert_failure(payload, code="ROI_FRAME_NOT_FOUND")
    # The message has to say where they are written, or there is nothing to do.
    assert "screenshots" in payload["message"].lower()


async def test_extract_404s_on_an_unknown_region(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/extract", json={"region": "nope"})

    assert response.status_code == 404
    payload = response.json()
    assert_failure(payload, code="ROI_REGION_NOT_FOUND")
    # Naming what *is* configured is what turns a 404 into a fixable one.
    assert "cash_meter" in payload["message"]


async def test_extract_404s_on_a_frame_that_is_not_there(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    write_frame(screenshots / "shot.png")

    response = await client.post(
        f"{API}/extract", json={"region": "quarter", "file_name": "missing.png"}
    )

    assert response.status_code == 404
    assert_failure(response.json(), code="ROI_FRAME_NOT_FOUND")


@pytest.mark.parametrize(
    "file_name",
    ["../escape.png", "sub/escape.png", "..\\escape.png", "C:/escape.png", ".."],
)
async def test_extract_rejects_names_that_escape_the_screenshot_dir(
    client: AsyncClient, active_game: Path, screenshots: Path, file_name: str
) -> None:
    """The endpoint must not become an arbitrary-file-read primitive."""
    write_frame(screenshots / "shot.png")

    response = await client.post(
        f"{API}/extract", json={"region": "quarter", "file_name": file_name}
    )

    assert response.status_code == 400
    assert_failure(response.json(), code="BAD_REQUEST")


async def test_extract_502s_on_a_frame_that_is_not_an_image(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    """A .png that is not one -- what a shot caught mid-write looks like."""
    (screenshots / "truncated.png").write_bytes(b"not an image")

    response = await client.post(f"{API}/extract", json={"region": "quarter"})

    assert response.status_code == 502
    assert_failure(response.json(), code="ROI_EXTRACT_FAILED")


async def test_extract_500s_on_a_region_declared_with_bad_numbers(
    client: AsyncClient,
    tmp_path: Path,
    screenshots: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The config is wrong, not the request -- so not a 4xx."""
    directory = tmp_path / "games"
    config = write_game_config(
        directory, {"name": "Broken", "roi": {"bad": [0.5, 0.1, 0.2, 0.9]}}
    )
    selection = tmp_path / "active_game.json"
    save_active_game(selection, "Broken")
    monkeypatch.setattr(
        type(settings), "ideck_game_config_dir", property(lambda _self: directory)
    )
    monkeypatch.setattr(
        type(settings), "ideck_active_game_path", property(lambda _self: selection)
    )
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/extract", json={"region": "bad"})

    assert response.status_code == 500
    payload = response.json()
    assert_failure(payload, code="GAME_CONFIG_INVALID")
    # Names the file, so the fix does not start with finding it.
    assert config.name in payload["message"]


async def test_extract_saves_the_cash_meter_crop_to_disk(
    client: AsyncClient,
    tmp_path: Path,
    active_game: Path,
    screenshots: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cash meter is the one region also kept on disk, run over run."""
    capture_dir = tmp_path / "capture"
    monkeypatch.setattr(
        type(settings), "obs_capture_dir", property(lambda _self: capture_dir)
    )
    write_frame(screenshots / "shot.png", size=(400, 200), region=CASH_METER)

    response = await client.post(f"{API}/extract", json={"region": "cash_meter"})

    assert response.status_code == 200
    saved = capture_dir / "cash-meter" / "shot.png"
    assert saved.is_file()
    saved_image = Image.open(saved)
    saved_image.load()
    data = assert_success(response.json())
    assert saved_image.size == (data["width"], data["height"])


async def test_extract_does_not_save_other_regions_to_disk(
    client: AsyncClient,
    tmp_path: Path,
    active_game: Path,
    screenshots: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture_dir = tmp_path / "capture"
    monkeypatch.setattr(
        type(settings), "obs_capture_dir", property(lambda _self: capture_dir)
    )
    write_frame(screenshots / "shot.png", size=(400, 200), region=QUARTER)

    response = await client.post(f"{API}/extract", json={"region": "quarter"})

    assert response.status_code == 200
    assert not (capture_dir / "cash-meter").exists()


async def test_extract_rejects_an_empty_region_name(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    response = await client.post(f"{API}/extract", json={"region": ""})

    assert response.status_code == 422
    assert_failure(response.json(), code="VALIDATION_ERROR")


async def test_extract_rejects_unknown_fields(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    """``extra='forbid'``, so a misspelled key is not silently ignored."""
    response = await client.post(
        f"{API}/extract", json={"region": "quarter", "output_dir": "elsewhere"}
    )

    assert response.status_code == 422
    assert_failure(response.json(), code="VALIDATION_ERROR")


def _make_newest(path: Path) -> None:
    """Push one file's mtime clear of its siblings.

    Frames written in the same test can land in the same filesystem timestamp
    tick, which would make "the newest" a coin flip.
    """
    newest = max(sibling.stat().st_mtime for sibling in path.parent.iterdir())
    os.utime(path, (newest + 10, newest + 10))
