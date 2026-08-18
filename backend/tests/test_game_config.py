"""The per-game config reader.

The i-deck tests cover the happy path end to end. What is pinned here is the
refusals: a game config is hand-edited, and a typo in it should say what is
wrong with which file rather than surfacing later as a missing button.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.config.game_config import GameConfigError, load_game_config

FULL = {
    "name": "ExampleGame",
    "process": "ExampleGame.exe",
    "log": r"C:\logs\Game\ExampleGame.log",
    "roi": {"cash_meter": [0.13, 0.75, 0.86, 0.78]},
    "button_targets": {"take_win": [0.124, 0.917]},
    "ideck": {"panel": "virtual_oled", "aliases": {"Primary": "ButtonA"}},
}


def write(tmp_path: Path, document: Any, name: str = "ExampleGame.json") -> Path:
    path = tmp_path / name
    path.write_text(
        document if isinstance(document, str) else json.dumps(document),
        encoding="utf-8",
    )
    return path


def test_every_block_is_read(tmp_path: Path) -> None:
    game = load_game_config(write(tmp_path, FULL))

    assert game.name == "ExampleGame"
    assert game.process == "ExampleGame.exe"
    assert game.ideck_panel == "virtual_oled"
    assert game.log_path == Path(r"C:\logs\Game\ExampleGame.log")
    assert game.roi["cash_meter"] == [0.13, 0.75, 0.86, 0.78]
    assert game.button_targets["take_win"] == [0.124, 0.917]


def test_alias_keys_are_casefolded_so_lookups_need_not_be(tmp_path: Path) -> None:
    """The alias arrives however the config author typed it; a press may not."""
    game = load_game_config(write(tmp_path, FULL))

    assert game.ideck_aliases["primary"] == "ButtonA"
    # The target keeps the layout's own casing, which is what gets looked up.
    assert "Primary" not in game.ideck_aliases


def test_a_game_without_an_ideck_block_is_not_an_error(tmp_path: Path) -> None:
    """Keys can always be pressed by their layout name, so aliases are optional."""
    game = load_game_config(write(tmp_path, {"name": "Bare"}))

    assert game.ideck_aliases == {}
    assert game.ideck_panel is None
    assert game.log_path is None
    assert game.roi == {}


def test_the_name_falls_back_to_the_filename(tmp_path: Path) -> None:
    game = load_game_config(write(tmp_path, {}, name="AnotherGame.json"))

    assert game.name == "AnotherGame"


def test_a_missing_file_names_the_path_it_looked_for(tmp_path: Path) -> None:
    with pytest.raises(GameConfigError, match="No game config at"):
        load_game_config(tmp_path / "Absent.json")


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ("{not json", "not valid JSON"),
        ([1, 2, 3], "must be a JSON object"),
        ({"ideck": []}, "'ideck'.*must be a JSON object"),
        ({"ideck": {"aliases": "spin"}}, "'ideck.aliases'.*must be a JSON object"),
        ({"ideck": {"aliases": {"spin": 5}}}, "must map strings to strings"),
        ({"ideck": {"panel": 3}}, "'ideck.panel'.*must be a string"),
        ({"log": ["a"]}, "'log'.*must be a string"),
        ({"process": ["a"]}, "'process'.*must be a string"),
        ({"name": 7}, "'name'.*must be a string"),
        ({"roi": "everything"}, "'roi'.*must be a JSON object"),
        ({"button_targets": 1}, "'button_targets'.*must be a JSON object"),
    ],
    ids=[
        "malformed-json",
        "not-an-object",
        "ideck-not-an-object",
        "aliases-not-an-object",
        "alias-target-not-a-string",
        "panel-not-a-string",
        "log-not-a-string",
        "process-not-a-string",
        "name-not-a-string",
        "roi-not-an-object",
        "button-targets-not-an-object",
    ],
)
def test_a_malformed_config_says_what_is_wrong(
    tmp_path: Path, document: Any, expected: str
) -> None:
    path = write(tmp_path, document)

    with pytest.raises(GameConfigError, match=expected):
        load_game_config(path)


def test_the_error_names_the_file_that_is_wrong(tmp_path: Path) -> None:
    """One backend, several games: the message has to say which file to fix."""
    path = write(tmp_path, {"ideck": {"aliases": {"spin": 5}}})

    with pytest.raises(GameConfigError, match=r"ExampleGame\.json"):
        load_game_config(path)
