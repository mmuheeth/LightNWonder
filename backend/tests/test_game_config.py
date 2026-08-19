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

from app.config.game_config import (
    ActiveGameSelectionError,
    GameConfigError,
    load_active_game,
    load_game_config,
    save_active_game,
)

FULL = {
    "name": "ExampleGame",
    "process": "ExampleGame.exe",
    "log": r"C:\logs\Game\ExampleGame.log",
    "obs": {"window_source": "Game Window"},
    "roi": {"cash_meter": [0.13, 0.75, 0.86, 0.78]},
    "button_targets": {"take_win": [0.124, 0.917]},
    "ideck": {"panel": "virtual_oled", "aliases": {"Primary": "ButtonA"}},
    "events": {
        "rules": [{"event": "jackpot-hit", "pattern": "MoneyLinkOutroSM"}],
        "disable": ["win-collected"],
    },
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
    assert game.obs_window_source == "Game Window"
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
    assert game.obs_window_source is None
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
        ({"obs": []}, "'obs'.*must be a JSON object"),
        (
            {"obs": {"window_source": 3}},
            "'obs.window_source'.*must be a string",
        ),
        ({"name": 7}, "'name'.*must be a string"),
        ({"roi": "everything"}, "'roi'.*must be a JSON object"),
        ({"button_targets": 1}, "'button_targets'.*must be a JSON object"),
        ({"events": []}, "'events'.*must be a JSON object"),
        (
            {"events": {"disable": "credit-meter-set"}},
            "'events.disable'.*array of strings",
        ),
        (
            {
                "events": {"rules": [{"event": "ok", "pattern": "("}]},
            },
            "not a valid regex",
        ),
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
        "obs-not-an-object",
        "obs-window-source-not-a-string",
        "name-not-a-string",
        "roi-not-an-object",
        "button-targets-not-an-object",
        "events-not-an-object",
        "events-disable-not-an-array",
        "events-rule-regex-invalid",
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


def test_active_game_selection_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "active_game.json"

    assert save_active_game(path, "ExampleGame") == "ExampleGame"
    assert load_active_game(path) == "ExampleGame"
    assert json.loads(path.read_text(encoding="utf-8")) == {"game": "ExampleGame"}


def test_active_game_selection_rejects_paths(tmp_path: Path) -> None:
    with pytest.raises(ActiveGameSelectionError, match="bare name"):
        save_active_game(tmp_path / "active_game.json", "../escape")


# --- the configs that actually ship ---------------------------------------

GAMES_DIR = (
    Path(__file__).resolve().parents[1] / "app" / "config" / "game_config" / "games"
)


def shipped_configs() -> list[Path]:
    return sorted(GAMES_DIR.glob("*.json"))


def test_there_are_shipped_configs_to_check() -> None:
    """Guards the two tests below from passing by finding nothing."""
    assert shipped_configs()


@pytest.mark.parametrize("path", shipped_configs(), ids=lambda path: path.stem)
def test_a_shipped_config_loads(path: Path) -> None:
    """A typo in one of these breaks the dashboard, not just one game."""
    game = load_game_config(path)

    assert game.name
    assert game.log_path is not None, "event capture needs a log to follow"


@pytest.mark.parametrize("path", shipped_configs(), ids=lambda path: path.stem)
def test_a_shipped_config_drops_no_default_rule(path: Path) -> None:
    """No shipped game needs to turn a default rule off.

    Pinned so that if one starts to, it is a deliberate choice rather than
    something left behind from an earlier trim.
    """
    game = load_game_config(path)

    assert game.disabled_events == ()


def test_huffnpufflink_declares_its_own_bet_rule() -> None:
    """Its theme log records a bet in a shape no other game's log uses.

    FortuneOx's client log publishes ``BetChangeMsg`` with the whole bet in it,
    which is what the shipped rule matches. The HuffNPuffLink theme log never
    logs that message at all -- the only record of a bet is
    ``[BetManager.UpdateCurrentBet]``, which it also re-logs unchanged several
    times a round. So the game declares a rule of its own, and marks it as one
    that fires only when a value moved.
    """
    game = load_game_config(GAMES_DIR / "HuffNPuffLink.json")

    rules = {rule.event: rule for rule in game.event_rules}
    assert "bet-changed" in rules
    assert rules["bet-changed"].only_on_change is True

    real = (
        "[BetManager.UpdateCurrentBet][CurrentBet {{ BetsPerUnit:125.000, "
        "UnitData:[ units: 243, cost: 60 ], TotalBetCost:7500.000, "
        "TotalBetValue:7500.000, DirectPlayData:{{ "
        "DirectPlayType:NotDirectPlay, BonusID:, BonusIndex:0, BonusOption:0 }} }}]"
    )
    found = rules["bet-changed"].pattern.search(real)
    assert found is not None
    assert found.group("total_bet") == "7500.000"


def test_fortuneox_needs_no_rules_of_its_own() -> None:
    """The shipped defaults were read off its log, so they should cover it."""
    game = load_game_config(GAMES_DIR / "FortuneOx.json")

    assert game.event_rules == ()
