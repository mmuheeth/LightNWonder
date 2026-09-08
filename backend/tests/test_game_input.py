"""Clicking the game window.

No real window is ever touched. ``FakeGame`` stands in for :mod:`app.utils.win32`
and is installed over its module functions, matching how ``test_ideck.py`` swaps
the panel and ``test_obs.py`` swaps the OBS client.

The fake does more than record calls: it **hit-tests** the coordinates it is
given against the same ``button_targets`` fractions the service reads, and writes
the game's confirmation line only if the click actually landed on the intended
target. A click that lands anywhere else in the window gets only the generic
touch line, which is exactly what the real game does. So a test asserting a click
was confirmed by ``target-event`` is asserting the whole coordinate pipeline --
fraction, scaling, rounding, clamping and message packing -- and a test asserting
the "you missed" failure is asserting the diagnosis the service gives for it.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.config.game_config.selection import save_active_game
from app.config.runtime import settings
from app.services import game_input as game_input_service
from app.utils import win32
from app.utils.click_target import ClickTarget
from tests.asserts import assert_failure, assert_success

# Real fractions from games/FortuneOx.json, and the real simulator window size.
TAKE_WIN = [0.0713, 0.9724]
GAMBLE = [0.0694, 0.9383]
WIDTH, HEIGHT = 518, 1033

GAME_CONFIG: dict[str, Any] = {
    "name": "FortuneOx",
    "process": "FortuneOx.exe",
    "log": "",  # rewritten per test to a temp file
    "button_targets": {
        "take_win": TAKE_WIN,
        "gamble": GAMBLE,
        # An explicit confirmation, in the object form.
        "gamble_red": {"point": [0.3, 0.5], "confirm": "gamble-picked"},
        # No default and no declaration: only a generic touch can prove this one.
        "info": [0.9, 0.05],
    },
}

READY_WINDOW = win32.WindowInfo(
    hwnd=0x1080C,
    title="FortuneOx",
    class_name="UnityWndClass",
    minimized=False,
    client_width=WIDTH,
    client_height=HEIGHT,
)

# The real shapes, from C:\logs\Game\FortuneOx\Logs\FortuneOx_Client.log.
_PREFIX = "08/19/26 03:39:18.580 00 FortuneOx:9244 DBG: "
TOUCH_LINE = (
    f"{_PREFIX}ServerProxy.ClientToServerRequest: "
    "GDK.Common.ServerAPI.TouchMsg, rval: 0"
)
# What the game publishes for each event name the targets above confirm against.
EVENT_LINES = {
    "gamble-accepted": f"{_PREFIX}[MessageQueue.Publish] msg[double_up_offer_accept]",
    "gamble-declined": f"{_PREFIX}[MessageQueue.Publish] msg[double_up_offer_decline]",
    "gamble-picked": f"{_PREFIX}[MessageQueue.Publish] msg[RED_BLACK_RED_CARD]",
}

# How close a click has to land to count as hitting the button. The buttons are
# real UI elements tens of pixels across; this is deliberately tight enough that
# a drifted fraction fails.
HIT_RADIUS = 8


class FakeGame:
    """Stands in for :mod:`app.utils.win32` and the game window behind it.

    ``accepts`` models the failure the design has to survive: a window that
    ignores injected input logs nothing at all. ``accepts_after_focus`` models
    the narrower case where only the first attempt is swallowed -- the second,
    after the retry's fresh foreground/cursor sequence, lands.
    """

    def __init__(
        self,
        log_path: Path,
        targets: dict[str, Any],
        *,
        window: win32.WindowInfo | None = READY_WINDOW,
        supported: bool = True,
        accepts: bool = True,
        accepts_after_focus: bool = False,
        restore_works: bool = True,
        can_interact: bool = True,
        topmost_matches: bool = True,
    ) -> None:
        self.log_path = log_path
        self.targets = targets
        self.window = window
        self.supported = supported
        self.accepts = accepts
        self.accepts_after_focus = accepts_after_focus
        self.restore_works = restore_works
        self.can_interact = can_interact
        self.topmost_matches = topmost_matches
        self.calls: list[tuple[str, int, int]] = []
        self._attempts = 0
        self._last_point = (0, 0)

    # --- the win32 surface the service uses ---

    def is_supported(self) -> bool:
        return self.supported

    def find_window(
        self,
        *,
        title: str,  # noqa: ARG002 - part of the signature being stood in for
        class_name: str | None = None,  # noqa: ARG002
    ) -> win32.WindowInfo | None:
        return self.window

    def describe(self, hwnd: int) -> win32.WindowInfo | None:  # noqa: ARG002
        return self.window

    def can_post(self, hwnd: int) -> bool:  # noqa: ARG002
        return self.can_interact

    def restore(self, hwnd: int) -> bool:
        self.calls.append(("restore", hwnd, 0))
        if not self.can_interact:
            return False
        if self.restore_works and self.window is not None:
            self.window = dataclasses.replace(
                self.window,
                minimized=False,
                client_width=WIDTH,
                client_height=HEIGHT,
            )
        return True

    def focus(self, hwnd: int) -> bool:
        self.calls.append(("focus", hwnd, 0))
        if self.accepts_after_focus:
            self.accepts = True
        return True

    # -- the SendInput surface used by `_inject_click` --

    def client_to_screen(self, hwnd: int, x: int, y: int) -> tuple[int, int]:  # noqa: ARG002
        # Identity mapping: the fake window sits at the screen origin, so
        # client and screen coordinates coincide and the existing hit-testing
        # (measured in client space) still applies unchanged.
        return x, y

    def get_cursor_pos(self) -> tuple[int, int]:
        return (0, 0)

    def set_cursor_pos(self, x: int, y: int) -> None:
        self._last_point = (x, y)

    def window_at(self, x: int, y: int) -> int:  # noqa: ARG002
        if self.window is None:
            return 0
        return self.window.hwnd if self.topmost_matches else self.window.hwnd + 1

    def bring_to_front(self, hwnd: int) -> bool:
        self.calls.append(("focus", hwnd, 0))
        return True

    def inject_left_down(self) -> None:
        self.calls.append(("down", *self._last_point))

    def inject_left_up(self) -> None:
        x, y = self._last_point
        self.calls.append(("up", x, y))
        self._attempts += 1
        # `accepts_after_focus` used to flip on the retry's explicit `focus`
        # call; it is now equivalent to "the second attempt lands", since
        # `bring_to_front` runs before every attempt, not just the retry.
        accepted = self.accepts or (self.accepts_after_focus and self._attempts >= 2)
        if not accepted:
            return
        # The real game reacts on the release. A touch anywhere in the window is
        # felt; only a touch on a button publishes that button's message.
        self._append(TOUCH_LINE)
        hit = self._hit_test(x, y)
        if hit is not None and hit.confirm in EVENT_LINES:
            self._append(EVENT_LINES[hit.confirm])

    # --- helpers ---

    def _append(self, line: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _hit_test(self, x: int, y: int) -> ClickTarget | None:
        """Which configured target a client-space point lands on, if any."""
        window = self.window
        if window is None or not window.client_width or not window.client_height:
            return None
        for name in self.targets:
            target = ClickTarget.from_value(self.targets[name], name=name)
            at_x, at_y = target.to_point(window.client_width, window.client_height)
            if abs(at_x - x) <= HIT_RADIUS and abs(at_y - y) <= HIT_RADIUS:
                return target
        return None

    def kinds(self) -> list[str]:
        return [kind for kind, _, _ in self.calls]

    def points(self, kind: str) -> list[tuple[int, int]]:
        return [(x, y) for name, x, y in self.calls if name == kind]


@pytest.fixture
def game_log_file(tmp_path: Path) -> Path:
    path = tmp_path / "FortuneOx_Client.log"
    path.write_text("", encoding="utf-8")
    return path


@pytest.fixture
def game_input_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, game_log_file: Path
) -> Iterator[None]:
    """Point the service at temp config, and make its waits instant."""
    games = tmp_path / "games"
    games.mkdir()
    config = {**GAME_CONFIG, "log": str(game_log_file)}
    (games / "FortuneOx.json").write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setattr(settings, "IDECK_GAME_CONFIG_DIR", games)
    save_active_game(settings.ideck_active_game_path, "FortuneOx")
    monkeypatch.setattr(settings, "GAME_INPUT_WINDOW_TITLE", "")
    monkeypatch.setattr(settings, "GAME_INPUT_WINDOW_CLASS", "UnityWndClass")
    monkeypatch.setattr(settings, "GAME_INPUT_CLICK_HOLD_SECONDS", 0.0)
    monkeypatch.setattr(settings, "GAME_INPUT_VERIFY_CLICKS", True)
    monkeypatch.setattr(settings, "GAME_INPUT_VERIFY_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(settings, "GAME_INPUT_RESTORE_IF_MINIMIZED", True)
    monkeypatch.setattr(settings, "GAME_INPUT_FOCUS_ON_RETRY", True)
    monkeypatch.setattr(game_input_service, "_POLL_SECONDS", 0.0)
    monkeypatch.setattr(game_input_service, "_RESTORE_ATTEMPTS", 2)
    yield


def install(monkeypatch: pytest.MonkeyPatch, game: FakeGame) -> FakeGame:
    """Make ``game`` the Win32 layer the service talks to."""
    for name in (
        "is_supported",
        "find_window",
        "describe",
        "can_post",
        "restore",
        "focus",
        "client_to_screen",
        "get_cursor_pos",
        "set_cursor_pos",
        "window_at",
        "bring_to_front",
        "inject_left_down",
        "inject_left_up",
    ):
        monkeypatch.setattr(win32, name, getattr(game, name))
    return game


@pytest.fixture
def game(monkeypatch: pytest.MonkeyPatch, game_log_file: Path) -> FakeGame:
    return install(
        monkeypatch, FakeGame(game_log_file, dict(GAME_CONFIG["button_targets"]))
    )


# --- clicking -------------------------------------------------------------


async def test_click_sends_move_then_down_then_up(
    client: AsyncClient, game_input_env: None, game: FakeGame
) -> None:
    """The cursor moves before the button does.

    Unity samples the cursor's real position rather than trusting a message's
    coordinates, so the click has to bring the window to front, park the real
    cursor on the target, and only then inject the press/release -- dropping
    the move would land the click wherever the cursor already was.
    """
    data = assert_success(
        (await client.post("/api/game-input/click", json={"target": "gamble"})).json()
    )
    assert game.kinds() == ["focus", "down", "up"]
    assert game.points("down") == [(36, 969)]
    assert (data["client_x"], data["client_y"]) == (36, 969)
    assert data["confirmed"] is True
    assert data["confirmed_by"] == "target-event"
    assert data["expected_event"] == "gamble-accepted"
    assert "double_up_offer_accept" in data["evidence"]
    assert data["game"] == "FortuneOx"


async def test_take_win_is_confirmed_by_the_decline_message(
    client: AsyncClient, game_input_env: None, game: FakeGame
) -> None:
    """Taking the win *is* declining the gamble, which is what the game logs.

    Read off the real logs: declining publishes ``double_up_offer_decline`` and
    then sets the credit meter. Getting this pairing backwards would confirm a
    take-win when the player had gambled instead.
    """
    data = assert_success(
        (await client.post("/api/game-input/click", json={"target": "take_win"})).json()
    )
    assert game.points("down") == [(37, 1004)]
    assert data["expected_event"] == "gamble-declined"
    assert data["confirmed_by"] == "target-event"
    assert "double_up_offer_decline" in data["evidence"]


async def test_click_scales_to_a_resized_window(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    game_log_file: Path,
) -> None:
    """A simulator shown at double size still gets clicked on the right button."""
    doubled = dataclasses.replace(
        READY_WINDOW, client_width=WIDTH * 2, client_height=HEIGHT * 2
    )
    live = install(
        monkeypatch,
        FakeGame(game_log_file, dict(GAME_CONFIG["button_targets"]), window=doubled),
    )
    data = assert_success(
        (await client.post("/api/game-input/click", json={"target": "gamble"})).json()
    )
    assert live.points("down") == [(72, 1939)]
    # The fake hit-tests against the same fractions, so confirmation proves the
    # scaled point still lands on the gamble button.
    assert data["confirmed_by"] == "target-event"


async def test_a_target_name_is_matched_case_insensitively(
    client: AsyncClient, game_input_env: None, game: FakeGame
) -> None:
    data = assert_success(
        (await client.post("/api/game-input/click", json={"target": "GAMBLE"})).json()
    )
    assert data["confirmed"] is True


async def test_an_explicit_confirmation_in_the_config_is_used(
    client: AsyncClient, game_input_env: None, game: FakeGame
) -> None:
    """The object form lets a game declare a proof for its own buttons."""
    data = assert_success(
        (
            await client.post("/api/game-input/click", json={"target": "gamble_red"})
        ).json()
    )
    assert data["expected_event"] == "gamble-picked"
    assert data["confirmed_by"] == "target-event"
    assert "RED_BLACK_RED_CARD" in data["evidence"]


# --- confirmation ---------------------------------------------------------


async def test_a_target_with_no_known_event_falls_back_to_a_touch(
    client: AsyncClient, game_input_env: None, game: FakeGame
) -> None:
    """Weaker proof, honestly labelled, rather than a refusal or a false claim."""
    data = assert_success(
        (await client.post("/api/game-input/click", json={"target": "info"})).json()
    )
    assert data["confirmed"] is True
    assert data["confirmed_by"] == "touch"
    assert data["expected_event"] is None
    assert "TouchMsg" in data["evidence"]


async def test_a_click_that_misses_the_button_says_to_re_measure(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    game_log_file: Path,
) -> None:
    """The failure a coordinate-based design actually has.

    The window accepts the click and the game feels the touch, but nothing was
    hit -- so the coordinate has drifted. The fake models this by hit-testing
    against fractions the service does not share.
    """
    moved = {**dict(GAME_CONFIG["button_targets"]), "gamble": [0.5, 0.5]}
    live = install(monkeypatch, FakeGame(game_log_file, moved))
    response = await client.post("/api/game-input/click", json={"target": "gamble"})
    assert response.status_code == 502
    payload = response.json()
    assert_failure(payload, code="GAME_CLICK_NOT_CONFIRMED")
    assert "registered a touch" in payload["message"]
    assert "re-measuring" in payload["message"]
    # It was posted twice: unconfirmed clicks get one focused retry.
    assert live.points("down") == [(36, 969), (36, 969)]


async def test_an_ignored_click_is_reported_rather_than_claimed(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    game_log_file: Path,
) -> None:
    """The point of verification: a window that swallows input must not 200.

    And the message has to separate this from a miss -- no touch at all is a
    window or privilege problem, not a coordinate one.
    """
    live = install(
        monkeypatch,
        FakeGame(game_log_file, dict(GAME_CONFIG["button_targets"]), accepts=False),
    )
    response = await client.post("/api/game-input/click", json={"target": "gamble"})
    assert response.status_code == 502
    payload = response.json()
    assert_failure(payload, code="GAME_CLICK_NOT_CONFIRMED")
    assert "not even a touch" in payload["message"]
    assert "elevated" in payload["message"]
    assert live.kinds().count("down") == 2


async def test_a_swallowed_first_click_is_retried_with_focus(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    game_log_file: Path,
) -> None:
    """A window that has never been focused can drop the first posted click."""
    live = install(
        monkeypatch,
        FakeGame(
            game_log_file,
            dict(GAME_CONFIG["button_targets"]),
            accepts=False,
            accepts_after_focus=True,
        ),
    )
    data = assert_success(
        (await client.post("/api/game-input/click", json={"target": "gamble"})).json()
    )
    assert data["confirmed"] is True
    assert data["refocused"] is True
    assert "focus" in live.kinds()


async def test_an_earlier_gamble_does_not_confirm_our_click(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    game_log_file: Path,
) -> None:
    """Only what was appended after the click counts.

    A person at the machine gambles all the time, so the log is full of exactly
    the line being watched for. Reading from a cursor taken before the click is
    what stops a stale one confirming it.
    """
    game_log_file.write_text(
        EVENT_LINES["gamble-accepted"] + "\n" + TOUCH_LINE + "\n", encoding="utf-8"
    )
    install(
        monkeypatch,
        FakeGame(game_log_file, dict(GAME_CONFIG["button_targets"]), accepts=False),
    )
    response = await client.post("/api/game-input/click", json={"target": "gamble"})
    assert response.status_code == 502
    assert_failure(response.json(), code="GAME_CLICK_NOT_CONFIRMED")


async def test_verification_can_be_turned_off_for_one_click(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    game_log_file: Path,
) -> None:
    """Unverified is allowed, but it is never dressed up as confirmed."""
    live = install(
        monkeypatch,
        FakeGame(game_log_file, dict(GAME_CONFIG["button_targets"]), accepts=False),
    )
    data = assert_success(
        (
            await client.post(
                "/api/game-input/click", json={"target": "gamble", "verify": False}
            )
        ).json()
    )
    assert data["verified"] is False
    assert data["confirmed"] is False
    assert data["confirmed_by"] is None
    assert live.kinds() == ["focus", "down", "up"]


async def test_verification_without_a_log_refuses_rather_than_degrades(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    game: FakeGame,
) -> None:
    """Silently dropping to unverified would defeat the whole design."""
    games = tmp_path / "games"
    config = {**GAME_CONFIG, "log": str(tmp_path / "absent.log")}
    (games / "FortuneOx.json").write_text(json.dumps(config), encoding="utf-8")
    game_input_service.reset()

    response = await client.post("/api/game-input/click", json={"target": "gamble"})
    assert response.status_code == 500
    payload = response.json()
    assert_failure(payload, code="GAME_INPUT_CONFIG_INVALID")
    assert "does not exist yet" in payload["message"]
    # Nothing was posted: the log is checked before any window is touched.
    assert game.kinds() == []


async def test_a_game_naming_no_log_cannot_verify(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    game: FakeGame,
) -> None:
    games = tmp_path / "games"
    config = {key: value for key, value in GAME_CONFIG.items() if key != "log"}
    (games / "FortuneOx.json").write_text(json.dumps(config), encoding="utf-8")
    game_input_service.reset()

    response = await client.post("/api/game-input/click", json={"target": "gamble"})
    assert response.status_code == 500
    payload = response.json()
    assert_failure(payload, code="GAME_INPUT_CONFIG_INVALID")
    assert "names no 'log'" in payload["message"]
    assert game.kinds() == []


# --- naming a target ------------------------------------------------------


async def test_click_rejects_an_unknown_target(
    client: AsyncClient, game_input_env: None, game: FakeGame
) -> None:
    response = await client.post("/api/game-input/click", json={"target": "collect"})
    assert response.status_code == 404
    payload = response.json()
    assert_failure(payload, code="GAME_TARGET_NOT_FOUND")
    # The message lists what *is* clickable, so a typo is self-correcting.
    assert "gamble" in payload["message"]
    assert "take_win" in payload["message"]
    # Nothing was posted: the name is checked before any window is touched.
    assert game.kinds() == []


async def test_a_malformed_target_is_a_config_error_not_a_missing_one(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    game_log_file: Path,
    game: FakeGame,
) -> None:
    """A name that is present but unreadable is the config's fault, not the caller's."""
    games = tmp_path / "games"
    config = {
        **GAME_CONFIG,
        "log": str(game_log_file),
        "button_targets": {"gamble": [5.0, 0.5]},
    }
    (games / "FortuneOx.json").write_text(json.dumps(config), encoding="utf-8")
    game_input_service.reset()

    response = await client.post("/api/game-input/click", json={"target": "gamble"})
    assert response.status_code == 500
    payload = response.json()
    assert_failure(payload, code="GAME_INPUT_CONFIG_INVALID")
    assert "button_targets.gamble" in payload["message"]
    assert game.kinds() == []


async def test_a_blank_target_is_rejected_by_the_schema(
    client: AsyncClient, game_input_env: None, game: FakeGame
) -> None:
    response = await client.post("/api/game-input/click", json={"target": ""})
    assert response.status_code == 422
    assert game.kinds() == []


# --- the window -----------------------------------------------------------


async def test_click_needs_the_game_running(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    game_log_file: Path,
) -> None:
    install(
        monkeypatch,
        FakeGame(game_log_file, dict(GAME_CONFIG["button_targets"]), window=None),
    )
    response = await client.post("/api/game-input/click", json={"target": "gamble"})
    assert response.status_code == 409
    payload = response.json()
    assert_failure(payload, code="GAME_WINDOW_NOT_FOUND")
    assert "FortuneOx" in payload["message"]


async def test_click_restores_a_minimized_game(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    game_log_file: Path,
) -> None:
    """The simulator is often left minimized; a click should still work."""
    iconic = dataclasses.replace(
        READY_WINDOW, minimized=True, client_width=0, client_height=0
    )
    live = install(
        monkeypatch,
        FakeGame(game_log_file, dict(GAME_CONFIG["button_targets"]), window=iconic),
    )
    data = assert_success(
        (await client.post("/api/game-input/click", json={"target": "gamble"})).json()
    )
    assert data["restored"] is True
    assert data["confirmed"] is True
    assert "restore" in live.kinds()


async def test_click_refuses_a_minimized_game_when_restoring_is_off(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    game_log_file: Path,
) -> None:
    monkeypatch.setattr(settings, "GAME_INPUT_RESTORE_IF_MINIMIZED", False)
    iconic = dataclasses.replace(
        READY_WINDOW, minimized=True, client_width=0, client_height=0
    )
    install(
        monkeypatch,
        FakeGame(game_log_file, dict(GAME_CONFIG["button_targets"]), window=iconic),
    )
    response = await client.post("/api/game-input/click", json={"target": "gamble"})
    assert response.status_code == 409
    assert_failure(response.json(), code="GAME_WINDOW_NOT_FOUND")


async def test_click_refuses_when_another_window_is_topmost(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    game_log_file: Path,
) -> None:
    """Injected input lands on whatever is topmost, not at an hwnd.

    A click must refuse rather than fire blind when something else covers the
    target point -- the failure mode that caught a File Explorer window in
    production for the reference implementation this mirrors.
    """
    live = install(
        monkeypatch,
        FakeGame(
            game_log_file, dict(GAME_CONFIG["button_targets"]), topmost_matches=False
        ),
    )
    response = await client.post("/api/game-input/click", json={"target": "gamble"})
    assert response.status_code == 409
    payload = response.json()
    assert_failure(payload, code="GAME_WINDOW_NOT_FOUND")
    assert "not the game" in payload["message"]
    # No press was injected once the mismatch was caught.
    assert "down" not in live.kinds()


async def test_click_reports_windows_blocking_input(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    game_log_file: Path,
) -> None:
    """UIPI is a deployment detail, and the message has to say how to fix it.

    The game really does run elevated on these machines -- its executable path
    is unreadable from a normal process -- so this is the common case, not an
    exotic one.
    """
    live = install(
        monkeypatch,
        FakeGame(
            game_log_file,
            dict(GAME_CONFIG["button_targets"]),
            can_interact=False,
        ),
    )
    response = await client.post("/api/game-input/click", json={"target": "gamble"})
    assert response.status_code == 409
    payload = response.json()
    assert_failure(payload, code="GAME_INPUT_ACCESS_DENIED")
    assert "Run as administrator" in payload["message"]
    # Checked before anything is posted, so a blocked click is not also a
    # mystery about which of the later calls failed.
    assert live.kinds() == []


async def test_click_needs_windows(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    game_log_file: Path,
) -> None:
    install(
        monkeypatch,
        FakeGame(game_log_file, dict(GAME_CONFIG["button_targets"]), supported=False),
    )
    response = await client.post("/api/game-input/click", json={"target": "gamble"})
    assert response.status_code == 503
    assert_failure(response.json(), code="SERVICE_UNAVAILABLE")


# --- status and listing ---------------------------------------------------


async def test_status_reports_a_ready_window(
    client: AsyncClient, game_input_env: None, game: FakeGame
) -> None:
    data = assert_success((await client.get("/api/game-input/status")).json())
    assert data["state"] == "ready"
    assert data["window_title"] == "FortuneOx"
    assert data["window_class"] == "UnityWndClass"
    assert data["client_width"] == WIDTH
    assert data["target_count"] == 4
    assert data["verify_clicks"] is True


async def test_status_reports_a_closed_game_without_failing(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    game_log_file: Path,
) -> None:
    """A closed game is a state to report, not a failed request."""
    install(
        monkeypatch,
        FakeGame(game_log_file, dict(GAME_CONFIG["button_targets"]), window=None),
    )
    response = await client.get("/api/game-input/status")
    assert response.status_code == 200
    assert assert_success(response.json())["state"] == "not_found"


async def test_status_reports_blocked_input(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    game_log_file: Path,
) -> None:
    """Reported rather than left to be guessed at from a failing click."""
    install(
        monkeypatch,
        FakeGame(
            game_log_file,
            dict(GAME_CONFIG["button_targets"]),
            can_interact=False,
        ),
    )
    data = assert_success((await client.get("/api/game-input/status")).json())
    assert data["state"] == "access_denied"


async def test_targets_lists_both_coordinate_spaces(
    client: AsyncClient, game_input_env: None, game: FakeGame
) -> None:
    """What a coordinate gets checked against when it needs re-measuring."""
    data = assert_success((await client.get("/api/game-input/targets")).json())
    assert [entry["name"] for entry in data] == [
        "gamble",
        "gamble_red",
        "info",
        "take_win",
    ]
    by_name = {entry["name"]: entry for entry in data}
    assert by_name["gamble"]["fraction_x"] == 0.0694
    assert (by_name["gamble"]["client_x"], by_name["gamble"]["client_y"]) == (36, 969)
    assert by_name["gamble"]["confirm_event"] == "gamble-accepted"
    assert by_name["info"]["confirm_event"] is None


async def test_targets_omits_client_coordinates_without_a_window(
    client: AsyncClient,
    game_input_env: None,
    monkeypatch: pytest.MonkeyPatch,
    game_log_file: Path,
) -> None:
    install(
        monkeypatch,
        FakeGame(game_log_file, dict(GAME_CONFIG["button_targets"]), window=None),
    )
    data = assert_success((await client.get("/api/game-input/targets")).json())
    assert all(entry["client_x"] is None for entry in data)
    # The configured fractions are still reported: they do not need a window.
    assert all(entry["fraction_x"] is not None for entry in data)


# --- configuration --------------------------------------------------------


async def test_the_window_title_defaults_to_the_active_game(
    game_input_env: None, game: FakeGame
) -> None:
    """One source of truth: the simulator titles its window after the game."""
    assert game_input_service._window_title() == "FortuneOx"


async def test_the_window_title_can_be_pinned_by_setting(
    game_input_env: None, monkeypatch: pytest.MonkeyPatch, game: FakeGame
) -> None:
    monkeypatch.setattr(settings, "GAME_INPUT_WINDOW_TITLE", "Something Else")
    assert game_input_service._window_title() == "Something Else"


async def test_switching_game_drops_the_cached_config(
    client: AsyncClient,
    game_input_env: None,
    tmp_path: Path,
    game_log_file: Path,
    game: FakeGame,
) -> None:
    """A cached GameConfig would keep clicking the old game's coordinates."""
    assert_success((await client.get("/api/game-input/targets")).json())

    other = {
        "name": "Other",
        "log": str(game_log_file),
        "button_targets": {"gamble": [0.25, 0.25]},
    }
    (tmp_path / "games" / "Other.json").write_text(json.dumps(other), encoding="utf-8")
    assert_success(
        (await client.put("/api/games/active", json={"game": "Other"})).json()
    )

    data = assert_success((await client.get("/api/game-input/targets")).json())
    assert [entry["name"] for entry in data] == ["gamble"]
    assert (data[0]["client_x"], data[0]["client_y"]) == (130, 258)
