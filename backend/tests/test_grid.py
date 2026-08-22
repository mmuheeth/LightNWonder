"""Splitting the reels of a captured frame into a matrix of tiles.

Everything here runs off real image files in ``tmp_path``, like
``tests/test_roi.py``: the frames are small Pillow images written to disk, and
both the screenshots directory and the capture directory are pointed at
temporary ones, so no test depends on what the developer's own
``obs-captured-files`` happens to contain.

**Every tile is painted its own colour, and that is the point of these tests.**
A splitter that transposes the matrix, or that resolves the reel bounds against
the frame instead of against the crop, still produces fifteen correctly-sized
tiles with the right names. The only thing that catches it is checking that the
picture inside ``r2c3`` is the picture that was painted at row 2, column 3 --
so the frame is built with a distinct colour per position and every assertion
about a tile is an assertion about its pixels.
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
from app.core.config import settings
from tests.asserts import assert_failure, assert_success

API = "/api/grid"

# A 400x400 frame whose reels region is the bottom-middle 200x300 block. Round
# fractions throughout, so five 40px columns and three 100px rows divide it
# exactly and no assertion needs a rounding allowance.
FRAME_SIZE = (400, 400)
REELS = [0.25, 0.25, 0.75, 1.0]
REELS_BOX = (100, 100, 300, 400)

REEL_BOUNDS = {"rows": 3, "columns": 5}

BACKGROUND = (10, 20, 30)
# Bars are pure black, the way OBS pads a canvas round a source. The background
# above is dim but well clear of the detection threshold, so a frame with no
# bars is all content.
BARS = (0, 0, 0)

# The two real captures of one FortuneOx window, resized: a 632x1080 simulator
# lands as a 421-wide strip of the 1280x720 canvas, and widening it lands as 643.
NARROW_CONTENT = (429, 0, 850, 720)
WIDE_CONTENT = (318, 0, 961, 720)
# A ring painted inside every tile, standing in for the frame a game draws round
# a reel when it highlights a win. The inset's whole job is to exclude it.
BORDER = (250, 240, 5)
BORDER_PIXELS = 2


def tile_colour(row: int, column: int) -> tuple[int, int, int]:
    """A colour no other tile has, from the 1-indexed matrix position.

    Encoding the position *into* the colour is what lets a test say "this tile
    holds what was painted at row 2, column 3" rather than only "this tile is
    not the background".
    """
    return (row * 60, column * 40, 200)


def write_game_config(directory: Path, document: dict[str, Any]) -> Path:
    """Write one game config into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{document['name']}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def write_frame(
    path: Path,
    *,
    size: tuple[int, int] = FRAME_SIZE,
    reels: list[float] | None = REELS,
    rows: int = 3,
    columns: int = 5,
    border: bool = False,
    content: tuple[int, int, int, int] | None = None,
) -> Path:
    """Write a frame whose reels region is painted one colour per tile.

    With ``border`` set, each tile also gets a ring of :data:`BORDER` inside its
    own edges -- so a test can tell a tile that was trimmed from one that merely
    came back smaller.

    With ``content`` given, the frame is black outside that box and the reels are
    painted relative to it -- a window capture letterboxed inside a canvas, which
    is what every real frame from OBS is.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, BARS if content is not None else BACKGROUND)
    if content is not None:
        image.paste(
            Image.new(
                "RGB",
                (content[2] - content[0], content[3] - content[1]),
                BACKGROUND,
            ),
            (content[0], content[1]),
        )
    if reels is not None:
        origin_x, origin_y, edge_x, edge_y = (
            content if content is not None else (0, 0, *size)
        )
        width, height = edge_x - origin_x, edge_y - origin_y
        left = origin_x + round(reels[0] * width)
        top = origin_y + round(reels[1] * height)
        right = origin_x + round(reels[2] * width)
        bottom = origin_y + round(reels[3] * height)
        crop_width, crop_height = right - left, bottom - top
        for row in range(1, rows + 1):
            for column in range(1, columns + 1):
                # Divided the same way the splitter will divide it, so the
                # blocks line up with the tiles by construction.
                box = (
                    left + round((column - 1) / columns * crop_width),
                    top + round((row - 1) / rows * crop_height),
                    left + round(column / columns * crop_width),
                    top + round(row / rows * crop_height),
                )
                image.paste(
                    Image.new(
                        "RGB",
                        (box[2] - box[0], box[3] - box[1]),
                        tile_colour(row, column),
                    ),
                    box,
                )
                if border:
                    edge = BORDER_PIXELS
                    image.paste(
                        Image.new("RGB", (box[2] - box[0], box[3] - box[1]), BORDER),
                        box,
                    )
                    inner = (
                        box[0] + edge,
                        box[1] + edge,
                        box[2] - edge,
                        box[3] - edge,
                    )
                    image.paste(
                        Image.new(
                            "RGB",
                            (inner[2] - inner[0], inner[3] - inner[1]),
                            tile_colour(row, column),
                        ),
                        inner,
                    )
    image.save(path)
    return path


def decode(image_data: str) -> Image.Image:
    """Open a tile the API returned as a data URI."""
    assert image_data.startswith("data:image/png;base64,")
    raw = base64.b64decode(image_data.split(",", 1)[1])
    image = Image.open(io.BytesIO(raw))
    image.load()
    return image


def has_border(image: Image.Image) -> bool:
    """Whether any of :data:`BORDER` survived into this tile.

    Pixel by pixel rather than ``getdata()``, which Pillow deprecates -- and
    ``pytest.ini`` turns a DeprecationWarning into a failure.
    """
    rgb = image.convert("RGB")
    pixels = rgb.load()
    return any(
        pixels[x, y] == BORDER for y in range(rgb.height) for x in range(rgb.width)
    )


def centre(image: Image.Image) -> tuple[int, ...]:
    """The middle pixel, which is clear of any rounding at the tile's edges."""
    return image.convert("RGB").getpixel((image.width // 2, image.height // 2))


@pytest.fixture
def screenshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the screenshots directory at a temporary one."""
    directory = tmp_path / "screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        type(settings),
        "obs_dashboard_screenshot_dir",
        property(lambda _self: directory),
    )
    return directory


@pytest.fixture
def captures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the capture directory, which is where a split is written."""
    directory = tmp_path / "capture"
    monkeypatch.setattr(
        type(settings), "obs_capture_dir", property(lambda _self: directory)
    )
    return directory


@pytest.fixture
def game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Make one game config active, and return the factory that wrote it."""

    def activate(document: dict[str, Any]) -> Path:
        directory = tmp_path / "games"
        path = write_game_config(directory, document)
        selection = tmp_path / "active_game.json"
        save_active_game(selection, document["name"])
        monkeypatch.setattr(
            type(settings), "ideck_game_config_dir", property(lambda _self: directory)
        )
        monkeypatch.setattr(
            type(settings), "ideck_active_game_path", property(lambda _self: selection)
        )
        return path

    return activate


@pytest.fixture
def active_game(game) -> Path:
    """A game declaring a reels region and the bounds to divide it by."""
    return game(
        {
            "name": "FortuneOx",
            "process": "FortuneOx.exe",
            "roi": {"cash_meter": [0.1, 0.9, 0.9, 0.95], "reels": REELS},
            "reel_bounds": REEL_BOUNDS,
        }
    )


# --- the layout -----------------------------------------------------------


async def test_layout_reports_the_shape_of_the_grid(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    response = await client.get(f"{API}/layout")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["game"] == "FortuneOx"
    assert data["region"] == "reels"
    assert data["roi"] == REELS
    assert (data["rows"], data["columns"]) == (3, 5)
    assert data["error"] is None


async def test_layout_lays_the_tile_names_out_as_a_matrix(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    """1-indexed and row-major, so the panel needs no chunking of its own."""
    response = await client.get(f"{API}/layout")

    assert assert_success(response.json())["positions"] == [
        ["r1c1", "r1c2", "r1c3", "r1c4", "r1c5"],
        ["r2c1", "r2c2", "r2c3", "r2c4", "r2c5"],
        ["r3c1", "r3c2", "r3c3", "r3c4", "r3c5"],
    ]


async def test_layout_names_the_frame_a_split_would_use(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    write_frame(screenshots / "older.png")
    newer = write_frame(screenshots / "newer.png", size=(800, 800))
    _make_newest(newer)

    response = await client.get(f"{API}/layout")

    frame = assert_success(response.json())["latest_frame"]
    assert frame["file_name"] == "newer.png"
    assert (frame["width"], frame["height"]) == (800, 800)


async def test_layout_reports_no_frame_before_a_screenshot_is_taken(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    response = await client.get(f"{API}/layout")

    assert assert_success(response.json())["latest_frame"] is None


async def test_layout_reports_a_game_without_reels_as_a_state(
    client: AsyncClient, game, screenshots: Path
) -> None:
    """Half the shipped games have no reels; choosing one is not a failure."""
    game({"name": "HuffNPuffLink", "roi": {"cash_meter": [0.1, 0.9, 0.9, 0.95]}})

    response = await client.get(f"{API}/layout")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert "roi.reels" in data["error"]
    # And the message names what the game does declare, so the next step is clear.
    assert "cash_meter" in data["error"]
    assert data["rows"] == 0
    assert data["positions"] == []
    assert data["roi"] is None


async def test_layout_reports_a_game_without_bounds_as_a_state(
    client: AsyncClient, game, screenshots: Path
) -> None:
    game({"name": "Bare", "roi": {"reels": REELS}})

    response = await client.get(f"{API}/layout")

    assert response.status_code == 200
    assert "reel_bounds" in assert_success(response.json())["error"]


async def test_layout_reports_unusable_bounds_as_a_state(
    client: AsyncClient, game, screenshots: Path
) -> None:
    """A typo in the numbers is visible in the panel, not only on a split."""
    config = game(
        {
            "name": "Broken",
            "roi": {"reels": REELS},
            "reel_bounds": {"rows": 0, "columns": 5},
        }
    )

    response = await client.get(f"{API}/layout")

    assert response.status_code == 200
    error = assert_success(response.json())["error"]
    assert "must be at least 1" in error
    # Names the file, so the fix does not start with finding it.
    assert config.name in error


# --- splitting ------------------------------------------------------------


async def test_split_returns_every_tile_from_the_right_position(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    """The whole feature: fifteen tiles, each holding what was at its position."""
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={})

    assert response.status_code == 200
    data = assert_success(response.json())
    assert (data["rows"], data["columns"]) == (3, 5)
    assert len(data["tiles"]) == 15

    for tile in data["tiles"]:
        assert centre(decode(tile["image_data"])) == tile_colour(
            tile["row"], tile["column"]
        )


async def test_split_crops_the_reels_region_off_the_newest_frame(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={})

    data = assert_success(response.json())
    assert data["source"]["file_name"] == "shot.png"
    assert data["roi"] == REELS
    assert data["box"] == list(REELS_BOX)
    assert (data["width"], data["height"]) == (200, 300)
    assert data["region"] == "reels"


async def test_split_reads_the_tile_bounds_against_the_crop_not_the_frame(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    """The one mistake that still produces plausible-looking tiles.

    Resolved against the 400px frame a 0.0..0.2 column would be 80px wide;
    against the 200px crop it is 40. Both divide into five, and only one is the
    reels.
    """
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={})

    tiles = {tile["name"]: tile for tile in assert_success(response.json())["tiles"]}
    assert tiles["r1c1"]["box"] == [0, 0, 40, 100]
    assert (tiles["r1c1"]["width"], tiles["r1c1"]["height"]) == (40, 100)
    assert tiles["r3c5"]["box"] == [160, 200, 200, 300]


async def test_split_names_tiles_one_indexed(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={})

    data = assert_success(response.json())
    assert data["tiles"][0]["name"] == "r1c1"
    assert data["tiles"][0]["file_name"] == "r1c1.png"
    assert data["tiles"][-1]["name"] == "r3c5"
    # Row-major, so the second tile is the next reel and not the next row.
    assert data["tiles"][1]["name"] == "r1c2"


async def test_split_writes_the_crop_and_the_tiles_under_the_frames_own_folder(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={})

    data = assert_success(response.json())
    directory = captures / "grid" / "shot"
    assert Path(data["output_dir"]) == directory
    assert (directory / "reels.png").is_file()
    assert data["crop_file"] == "reels.png"

    tiles = directory / "tiles"
    written = sorted(path.name for path in tiles.iterdir())
    assert written == sorted(
        f"r{row}c{column}.png" for row in (1, 2, 3) for column in range(1, 6)
    )


async def test_the_written_tiles_hold_the_same_pictures_as_the_response(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    """The files are the deliverable, so they get the same assertion as the API."""
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={})

    data = assert_success(response.json())
    tiles = Path(data["output_dir"]) / "tiles"
    for tile in data["tiles"]:
        with Image.open(tiles / tile["file_name"]) as image:
            image.load()
            assert image.size == (tile["width"], tile["height"])
            assert centre(image) == tile_colour(tile["row"], tile["column"])


async def test_the_written_crop_is_the_reels_region(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={})

    data = assert_success(response.json())
    with Image.open(Path(data["output_dir"]) / data["crop_file"]) as crop:
        crop.load()
        assert crop.size == (data["width"], data["height"])
        # Its top-left corner is r1c1's colour, not the frame's background.
        assert crop.convert("RGB").getpixel((5, 5)) == tile_colour(1, 1)


async def test_split_returns_the_positions_as_a_matrix(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={})

    data = assert_success(response.json())
    assert data["positions"] == [
        ["r1c1", "r1c2", "r1c3", "r1c4", "r1c5"],
        ["r2c1", "r2c2", "r2c3", "r2c4", "r2c5"],
        ["r3c1", "r3c2", "r3c3", "r3c4", "r3c5"],
    ]
    # The same names the tiles carry, in the same order.
    assert [name for row in data["positions"] for name in row] == [
        tile["name"] for tile in data["tiles"]
    ]


async def test_split_can_name_an_older_frame(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    write_frame(screenshots / "older.png")
    newest = write_frame(screenshots / "newest.png", size=(800, 800))
    _make_newest(newest)

    response = await client.post(f"{API}/split", json={"file_name": "older.png"})

    data = assert_success(response.json())
    assert data["source"]["file_name"] == "older.png"
    # Written under the frame it came from, so two frames leave two records.
    assert Path(data["output_dir"]).name == "older"


async def test_the_same_fractions_scale_to_any_frame_size(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    """The whole point of fractions: no re-measurement at a new capture size."""
    write_frame(screenshots / "small.png", size=(400, 400))
    small = assert_success((await client.post(f"{API}/split", json={})).json())

    big = write_frame(screenshots / "big.png", size=(1600, 1600))
    _make_newest(big)
    large = assert_success((await client.post(f"{API}/split", json={})).json())

    assert (large["width"], large["height"]) == (
        small["width"] * 4,
        small["height"] * 4,
    )
    # Four times the frame, four times the tile, same symbol in it.
    for one, four in zip(small["tiles"], large["tiles"], strict=True):
        assert four["width"] == one["width"] * 4
        assert centre(decode(four["image_data"])) == centre(decode(one["image_data"]))


async def test_the_reels_are_found_inside_the_game_not_inside_the_canvas(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    """``roi.reels`` is fractions of the picture, not of the black round it."""
    write_frame(screenshots / "shot.png", size=(1280, 720), content=NARROW_CONTENT)

    data = assert_success((await client.post(f"{API}/split", json={})).json())

    assert data["content_box"] == list(NARROW_CONTENT)
    assert data["letterboxed"] is True
    # Inside the content box, not the canvas: 0.25..0.75 of 421 offset by 429.
    assert data["box"] == [534, 180, 745, 720]
    # And every tile still holds the symbol it was painted with.
    for tile in data["tiles"]:
        assert centre(decode(tile["image_data"])) == tile_colour(
            tile["row"], tile["column"]
        )


async def test_the_same_bounds_follow_the_game_when_the_window_is_resized(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    """The bug this exists for: one canvas size, two window shapes, one config.

    Both frames are 1280x720 because OBS's canvas never changes; only the part
    of it the game fills does. Neither ``roi.reels`` nor ``reel_bounds`` may need
    a second measurement for the second one.
    """
    write_frame(screenshots / "narrow.png", size=(1280, 720), content=NARROW_CONTENT)
    narrow = assert_success((await client.post(f"{API}/split", json={})).json())

    wide = write_frame(screenshots / "wide.png", size=(1280, 720), content=WIDE_CONTENT)
    _make_newest(wide)
    widened = assert_success((await client.post(f"{API}/split", json={})).json())

    assert narrow["content_box"] == list(NARROW_CONTENT)
    assert widened["content_box"] == list(WIDE_CONTENT)
    # A different crop of a same-sized frame, because the game moved and grew...
    assert narrow["box"] != widened["box"]
    assert widened["width"] > narrow["width"]
    # ...and the matrix still reads the same, tile for tile.
    assert widened["positions"] == narrow["positions"]
    for one, other in zip(narrow["tiles"], widened["tiles"], strict=True):
        assert centre(decode(other["image_data"])) == centre(decode(one["image_data"]))


async def test_split_reports_an_unletterboxed_frame_as_the_whole_frame(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    write_frame(screenshots / "shot.png")

    data = assert_success((await client.post(f"{API}/split", json={})).json())

    assert data["content_box"] == [0, 0, *FRAME_SIZE]
    assert data["letterboxed"] is False
    assert data["box"] == list(REELS_BOX)


async def test_split_ignores_the_letterbox_when_trimming_is_off(
    client: AsyncClient,
    active_game: Path,
    screenshots: Path,
    captures: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``FRAME_LETTERBOX_TRIM=false`` puts the reels region back on the canvas."""
    monkeypatch.setattr(settings, "FRAME_LETTERBOX_TRIM", False)
    write_frame(screenshots / "shot.png", size=(1280, 720), content=NARROW_CONTENT)

    data = assert_success((await client.post(f"{API}/split", json={})).json())

    assert data["content_box"] == [0, 0, 1280, 720]
    assert data["letterboxed"] is False
    assert data["box"] == [320, 180, 960, 720]


async def test_splitting_the_same_frame_twice_replaces_its_own_record(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    """Idempotent, which is what makes it safe to re-run after editing bounds."""
    write_frame(screenshots / "shot.png")

    first = assert_success((await client.post(f"{API}/split", json={})).json())
    second = assert_success((await client.post(f"{API}/split", json={})).json())

    assert first["output_dir"] == second["output_dir"]
    assert len(list((captures / "grid").iterdir())) == 1
    assert len(list((captures / "grid" / "shot" / "tiles").iterdir())) == 15


async def test_a_stale_tile_from_a_wider_grid_is_cleared(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    """Otherwise the old r1c5 sits there looking like part of the new answer."""
    write_frame(screenshots / "shot.png")
    stale = captures / "grid" / "shot" / "tiles"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "r1c9.png").write_bytes(b"old")
    (stale / "notes.txt").write_bytes(b"mine")

    await client.post(f"{API}/split", json={})

    assert not (stale / "r1c9.png").exists()
    # Only tiles are cleared, so anything else in the folder survives.
    assert (stale / "notes.txt").is_file()


async def test_include_images_false_still_writes_the_files(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    """The files are the deliverable; the data URIs are only convenience."""
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={"include_images": False})

    data = assert_success(response.json())
    assert data["crop_image"] is None
    assert all(tile["image_data"] is None for tile in data["tiles"])
    assert (captures / "grid" / "shot" / "tiles" / "r2c3.png").is_file()


# --- failures -------------------------------------------------------------


async def test_split_404s_when_the_game_declares_no_reels(
    client: AsyncClient, game, screenshots: Path, captures: Path
) -> None:
    game({"name": "HuffNPuffLink", "roi": {"cash_meter": [0.1, 0.9, 0.9, 0.95]}})
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={})

    assert response.status_code == 404
    payload = response.json()
    assert_failure(payload, code="GRID_NOT_CONFIGURED")
    assert "roi.reels" in payload["message"]


async def test_split_404s_when_the_game_declares_no_bounds(
    client: AsyncClient, game, screenshots: Path, captures: Path
) -> None:
    game({"name": "Bare", "roi": {"reels": REELS}})
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={})

    assert response.status_code == 404
    payload = response.json()
    assert_failure(payload, code="GRID_NOT_CONFIGURED")
    assert "reel_bounds" in payload["message"]


async def test_split_404s_when_no_screenshot_has_been_taken(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    """ROI's error, because it is ROI's frame -- and its message says where."""
    response = await client.post(f"{API}/split", json={})

    assert response.status_code == 404
    payload = response.json()
    assert_failure(payload, code="ROI_FRAME_NOT_FOUND")
    assert "screenshots" in payload["message"].lower()


async def test_split_404s_on_a_frame_that_is_not_there(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={"file_name": "missing.png"})

    assert response.status_code == 404
    assert_failure(response.json(), code="ROI_FRAME_NOT_FOUND")


@pytest.mark.parametrize(
    "file_name",
    ["../escape.png", "sub/escape.png", "..\\escape.png", "C:/escape.png", ".."],
)
async def test_split_rejects_names_that_escape_the_screenshot_dir(
    client: AsyncClient,
    active_game: Path,
    screenshots: Path,
    captures: Path,
    file_name: str,
) -> None:
    """The endpoint must not become an arbitrary-file-read primitive.

    It writes a directory named after the frame's stem, so an escaping name
    would be an arbitrary-file-*write* primitive too.
    """
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={"file_name": file_name})

    assert response.status_code == 400
    assert_failure(response.json(), code="BAD_REQUEST")
    assert not (captures / "grid").exists()


async def test_split_502s_on_a_frame_that_is_not_an_image(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    """A .png that is not one -- what a shot caught mid-write looks like."""
    (screenshots / "truncated.png").write_bytes(b"not an image")

    response = await client.post(f"{API}/split", json={})

    assert response.status_code == 502
    assert_failure(response.json(), code="ROI_EXTRACT_FAILED")


async def test_split_500s_on_a_reels_region_declared_with_bad_numbers(
    client: AsyncClient, game, screenshots: Path, captures: Path
) -> None:
    """The config is wrong, not the request -- so not a 4xx."""
    config = game(
        {
            "name": "Broken",
            "roi": {"reels": [0.75, 0.25, 0.25, 1.0]},
            "reel_bounds": REEL_BOUNDS,
        }
    )
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={})

    assert response.status_code == 500
    payload = response.json()
    assert_failure(payload, code="GAME_CONFIG_INVALID")
    assert config.name in payload["message"]


async def test_split_500s_on_a_count_that_is_not_a_count(
    client: AsyncClient, game, screenshots: Path, captures: Path
) -> None:
    game(
        {
            "name": "Fractional",
            "roi": {"reels": REELS},
            "reel_bounds": {"rows": 3, "columns": 2.5},
        }
    )
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={})

    assert response.status_code == 500
    payload = response.json()
    assert_failure(payload, code="GAME_CONFIG_INVALID")
    assert "must be a whole number" in payload["message"]


async def test_split_500s_on_a_config_still_using_the_replaced_span_keys(
    client: AsyncClient, game, screenshots: Path, captures: Path
) -> None:
    """Ignoring them would split evenly anyway and look entirely correct."""
    game(
        {
            "name": "Spans",
            "roi": {"reels": REELS},
            "reel_bounds": {
                "col_bounds": [[0.0, 0.2], [0.2, 0.4]],
                "row_bounds": [[0.0, 1.0]],
            },
        }
    )
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={})

    assert response.status_code == 500
    payload = response.json()
    assert_failure(payload, code="GAME_CONFIG_INVALID")
    assert "col_bounds is no longer read" in payload["message"]


async def test_nothing_is_written_when_the_split_cannot_start(
    client: AsyncClient, game, screenshots: Path, captures: Path
) -> None:
    """The config is checked before the disk is touched."""
    game({"name": "Bare", "roi": {"reels": REELS}})
    write_frame(screenshots / "shot.png")

    await client.post(f"{API}/split", json={})

    assert not (captures / "grid").exists()


async def test_split_rejects_unknown_fields(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    """``extra='forbid'``, so a misspelled key is not silently ignored."""
    response = await client.post(f"{API}/split", json={"region": "reels"})

    assert response.status_code == 422
    assert_failure(response.json(), code="VALIDATION_ERROR")


# --- the border inset -----------------------------------------------------
# The bounds divide the crop edge to edge, so a tile takes the frame a game draws
# inside a reel to highlight a win along with the symbol. Every test here paints
# that ring and asserts on whether it survived, rather than only on tile sizes --
# a tile that came back smaller is not the same as a tile that was trimmed.


@pytest.fixture
def inset_game(game) -> Path:
    """The same grid, with a border trim big enough to clear the painted ring.

    A twentieth of a 40x100 tile is 2px across and 5px down, which is exactly
    :data:`BORDER_PIXELS` on the narrow axis -- the tightest trim that can work,
    so the test would catch an inset applied as a fraction of the crop rather
    than of the tile.
    """
    return game(
        {
            "name": "FortuneOx",
            "roi": {"reels": REELS},
            "reel_bounds": {**REEL_BOUNDS, "inset": 0.05},
        }
    )


async def test_layout_reports_no_inset_when_the_game_asks_for_none(
    client: AsyncClient, active_game: Path, screenshots: Path
) -> None:
    response = await client.get(f"{API}/layout")

    assert assert_success(response.json())["inset"] == [0.0, 0.0, 0.0, 0.0]


async def test_layout_reports_the_configured_inset(
    client: AsyncClient, inset_game: Path, screenshots: Path
) -> None:
    """The panel shows what a split will do before it does it."""
    response = await client.get(f"{API}/layout")

    assert assert_success(response.json())["inset"] == [0.05, 0.05, 0.05, 0.05]


async def test_without_an_inset_the_border_lands_in_the_tile(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    """The baseline the inset exists to fix, asserted rather than assumed."""
    write_frame(screenshots / "shot.png", border=True)

    response = await client.post(f"{API}/split", json={})

    tiles = assert_success(response.json())["tiles"]
    assert all(has_border(decode(tile["image_data"])) for tile in tiles)


async def test_the_configured_inset_trims_the_border_off_every_tile(
    client: AsyncClient, inset_game: Path, screenshots: Path, captures: Path
) -> None:
    write_frame(screenshots / "shot.png", border=True)

    response = await client.post(f"{API}/split", json={})

    data = assert_success(response.json())
    assert data["inset"] == [0.05, 0.05, 0.05, 0.05]
    for tile in data["tiles"]:
        image = decode(tile["image_data"])
        assert not has_border(image), f"{tile['name']} kept its border"
        # Still the right symbol, so the trim took the ring and not the tile.
        assert centre(image) == tile_colour(tile["row"], tile["column"])


async def test_the_inset_shrinks_the_tile_by_a_fraction_of_itself(
    client: AsyncClient, inset_game: Path, screenshots: Path, captures: Path
) -> None:
    """A twentieth of a 40x100 tile is 2px and 5px.

    Not the same fraction of the 200x300 crop, which would be 10px and 15px --
    which is the mistake this pins down.
    """
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={})

    tiles = {tile["name"]: tile for tile in assert_success(response.json())["tiles"]}
    assert tiles["r1c1"]["box"] == [2, 5, 38, 95]
    assert (tiles["r1c1"]["width"], tiles["r1c1"]["height"]) == (36, 90)


async def test_a_request_can_override_the_configured_inset(
    client: AsyncClient, inset_game: Path, screenshots: Path, captures: Path
) -> None:
    """How the right number gets found before it is written into the config."""
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={"inset": 0.1})

    data = assert_success(response.json())
    assert data["inset"] == [0.1, 0.1, 0.1, 0.1]
    assert data["tiles"][0]["box"] == [4, 10, 36, 90]


async def test_a_request_can_turn_a_configured_inset_off(
    client: AsyncClient, inset_game: Path, screenshots: Path, captures: Path
) -> None:
    """Zero has to mean zero rather than unset, or the override cannot undo."""
    write_frame(screenshots / "shot.png", border=True)

    response = await client.post(f"{API}/split", json={"inset": 0})

    data = assert_success(response.json())
    assert data["inset"] == [0.0, 0.0, 0.0, 0.0]
    assert has_border(decode(data["tiles"][0]["image_data"]))


async def test_a_two_number_inset_is_horizontal_then_vertical(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={"inset": [0.25, 0.1]})

    data = assert_success(response.json())
    assert data["inset"] == [0.25, 0.1, 0.25, 0.1]
    # A quarter off each side of a 40px tile, a tenth off each of 100px.
    assert data["tiles"][0]["box"] == [10, 10, 30, 90]


async def test_a_four_number_inset_is_left_top_right_bottom(
    client: AsyncClient, active_game: Path, screenshots: Path, captures: Path
) -> None:
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={"inset": [0.25, 0.1, 0.0, 0.0]})

    data = assert_success(response.json())
    assert data["inset"] == [0.25, 0.1, 0.0, 0.0]
    assert data["tiles"][0]["box"] == [10, 10, 40, 100]


async def test_the_written_tiles_are_trimmed_too(
    client: AsyncClient, inset_game: Path, screenshots: Path, captures: Path
) -> None:
    """The files are the deliverable, so the trim has to reach them."""
    write_frame(screenshots / "shot.png", border=True)

    response = await client.post(f"{API}/split", json={})

    data = assert_success(response.json())
    tiles = Path(data["output_dir"]) / "tiles"
    for tile in data["tiles"]:
        with Image.open(tiles / tile["file_name"]) as image:
            image.load()
            assert image.size == (tile["width"], tile["height"])
            assert not has_border(image), f"{tile['name']} kept its border on disk"


# A numeric string is not here: pydantic coerces "0.1" to 0.1 for a float field,
# the same as everywhere else in this API, so it is a valid trim and not a
# rejection worth asserting.
@pytest.mark.parametrize(
    "value", [-0.1, 1.0, 0.5, [0.1, 0.1, 0.1], [0.6, 0.0, 0.6, 0.0], "border"]
)
async def test_an_unusable_request_inset_is_refused_and_writes_nothing(
    client: AsyncClient,
    active_game: Path,
    screenshots: Path,
    captures: Path,
    value: object,
) -> None:
    """The request is wrong, not the config -- so a 4xx, and nothing on disk."""
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={"inset": value})

    assert response.status_code in (400, 422)
    assert not (captures / "grid").exists()


async def test_an_unusable_configured_inset_is_a_500(
    client: AsyncClient, game, screenshots: Path, captures: Path
) -> None:
    """The same numbers in the config are the config's fault, not the caller's."""
    config = game(
        {
            "name": "Broken",
            "roi": {"reels": REELS},
            "reel_bounds": {**REEL_BOUNDS, "inset": 0.5},
        }
    )
    write_frame(screenshots / "shot.png")

    response = await client.post(f"{API}/split", json={})

    assert response.status_code == 500
    payload = response.json()
    assert_failure(payload, code="GAME_CONFIG_INVALID")
    assert config.name in payload["message"]


async def test_layout_reports_an_unusable_inset_as_a_state(
    client: AsyncClient, game, screenshots: Path
) -> None:
    game(
        {
            "name": "Broken",
            "roi": {"reels": REELS},
            "reel_bounds": {**REEL_BOUNDS, "inset": 0.5},
        }
    )

    response = await client.get(f"{API}/layout")

    assert response.status_code == 200
    assert "trim the whole" in assert_success(response.json())["error"]


# --- one size for every tile ----------------------------------------------
# The round fractions the tests above use divide evenly, so they cannot catch an
# uneven grid. These use FortuneOx's real column bounds over a crop that divides
# into neither five nor three, which is where the pixel goes missing.

# 0.25..0.755 of 400 is 202 wide; 0.25..0.995 of 400 is 298 tall. Five reels over
# 202 round to 40, 41, 40, 41, 40 and three rows over 298 to 99, 100, 99 -- so
# neither axis divides evenly and both can catch a tile a pixel off its
# neighbours.
UNEVEN_REELS = [0.25, 0.25, 0.755, 0.995]
UNEVEN_BOUNDS = {"rows": 3, "columns": 5}


@pytest.fixture
def uneven_game(game) -> Path:
    """A game whose bounds would round to tiles of two different sizes."""
    return game(
        {
            "name": "FortuneOx",
            "roi": {"reels": UNEVEN_REELS},
            "reel_bounds": UNEVEN_BOUNDS,
        }
    )


async def test_every_tile_has_the_same_pixel_size(
    client: AsyncClient, uneven_game: Path, screenshots: Path, captures: Path
) -> None:
    """The guarantee: no tile a row or a column of pixels off any other."""
    write_frame(screenshots / "shot.png", reels=UNEVEN_REELS)

    response = await client.post(f"{API}/split", json={})

    data = assert_success(response.json())
    sizes = {(tile["width"], tile["height"]) for tile in data["tiles"]}
    assert sizes == {(40, 99)}


async def test_the_result_names_the_shared_tile_size_once(
    client: AsyncClient, uneven_game: Path, screenshots: Path, captures: Path
) -> None:
    """One number, rather than fifteen equal ones to be compared by the caller."""
    write_frame(screenshots / "shot.png", reels=UNEVEN_REELS)

    response = await client.post(f"{API}/split", json={})

    data = assert_success(response.json())
    assert (data["tile_width"], data["tile_height"]) == (40, 99)
    assert all(
        (tile["width"], tile["height"]) == (data["tile_width"], data["tile_height"])
        for tile in data["tiles"]
    )


async def test_the_written_tiles_are_all_the_same_size(
    client: AsyncClient, uneven_game: Path, screenshots: Path, captures: Path
) -> None:
    """The files are the deliverable, so the guarantee has to reach the disk."""
    write_frame(screenshots / "shot.png", reels=UNEVEN_REELS)

    response = await client.post(f"{API}/split", json={})

    data = assert_success(response.json())
    tiles = Path(data["output_dir"]) / "tiles"
    written = set()
    for tile in data["tiles"]:
        with Image.open(tiles / tile["file_name"]) as image:
            image.load()
            written.add(image.size)
    assert written == {(40, 99)}


async def test_uniform_tiles_keep_their_own_positions(
    client: AsyncClient, uneven_game: Path, screenshots: Path, captures: Path
) -> None:
    """Sharing a size must not become sharing a pitch, which would drift."""
    write_frame(screenshots / "shot.png", reels=UNEVEN_REELS)

    response = await client.post(f"{API}/split", json={})

    tiles = {
        tile["name"]: tile["box"] for tile in assert_success(response.json())["tiles"]
    }
    # Reel 3 and row 3 sit at their own rounded edges, not at two times the
    # width and height of the first tile.
    assert tiles["r1c1"] == [0, 0, 40, 99]
    assert tiles["r1c3"] == [81, 0, 121, 99]
    assert tiles["r3c5"] == [162, 199, 202, 298]


async def test_an_inset_does_not_make_the_tiles_uneven_again(
    client: AsyncClient, game, screenshots: Path, captures: Path
) -> None:
    """The trim shrinks fractions, so it is a second chance to round unevenly."""
    game(
        {
            "name": "FortuneOx",
            "roi": {"reels": UNEVEN_REELS},
            "reel_bounds": {**UNEVEN_BOUNDS, "inset": 0.04},
        }
    )
    write_frame(screenshots / "shot.png", reels=UNEVEN_REELS)

    response = await client.post(f"{API}/split", json={})

    data = assert_success(response.json())
    assert len({(tile["width"], tile["height"]) for tile in data["tiles"]}) == 1


async def test_tiles_stay_uniform_at_a_bigger_capture_size(
    client: AsyncClient, uneven_game: Path, screenshots: Path, captures: Path
) -> None:
    """A new capture resolution must not bring the odd pixel back."""
    big = write_frame(screenshots / "big.png", size=(1600, 1600), reels=UNEVEN_REELS)
    _make_newest(big)

    response = await client.post(f"{API}/split", json={})

    data = assert_success(response.json())
    assert len({(tile["width"], tile["height"]) for tile in data["tiles"]}) == 1
    assert data["tile_width"] > 40


def _make_newest(path: Path) -> None:
    """Push one file's mtime clear of its siblings.

    Frames written in the same test can land in the same filesystem timestamp
    tick, which would make "the newest" a coin flip.
    """
    newest = max(sibling.stat().st_mtime for sibling in path.parent.iterdir())
    os.utime(path, (newest + 10, newest + 10))
