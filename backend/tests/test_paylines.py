"""Checking a split reel grid against the patterns that pay.

Everything here runs off real image files in ``tmp_path``, like
``tests/test_grid.py``: a split is written by hand into a temporary capture
directory, so no test depends on what the developer's own
``obs-captured-files/grid`` happens to hold.

**Every tile is painted the colour of the symbol it is meant to hold, and the
colours are near-orthogonal on purpose.** Two flat pictures of the same symbol
score exactly 1.0 whatever their brightness, and two of different symbols score
about 0.1, so a threshold anywhere in the middle makes every assertion here
about the *rule* being applied rather than about where the cut happens to fall.
A test that had to allow for a fuzzy score could not tell "the run stopped
because the symbols differ" from "the run stopped because the threshold moved".

**The rule under test is the left-to-right short circuit.** A line pays the
length of its *leading* run, so three matching reels at positions 3, 4 and 5 pay
nothing at all when reels 1 and 2 differ -- which is the one thing about this
feature that is easy to implement as "count the matches" and be wrong about.
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
from app.services import paylines as paylines_service
from app.utils import paylines as payline_config
from tests.asserts import assert_failure, assert_success

API = "/api/paylines"

# A 200x300 reels crop of five 40px reels and three 100px rows, so the bounds
# divide it exactly and no assertion needs a rounding allowance.
CROP_SIZE = (200, 300)
TILE_SIZE = (40, 100)
GRID_ROWS, GRID_COLUMNS = 3, 5
REEL_BOUNDS = {"rows": GRID_ROWS, "columns": GRID_COLUMNS}
REELS = [0.25, 0.25, 0.75, 1.0]

# Near-orthogonal, so two different symbols score about 0.1 and two of the same
# score exactly 1.0 -- see the module docstring.
SYMBOLS = {
    "A": (200, 10, 10),
    "B": (10, 200, 10),
    "C": (10, 10, 200),
    # A dimmer A: the same direction, so cosine calls it the same symbol. This is
    # the glow a game animates a symbol with, and the reason the measure is
    # cosine rather than a difference.
    "a": (100, 5, 5),
}

# The five-line set of a real cabinet: the three straight rows, then the two
# diagonals.
FIVE_LINES = {
    "1": [[2, 1], [2, 2], [2, 3], [2, 4], [2, 5]],
    "2": [[1, 1], [1, 2], [1, 3], [1, 4], [1, 5]],
    "3": [[3, 1], [3, 2], [3, 3], [3, 4], [3, 5]],
    "4": [[1, 1], [2, 2], [3, 3], [2, 4], [1, 5]],
    "5": [[3, 1], [2, 2], [1, 3], [2, 4], [3, 5]],
}
PAYLINES = {"5": FIVE_LINES, "20": {"1": [[2, 1], [2, 2], [2, 3], [2, 4], [2, 5]]}}

# Threshold every test uses unless it is testing the threshold: far above two
# different symbols and far below two of the same one.
CUT = 0.9


def write_game_config(directory: Path, document: dict[str, Any]) -> Path:
    """Write one game config into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{document['name']}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def write_split(root: Path, name: str, grid: list[str]) -> Path:
    """Write a split whose tiles hold the symbols of ``grid``.

    ``grid`` is one string per row, one character per reel, keyed into
    :data:`SYMBOLS` -- so a whole board reads as three lines in the test that
    uses it and a transposition is visible rather than arithmetic.
    """
    directory = root / "grid" / name
    tiles = directory / "tiles"
    tiles.mkdir(parents=True, exist_ok=True)

    crop = Image.new("RGB", CROP_SIZE, (0, 0, 0))
    for row, symbols in enumerate(grid, start=1):
        for column, symbol in enumerate(symbols, start=1):
            colour = SYMBOLS[symbol]
            tile = Image.new("RGB", TILE_SIZE, colour)
            tile.save(tiles / f"r{row}c{column}.png")
            crop.paste(
                tile,
                (
                    round((column - 1) / GRID_COLUMNS * CROP_SIZE[0]),
                    round((row - 1) / GRID_ROWS * CROP_SIZE[1]),
                ),
            )
    crop.save(directory / "reels.png")
    return directory


def decode(image_data: str) -> Image.Image:
    """Open a picture the API returned as a data URI."""
    assert image_data.startswith("data:image/png;base64,")
    image = Image.open(io.BytesIO(base64.b64decode(image_data.split(",", 1)[1])))
    image.load()
    return image


def line(data: dict[str, Any], name: str) -> dict[str, Any]:
    """One line out of a result, by its own number."""
    return next(entry for entry in data["lines"] if entry["name"] == name)


@pytest.fixture
def captures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the capture directory, which is where splits live."""
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
    """A game declaring reels, the bounds to divide them by, and its paylines."""
    return game(
        {
            "name": "FortuneOx",
            "process": "FortuneOx.exe",
            "roi": {"reels": REELS},
            "reel_bounds": REEL_BOUNDS,
            "paylines": PAYLINES,
        }
    )


@pytest.fixture(autouse=True)
def _default_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the shipped default, so a change to it does not move these tests."""
    monkeypatch.setattr(settings, "PAYLINE_MATCH_THRESHOLD", CUT)
    monkeypatch.setattr(settings, "PAYLINE_DEFAULT_SET", "")


# --- the layout -----------------------------------------------------------


async def test_layout_reports_the_sets_and_the_split_a_check_would_use(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-1", ["ABC AB".replace(" ", ""), "AAAAA", "CBABC"])

    response = await client.get(f"{API}/layout")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["game"] == "FortuneOx"
    assert [entry["name"] for entry in data["sets"]] == ["5", "20"]
    assert data["default_set"] == "5"
    assert data["threshold"] == CUT
    assert (data["rows"], data["columns"]) == (3, 5)
    assert data["latest_split"]["split"] == "screenshot-1"
    assert data["latest_split"]["tile_width"] == TILE_SIZE[0]
    assert data["error"] is None


async def test_layout_uses_the_configured_default_set(
    client: AsyncClient,
    active_game: Path,
    captures: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "PAYLINE_DEFAULT_SET", "20")
    write_split(captures, "screenshot-1", ["AAAAA", "AAAAA", "AAAAA"])

    data = assert_success((await client.get(f"{API}/layout")).json())

    assert data["default_set"] == "20"


async def test_layout_falls_back_when_the_game_lacks_the_configured_set(
    client: AsyncClient,
    active_game: Path,
    captures: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The setting is deployment-wide; the games are not all the same shape."""
    monkeypatch.setattr(settings, "PAYLINE_DEFAULT_SET", "40")
    write_split(captures, "screenshot-1", ["AAAAA", "AAAAA", "AAAAA"])

    data = assert_success((await client.get(f"{API}/layout")).json())

    assert data["default_set"] == "5"


async def test_layout_reports_a_game_with_no_paylines_as_a_state(
    client: AsyncClient, game, captures: Path
) -> None:
    """A 200 with `error`, not a 404: only some games declare the block."""
    game(
        {
            "name": "HuffNPuffLink",
            "roi": {"reels": REELS},
            "reel_bounds": REEL_BOUNDS,
        }
    )

    response = await client.get(f"{API}/layout")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["sets"] == []
    assert "no 'paylines' block" in data["error"]


async def test_layout_reports_nothing_split_yet_as_a_state(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    response = await client.get(f"{API}/layout")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["latest_split"] is None
    assert "has been split yet" in data["error"]
    # The sets are still offered: what is missing is the picture, not the config.
    assert [entry["name"] for entry in data["sets"]] == ["5", "20"]


# --- the rule -------------------------------------------------------------


async def test_a_whole_row_of_one_symbol_pays_five(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-1", ["BBBBB", "AAAAA", "CCCCC"])

    data = assert_success((await client.post(f"{API}/check", json={})).json())

    assert line(data, "1")["pays"] == 5
    assert line(data, "1")["matched_positions"] == [
        "r2c1",
        "r2c2",
        "r2c3",
        "r2c4",
        "r2c5",
    ]
    # A line that pays end to end has nowhere it broke.
    assert line(data, "1")["break_position"] is None


async def test_a_run_stops_at_the_first_reel_that_differs(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-1", ["BBBBB", "AABAA", "CCCCC"])

    data = assert_success((await client.post(f"{API}/check", json={})).json())
    middle = line(data, "1")

    assert middle["pays"] == 2
    assert middle["matched_positions"] == ["r2c1", "r2c2"]
    assert middle["paying"] is True
    # r2c3 is the tile that broke the run, not r2c2 which is the last one that
    # still matched.
    assert middle["break_position"] == "r2c3"


async def test_a_line_whose_first_two_reels_differ_pays_nothing(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    """The rule that matters: three matching reels behind a break pay zero."""
    write_split(captures, "screenshot-1", ["BBBBB", "ABBBB", "CCCCC"])

    data = assert_success((await client.post(f"{API}/check", json={})).json())
    middle = line(data, "1")

    assert middle["pays"] == 0
    assert middle["paying"] is False
    assert middle["matched_positions"] == []
    # The later pairs did match; they were simply never counted.
    assert [step["matched"] for step in middle["steps"]] == [False, True, True, True]
    assert [step["counted"] for step in middle["steps"]] == [True, False, False, False]
    # Even a line that never gets going breaks somewhere -- at the second tile,
    # since the first pair (r2c1, r2c2) is what failed.
    assert middle["break_position"] == "r2c2"


async def test_a_dimmer_copy_of_a_symbol_is_the_same_symbol(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    """A glow must not break a run, which is why the measure is cosine."""
    write_split(captures, "screenshot-1", ["BBBBB", "AaAaA", "CCCCC"])

    data = assert_success((await client.post(f"{API}/check", json={})).json())

    assert line(data, "1")["pays"] == 5


async def test_a_diagonal_line_is_read_the_same_way(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    """Line 5 climbs r3c1, r2c2, r1c3 and comes back down: all A here."""
    write_split(captures, "screenshot-1", ["BBABB", "BABAB", "ABBBA"])

    data = assert_success((await client.post(f"{API}/check", json={})).json())

    assert line(data, "5")["pays"] == 5
    assert line(data, "1")["pays"] == 0


async def test_every_adjacent_pair_is_reported_with_its_score(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-1", ["BBBBB", "AABAA", "CCCCC"])

    data = assert_success((await client.post(f"{API}/check", json={})).json())
    steps = line(data, "1")["steps"]

    assert [(step["left"], step["right"]) for step in steps] == [
        ("r2c1", "r2c2"),
        ("r2c2", "r2c3"),
        ("r2c3", "r2c4"),
        ("r2c4", "r2c5"),
    ]
    assert steps[0]["similarity"] == pytest.approx(1.0)
    assert steps[1]["similarity"] < 0.2


# --- what it says ---------------------------------------------------------


async def test_the_summary_names_every_paying_line_in_order(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    # Row 2 is A,A,B,... so line 1 pays 2; row 3 is all C so line 3 pays 5.
    write_split(captures, "screenshot-1", ["BABAB", "AABCA", "CCCCC"])

    data = assert_success((await client.post(f"{API}/check", json={})).json())

    assert data["summary"].startswith("Line 1 pays 2")
    assert "Line 3 pays 5" in data["summary"]


async def test_nothing_paying_says_so_rather_than_coming_back_empty(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-1", ["ABABA", "BCBCB", "ABABA"])

    data = assert_success((await client.post(f"{API}/check", json={})).json())

    assert data["summary"] == "No line pays"
    assert data["stats"]["paying"] == 0
    assert data["stats"]["best_line"] is None
    assert len(data["lines"]) == 5


async def test_the_stats_count_distinct_pairs_and_report_the_separation(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-1", ["BBBBB", "AAAAA", "CCCCC"])

    data = assert_success((await client.post(f"{API}/check", json={})).json())
    stats = data["stats"]

    # Five lines of four steps is twenty questions, but the diagonals share
    # pairs with the straight rows, so fewer distinct pairs are measured.
    assert stats["lines"] == 5
    assert stats["comparisons"] <= 20
    assert stats["matched_min"] == pytest.approx(1.0)
    assert stats["rejected_max"] < CUT
    assert stats["best_line"] == "1"
    assert stats["best_pays"] == 5


async def test_each_line_carries_the_colour_it_is_drawn_in(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    """The panel's swatch and the overlay's stroke have to be one decision."""
    write_split(captures, "screenshot-1", ["AAAAA", "AAAAA", "AAAAA"])

    data = assert_success((await client.post(f"{API}/check", json={})).json())
    colours = [entry["color"] for entry in data["lines"]]

    assert len(set(colours)) == len(colours)
    assert all(colour.startswith("#") for colour in colours)


# --- the threshold --------------------------------------------------------


async def test_the_request_threshold_overrides_the_setting(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-1", ["BBBBB", "AABAA", "CCCCC"])

    loose = assert_success(
        (await client.post(f"{API}/check", json={"threshold": 0.05})).json()
    )
    strict = assert_success(
        (await client.post(f"{API}/check", json={"threshold": 0.99})).json()
    )

    # At 0.05 even two different symbols count as one, so the run runs on.
    assert line(loose, "1")["pays"] == 5
    assert loose["threshold"] == 0.05
    assert line(strict, "1")["pays"] == 2


# --- the picture ----------------------------------------------------------


async def test_the_annotated_reels_are_written_beside_the_tiles(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    directory = write_split(captures, "screenshot-1", ["BBBBB", "AAAAA", "CCCCC"])

    data = assert_success((await client.post(f"{API}/check", json={})).json())

    written = directory / "paylines" / "5.png"
    assert written.is_file()
    assert data["output_dir"] == str(directory / "paylines")
    assert data["output_file"] == "5.png"


async def test_the_overlay_is_larger_than_the_crop_so_a_line_is_legible(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-1", ["BBBBB", "AAAAA", "CCCCC"])

    data = assert_success((await client.post(f"{API}/check", json={})).json())
    overlay = decode(data["overlay_image"])

    assert overlay.width >= CROP_SIZE[0]
    assert overlay.width % CROP_SIZE[0] == 0
    assert overlay.width / CROP_SIZE[0] == overlay.height / CROP_SIZE[1]


async def test_the_picture_can_be_left_out_of_the_response(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    """The file is written either way -- only the base64 payload is optional."""
    directory = write_split(captures, "screenshot-1", ["BBBBB", "AAAAA", "CCCCC"])

    data = assert_success(
        (await client.post(f"{API}/check", json={"include_images": False})).json()
    )

    assert data["overlay_image"] is None
    assert all(entry["image_data"] is None for entry in data["lines"])
    assert (directory / "paylines" / "5.png").is_file()


async def test_every_line_gets_its_own_picture_paying_or_not(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    """Unlike the combined overlay, a non-paying line still gets a picture --
    it is the one whose break is worth seeing on its own."""
    write_split(captures, "screenshot-1", ["BBBBB", "AABAA", "CCCCC"])

    data = assert_success((await client.post(f"{API}/check", json={})).json())

    for entry in data["lines"]:
        assert entry["image_data"] is not None
        decode(entry["image_data"])  # each is a readable picture in its own right


async def test_a_board_where_nothing_pays_still_gets_a_picture(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-1", ["ABABA", "BCBCB", "ABABA"])

    data = assert_success((await client.post(f"{API}/check", json={})).json())

    assert data["overlay_image"] is not None


# --- choosing the split and the set ---------------------------------------


async def test_a_check_with_no_split_uses_the_newest_one(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    older = write_split(captures, "screenshot-old", ["BBBBB", "AAAAA", "CCCCC"])
    newer = write_split(captures, "screenshot-new", ["BBBBB", "ABBBB", "CCCCC"])
    # mtime, not name: the newest split is the one written last, and sorting the
    # directory names as strings is a bug waiting for the day they carry no
    # timestamp.
    os.utime(older, (1, 1))

    data = assert_success((await client.post(f"{API}/check", json={})).json())

    assert data["source"]["split"] == newer.name
    assert line(data, "1")["pays"] == 0


async def test_a_named_split_is_used_instead(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-old", ["BBBBB", "AAAAA", "CCCCC"])
    write_split(captures, "screenshot-new", ["BBBBB", "ABBBB", "CCCCC"])

    data = assert_success(
        (await client.post(f"{API}/check", json={"split": "screenshot-old"})).json()
    )

    assert data["source"]["split"] == "screenshot-old"
    assert line(data, "1")["pays"] == 5


async def test_a_named_set_is_checked_instead_of_the_default(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-1", ["BBBBB", "AAAAA", "CCCCC"])

    data = assert_success(
        (await client.post(f"{API}/check", json={"set": "20"})).json()
    )

    assert data["set"] == "20"
    assert len(data["lines"]) == 1
    assert data["output_file"] == "20.png"


# --- the failures ---------------------------------------------------------


async def test_no_split_at_all_is_a_404_pointing_at_the_grid_panel(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    response = await client.post(f"{API}/check", json={})

    assert response.status_code == 404
    payload = response.json()
    assert_failure(payload, code="PAYLINE_SOURCE_NOT_FOUND")
    assert "Reel grid panel" in payload["message"]


async def test_a_split_that_is_not_there_is_a_404(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-1", ["BBBBB", "AAAAA", "CCCCC"])

    response = await client.post(f"{API}/check", json={"split": "screenshot-9"})

    assert response.status_code == 404
    assert_failure(response.json(), code="PAYLINE_SOURCE_NOT_FOUND")


async def test_a_split_name_that_escapes_the_directory_is_a_400(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    response = await client.post(f"{API}/check", json={"split": "../secrets"})

    assert response.status_code == 400
    assert_failure(response.json(), code="BAD_REQUEST")


async def test_a_split_missing_a_tile_is_a_404_saying_which(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    """A hole in the middle of a payline is not a line that can be evaluated."""
    directory = write_split(captures, "screenshot-1", ["BBBBB", "AAAAA", "CCCCC"])
    (directory / "tiles" / "r2c3.png").unlink()

    response = await client.post(f"{API}/check", json={})

    assert response.status_code == 404
    payload = response.json()
    assert_failure(payload, code="PAYLINE_SOURCE_NOT_FOUND")
    assert "r2c3" in payload["message"]


async def test_a_split_of_the_wrong_shape_is_a_409(
    client: AsyncClient, game, captures: Path
) -> None:
    """A 409, not a 500: the config may be right and the split merely old."""
    game(
        {
            "name": "FortuneOx",
            "roi": {"reels": REELS},
            "reel_bounds": {"rows": GRID_ROWS, "columns": 4},
            "paylines": PAYLINES,
        }
    )
    write_split(captures, "screenshot-1", ["BBBBB", "AAAAA", "CCCCC"])

    response = await client.post(f"{API}/check", json={})

    assert response.status_code == 409
    payload = response.json()
    assert_failure(payload, code="PAYLINE_SOURCE_STALE")
    assert "3x5 split" in payload["message"]


async def test_a_game_with_no_paylines_is_a_404_on_a_check(
    client: AsyncClient, game, captures: Path
) -> None:
    game({"name": "HuffNPuffLink", "roi": {"reels": REELS}, "reel_bounds": REEL_BOUNDS})
    write_split(captures, "screenshot-1", ["BBBBB", "AAAAA", "CCCCC"])

    response = await client.post(f"{API}/check", json={})

    assert response.status_code == 404
    assert_failure(response.json(), code="PAYLINES_NOT_CONFIGURED")


async def test_a_set_the_game_does_not_declare_is_a_404_listing_the_ones_it_does(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-1", ["BBBBB", "AAAAA", "CCCCC"])

    response = await client.post(f"{API}/check", json={"set": "40"})

    assert response.status_code == 404
    payload = response.json()
    assert_failure(payload, code="PAYLINES_NOT_CONFIGURED")
    assert "5, 20" in payload["message"]


async def test_a_line_running_off_the_grid_is_a_500_naming_the_config(
    client: AsyncClient, game, captures: Path
) -> None:
    """The coordinates are wrong, not the request."""
    game(
        {
            "name": "FortuneOx",
            "roi": {"reels": REELS},
            "reel_bounds": REEL_BOUNDS,
            "paylines": {"5": {"1": [[1, 1], [4, 2]]}},
        }
    )
    write_split(captures, "screenshot-1", ["BBBBB", "AAAAA", "CCCCC"])

    response = await client.post(f"{API}/check", json={})

    assert response.status_code == 500
    assert_failure(response.json(), code="GAME_CONFIG_INVALID")


async def test_a_game_with_no_reel_bounds_is_a_404(
    client: AsyncClient, game, captures: Path
) -> None:
    game({"name": "FortuneOx", "roi": {"reels": REELS}, "paylines": PAYLINES})
    write_split(captures, "screenshot-1", ["BBBBB", "AAAAA", "CCCCC"])

    response = await client.post(f"{API}/check", json={})

    assert response.status_code == 404
    assert_failure(response.json(), code="GRID_NOT_CONFIGURED")


async def test_an_unknown_request_field_is_refused(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    response = await client.post(f"{API}/check", json={"tolerance": 0.9})

    assert response.status_code == 422


# --- comparing by symbol code ---------------------------------------------
#
# The other way two tiles can be "the same symbol": a classifier read a code off
# each, and the codes are equal. No threshold, no scores -- so these tests write
# splits whose *colours are irrelevant* and pass the codes in by hand, which is
# also the point of the split being read back rather than re-derived.
#
# One rule here has no counterpart in the similarity path and is easy to get
# wrong: **two unnamed tiles are not a match.** A classifier below its confidence
# floor said "I could not tell", and twice over that is not a run.


def geometry_set(lines: dict[str, list[list[int]]], name: str = "geometry-5"):
    """A line set assembled the way analyze_spin assembles one from winGeometry."""
    return payline_config.read_set({name: lines}, name)


def codes(*rows: str) -> dict[str, str | None]:
    """A board of two-letter codes as one string per row, ``--`` for unnamed.

    ``codes("AABBC", ...)`` is one character per reel keyed to a code, so a whole
    board reads as three lines and a transposition is visible rather than
    arithmetic -- the same trick :func:`write_split` plays with colours.
    """
    named = {"A": "AA", "B": "BB", "C": "CC", "-": None}
    return {
        f"r{row}c{column}": named[symbol]
        for row, entries in enumerate(rows, start=1)
        for column, symbol in enumerate(entries, start=1)
    }


async def test_a_whole_row_of_one_code_pays_five(
    active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-1", ["ABCAB", "AAAAA", "CBABC"])

    result = await paylines_service.check_symbols(
        geometry_set(FIVE_LINES), codes("ABCAB", "AAAAA", "CBABC")
    )

    data = result.model_dump()
    assert data["method"] == "symbol"
    assert line(data, "1")["pays"] == 5
    assert line(data, "1")["symbols"] == ["AA"] * 5
    assert line(data, "1")["matched_positions"] == [
        "r2c1",
        "r2c2",
        "r2c3",
        "r2c4",
        "r2c5",
    ]


async def test_a_run_stops_at_the_first_reel_with_a_different_code(
    active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-1", ["AAAAA", "AABAA", "AAAAA"])

    result = await paylines_service.check_symbols(
        geometry_set(FIVE_LINES), codes("AAAAA", "AABAA", "AAAAA")
    )

    middle = line(result.model_dump(), "1")
    assert middle["pays"] == 2
    assert middle["break_position"] == "r2c3"
    # Every pair is still compared -- the two after the break are the evidence
    # that the break was real.
    assert [step["counted"] for step in middle["steps"]] == [True, True, False, False]
    assert [step["matched"] for step in middle["steps"]] == [True, False, False, True]


async def test_two_unnamed_tiles_are_not_a_match(
    active_game: Path, captures: Path
) -> None:
    """The rule the similarity path has no equivalent of.

    A classifier that fell below its floor twice in a row said nothing twice, and
    reading that as a run of two would credit a spin with a pay nothing measured.
    """
    # The colours are irrelevant here -- what is under test is the codes, and
    # the split exists only so there are tiles of the right shape to read back.
    write_split(captures, "screenshot-1", ["AAAAA", "AAAAA", "AAAAA"])

    result = await paylines_service.check_symbols(
        geometry_set(FIVE_LINES), codes("AAAAA", "--AAA", "AAAAA")
    )

    middle = line(result.model_dump(), "1")
    assert middle["pays"] == 0
    assert middle["paying"] is False
    assert middle["break_position"] == "r2c2"
    assert middle["symbols"] == [None, None, "AA", "AA", "AA"]


async def test_a_line_through_one_unnamed_tile_stops_there(
    active_game: Path, captures: Path
) -> None:
    write_split(captures, "screenshot-1", ["AAAAA", "AAAAA", "AAAAA"])

    result = await paylines_service.check_symbols(
        geometry_set(FIVE_LINES), codes("AAAAA", "AA-AA", "AAAAA")
    )

    middle = line(result.model_dump(), "1")
    assert middle["pays"] == 2
    assert middle["break_position"] == "r2c3"


async def test_a_symbol_check_reports_codes_and_no_scores(
    active_game: Path, captures: Path
) -> None:
    """The evidence swaps over with the method, rather than being faked.

    A score of 1.0 for "the codes are equal" would read as a measurement, and the
    threshold it was compared against does not exist -- so both come back null
    and the codes come back instead.
    """
    write_split(captures, "screenshot-1", ["AAAAA", "AABAA", "AAAAA"])

    result = await paylines_service.check_symbols(
        geometry_set(FIVE_LINES), codes("AAAAA", "AABAA", "AAAAA")
    )

    data = result.model_dump()
    assert data["threshold"] is None
    stats = data["stats"]
    assert stats["score_min"] is None
    assert stats["score_max"] is None
    assert stats["matched_min"] is None
    assert stats["rejected_max"] is None
    # Distinct pairs, not steps. They come to the same twenty here because these
    # five lines share tiles but no adjacent *pair* -- which is what makes this a
    # check on the count rather than on the cache.
    assert stats["comparisons"] == 20
    step = line(data, "1")["steps"][1]
    assert step["similarity"] is None
    assert (step["left_symbol"], step["right_symbol"]) == ("AA", "BB")
    assert step["matched"] is False


async def test_a_symbol_step_reports_its_codes_in_the_order_it_was_asked(
    active_game: Path, captures: Path
) -> None:
    """Pairs are cached unordered, so a step read the other way must not transpose.

    Line 4 runs r1c1 -> r2c2, whose names sort the other way round from line 5's
    r3c1 -> r2c2. Both share the cached pair (r2c2, r3c1), and a comparer that
    handed the cached entry back unflipped would put r2c2's code on the left of a
    step whose left tile is r3c1.
    """
    board = ("ABCBA", "BBBBB", "CBABC")
    write_split(captures, "screenshot-1", list(board))

    result = await paylines_service.check_symbols(
        geometry_set(FIVE_LINES), codes(*board)
    )

    data = result.model_dump()
    for name in ("4", "5"):
        for step in line(data, name)["steps"]:
            assert step["left_symbol"] == codes(*board)[step["left"]]
            assert step["right_symbol"] == codes(*board)[step["right"]]


async def test_a_symbol_check_writes_the_overlay_where_a_similarity_one_does(
    active_game: Path, captures: Path
) -> None:
    """Everything below the comparison is the same joinery, deliberately."""
    directory = write_split(captures, "screenshot-1", ["ABCAB", "AAAAA", "CBABC"])

    result = await paylines_service.check_symbols(
        geometry_set(FIVE_LINES), codes("ABCAB", "AAAAA", "CBABC")
    )

    written = directory / "paylines" / "geometry-5.png"
    assert written.is_file()
    assert Path(result.output_dir) == directory / "paylines"
    assert result.output_file == "geometry-5.png"


async def test_a_similarity_check_names_no_symbols(
    client: AsyncClient, active_game: Path, captures: Path
) -> None:
    """The other half of the swap: cosine similarity cannot name a tile, and says
    so with an empty list rather than a row of nulls that would read as
    "the classifier was unsure"."""
    write_split(captures, "screenshot-1", ["ABCAB", "AAAAA", "CBABC"])

    response = await client.post(f"{API}/check", json={"set": "5"})

    data = assert_success(response.json())
    assert data["method"] == "similarity"
    assert data["threshold"] == CUT
    assert line(data, "1")["symbols"] == []
