"""The scripted attendant-menu replay.

Nothing real is touched. Two fakes stand in for the two very different things
the sequence drives, which is the shape of the feature itself:

``FakeCabinet`` replaces :mod:`app.utils.win32` -- the DevTool window and its
controls, the menu's *window*, and the game's window -- and reacts to where a
click lands, the way ``test_game_input.py``'s fake does. Pressing *Connect*
enables *Attendant Key*; pressing *Attendant Key* opens the menu.

``FakeMenu`` replaces :mod:`app.utils.cdp` -- the menu's *page* -- as a few
screens that clicking moves between, with elements shaped like the real ones:
the nav buttons are ``div``s whose ``id`` is their label, and *View* is a
``<button>`` with no id, one per record row, wrapped in a panel reading the
same text. So a test that asserts a run completed is asserting the whole
resolution path -- id before text, innermost match, first row is newest,
nothing clicked out of view -- and a test that asserts a failure is asserting
the diagnosis given for it.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from PIL import Image

from app.config.game_config.selection import save_active_game
from app.config.runtime import settings
from app.exceptions.base import ObsRequestError, ReplayScreenshotNotFoundError
from app.schemas.obs import ObsGameWindowSelection, ScreenshotResult
from app.services import game_input as game_input_service
from app.services import obs as obs_service
from app.services import replay as replay_service
from app.services import roi as roi_service
from app.utils import cdp, win32, window_ui
from tests.asserts import assert_failure, assert_success

# --- the cabinet the fakes model -----------------------------------------

DEVTOOL_CLASS = "WindowsForms10.Window.8.app.0.3553390_r8_ad1"
DEVTOOL_TITLE = "DevTool"
ADMIN_TITLE = "System Admin"
ADMIN_CLASS = "Chrome_WidgetWin_1"
GAME_TITLE = "FortuneOx"
GAME_CLASS = "UnityWndClass"

CDP_URL = "http://127.0.0.1:9999"
PAGE_URL = "http://localhost:9002/home/homepage"
SHOT_DIR = r"C:\obs-captured-files\replay"

# Windows laid out so that no two overlap: `window_at` has to be able to say
# which one a screen point belongs to, exactly as the real desktop does.
DEVTOOL_ORIGIN = (200, 200)
DEVTOOL_CLIENT = (750, 570)
ADMIN_ORIGIN = (1200, 100)
ADMIN_CLIENT = (1150, 640)
GAME_ORIGIN = (0, 900)
GAME_CLIENT = (420, 840)

# Client-space rectangles of the two controls the sequence presses, close to
# the real ones read off the live window.
CONNECT_RECT = (499, 131, 613, 165)
ATTENDANT_RECT = (254, 250, 375, 273)

EXIT_GAMEPLAY = [0.5, 0.94]

GAME_CONFIG: dict[str, Any] = {
    "name": GAME_TITLE,
    "process": "FortuneOx.exe",
    "log": "",
    "button_targets": {
        "take_win": [0.0713, 0.9724],
        "exit_gameplay": EXIT_GAMEPLAY,
    },
}

# Somebody else's window, for modelling a foreground this process cannot take:
# an always-on-top window or a dialog sitting over the target.
STRANGER_HWND = 0xBEEF1

# How close a click has to land to count as pressing something. The buttons are
# real UI elements tens of pixels across; tight enough that a drifted fraction
# fails.
HIT_RADIUS = 6


def _control(
    text: str, rect: tuple[int, int, int, int], *, enabled: bool
) -> win32.ControlInfo:
    """One DevTool control, in screen coordinates as Win32 reports them."""
    left, top, right, bottom = rect
    return win32.ControlInfo(
        hwnd=0x1000 + abs(hash(text)) % 0x1000,
        class_name="WindowsForms10.BUTTON.app.0.3553390_r8_ad1",
        text=text,
        enabled=enabled,
        visible=True,
        left=DEVTOOL_ORIGIN[0] + left,
        top=DEVTOOL_ORIGIN[1] + top,
        right=DEVTOOL_ORIGIN[0] + right,
        bottom=DEVTOOL_ORIGIN[1] + bottom,
    )


def _element(
    text: str,
    *,
    element_id: str = "",
    tag: str = "div",
    x: float = 20.0,
    y: float = 100.0,
    width: float = 240.0,
    height: float = 48.0,
    visible: bool = True,
    in_viewport: bool = True,
) -> cdp.Element:
    """One element of the menu's page."""
    return cdp.Element(
        text=text,
        tag=tag,
        element_id=element_id,
        x=x,
        y=y,
        width=width,
        height=height,
        visible=visible,
        in_viewport=in_viewport,
    )


def _hits(element: cdp.Element, x: float, y: float) -> bool:
    """Whether a click at ``(x, y)`` in the page lands on an element."""
    return (
        element.x <= x <= element.x + element.width
        and element.y <= y <= element.y + element.height
    )


class FakeMenu:
    """The attendant menu's page: a few screens, and what clicking does."""

    def __init__(
        self,
        *,
        reachable: bool = True,
        rows: int = 3,
        exit_closes: bool = True,
        loads_after: int = 0,
        rows_in_view: bool = True,
        tab_opens: bool = True,
        records_list: bool = True,
        records_after: int = 0,
        exit_drifts: int = 0,
    ) -> None:
        self.reachable = reachable
        self.rows = rows
        self.exit_closes = exit_closes
        self.loads_after = loads_after
        self.rows_in_view = rows_in_view
        self.tab_opens = tab_opens
        self.records_list = records_list
        # Reads of the records screen that come back with the table still
        # empty: the menu's server fetching game-play history, which is the
        # one wait in the sequence that is somebody else's round-trip.
        self.records_after = records_after

        # Reads that come back with the Exit button's box somewhere other than
        # where the button *is*. Counted from the moment the menu comes *back*
        # -- the drawer sliding in after the game play view closes -- because
        # that is when it happens on the cabinet, and a rectangle read then is
        # stale before anything can click it.
        self.drifts_on_return = exit_drifts
        self.drifting = 0

        self.record_reads = 0
        self.screen = "home"
        self.clicked: list[str] = []
        self.reads = 0
        self.open = False
        self.sessions = 0

    # --- what the page is showing ---

    def elements(self) -> list[cdp.Element]:
        """One read of the page, as the service's poll sees it.

        Deliberately not the same as :meth:`drawn`: a read can catch the page
        mid-animation, and everything downstream of it is then working from a
        box the page has already moved on from."""
        self.reads += 1
        if not self.open:
            # The menu closed: the app resets to a route with nothing on it.
            return []
        if self.reads <= self.loads_after:
            # The page is up but React has not rendered yet.
            return [_element("", element_id="root", width=0, height=0, visible=False)]
        if self.drifting > 0:
            self.drifting -= 1
            return self.drawn(drifting=True)
        return self.drawn()

    def hit_chain(self, x: float, y: float) -> list[cdp.HitElement]:
        """What is under a point of the page *now* -- innermost first, like
        ``elementFromPoint`` and its ancestors. Always the settled page, never
        a remembered read: that difference is the whole point of asking."""
        under = sorted(
            (e for e in self.drawn(drifting=False) if _hits(e, x, y)),
            key=lambda element: element.area,
        )
        return [
            cdp.HitElement(tag=e.tag, element_id=e.element_id, text=e.text)
            for e in under
        ]

    def drawn(self, *, drifting: bool = False) -> list[cdp.Element]:
        """The page as it actually is, for clicking and hit-testing against."""
        if not self.open:
            return []

        # In the nav drawer, on every screen.
        found = [
            _element("Meters", element_id="Meters", y=300),
            _element("Events / History", element_id="Events / History", y=380),
            _element("Diagnostics", element_id="Diagnostics", y=450),
            # The Quick Links anchor reads the same as the nav button and has
            # no id: the id is what has to win.
            _element("Events / History", tag="a", x=400, y=308, width=140),
            _element("Game Play History", tag="a", x=400, y=350, width=170),
            # Something else that reads "Exit" and closes nothing -- a drawer
            # item above the real button, which is where one sits. It comes
            # first in DOM order deliberately: a text match takes the first of
            # its remaining candidates, so this is the one it would pick.
            _element("Exit", tag="div", x=70, y=520, width=60, height=24),
            # The button that closes the menu. Its id is **lower case** while
            # the label is "Exit", which is how the real page writes it -- so a
            # byte-for-byte id comparison misses it, falls through to the text,
            # and clicks the decoy above instead.
            _element(
                "Exit",
                element_id="exit",
                tag="button",
                # Mid-slide the drawer puts it somewhere else entirely, which
                # is what a read caught at that moment reports.
                x=700 if drifting else 100,
                y=880,
                width=95,
                height=45,
            ),
        ]
        if self.screen in {"events", "records"}:
            found.append(_element("Game Play", element_id="Game Play", x=430, y=180))
            found.append(_element("Events", element_id="Events", x=360, y=180))
        if self.screen == "records":
            self.record_reads += 1
        if self.screen == "records" and self.record_reads > self.records_after:
            for index in range(self.rows):
                row_y = 260.0 + index * 60.0
                # The row's panel carries the same text as the button in it,
                # and stretches well past it -- so its *centre* is not over the
                # button. Deliberate: a click aimed at the panel would land on
                # the row's empty space, which is what makes clicking the
                # innermost match a real requirement rather than a preference.
                found.append(
                    _element(
                        "View",
                        tag="div",
                        x=700,
                        y=row_y - 6,
                        width=320,
                        height=52,
                        in_viewport=self.rows_in_view,
                    )
                )
                found.append(
                    _element(
                        "View",
                        tag="button",
                        x=720,
                        y=row_y,
                        width=70,
                        height=34,
                        in_viewport=self.rows_in_view,
                    )
                )
        return found

    # --- what clicking does ---

    def click(self, x: float, y: float) -> None:
        """Deliver a click the way a browser does: to the innermost element
        under the point, not the first one that happens to contain it.

        This matters for the record rows, where a panel and the button inside
        it both cover the point -- a fake that handed the click to the panel
        would report a hit the real page would give to the button."""
        under = [element for element in self.drawn() if _hits(element, x, y)]
        if not under:
            self.clicked.append("page:nothing")
            return
        self._press(min(under, key=lambda element: element.area))

    def _press(self, element: cdp.Element) -> None:
        self.clicked.append(element.element_id or f"{element.tag}:{element.text}")
        if element.element_id == "Events / History" and self.tab_opens:
            self.screen = "events"
        elif element.element_id == "Game Play" and self.records_list:
            self.screen = "records"
        elif element.element_id == "exit" and self.exit_closes:
            self.open = False
            self.screen = "home"


class FakeCabinet:
    """The three windows, and how they react to clicks on screen."""

    def __init__(
        self,
        *,
        supported: bool = True,
        devtool: bool = True,
        connected: bool = False,
        can_interact: bool = True,
        menu_opens: bool = True,
        devtool_minimized: bool = False,
        connect_caption: str = "Connect",
        raises_after: int = 0,
        never_raises: bool = False,
        menu: FakeMenu | None = None,
        obs: FakeObs | None = None,
        game_focuses: bool = True,
        admin_focuses: bool = True,
        game: bool = True,
    ) -> None:
        self.supported = supported
        self.has_devtool = devtool
        self.connected = connected
        self.can_interact = can_interact
        self.menu_opens = menu_opens
        self.connect_caption = connect_caption
        self.menu = menu if menu is not None else FakeMenu()
        self.obs = obs if obs is not None else FakeObs()
        self.game_focuses = game_focuses
        self.admin_focuses = admin_focuses
        self.has_game = game

        self.devtool = win32.WindowInfo(
            hwnd=0x20780,
            title=DEVTOOL_TITLE,
            class_name=DEVTOOL_CLASS,
            minimized=devtool_minimized,
            client_width=DEVTOOL_CLIENT[0],
            client_height=DEVTOOL_CLIENT[1],
        )
        self.admin = win32.WindowInfo(
            hwnd=0x107EE,
            title=ADMIN_TITLE,
            class_name=ADMIN_CLASS,
            minimized=False,
            client_width=ADMIN_CLIENT[0],
            client_height=ADMIN_CLIENT[1],
        )
        self.game = win32.WindowInfo(
            hwnd=0x1082C,
            title=GAME_TITLE,
            class_name=GAME_CLASS,
            minimized=False,
            client_width=GAME_CLIENT[0],
            client_height=GAME_CLIENT[1],
        )

        self.pressed: list[str] = []
        """Captions and target names of everything this cabinet felt, in order."""

        self.points: list[tuple[int, int]] = []
        self._cursor = (0, 0)

        # How the real desktop behaves: these windows sit in the background,
        # and `bring_to_front` returns before the restack has happened.
        self.raises_after = raises_after
        self.never_raises = never_raises
        self.front: int | None = STRANGER_HWND if never_raises else None
        self.pending_front: int | None = None
        self.polls_until_front = 0
        self.fronted: list[int] = []

    @property
    def admin_open(self) -> bool:
        """The menu's window and its page open and close together."""
        return self.menu.open

    # --- the win32 surface the services use ---

    def is_supported(self) -> bool:
        return self.supported

    def find_window(
        self,
        *,
        title: str,
        class_name: str | None = None,
        class_prefix: str | None = None,
        visible_only: bool = False,
    ) -> win32.WindowInfo | None:
        wanted = title.casefold()
        for window, present in (
            (self.devtool, self.has_devtool),
            (self.admin, self.admin_open),
            (self.game, self.has_game),
        ):
            if not present:
                continue
            if wanted and wanted not in window.title.casefold():
                continue
            if class_name is not None and window.class_name != class_name:
                continue
            if class_prefix and not window.class_name.startswith(class_prefix):
                continue
            if visible_only and not window.visible:
                continue
            return window
        return None

    def describe(self, hwnd: int) -> win32.WindowInfo | None:
        for window in (self.devtool, self.admin, self.game):
            if window.hwnd == hwnd:
                return window
        return None

    def can_post(self, hwnd: int) -> bool:  # noqa: ARG002
        return self.can_interact

    def restore(self, hwnd: int) -> bool:
        if not self.can_interact:
            return False
        if hwnd == self.devtool.hwnd:
            self.devtool = dataclasses.replace(self.devtool, minimized=False)
        return True

    def descendants(self, hwnd: int) -> list[win32.ControlInfo]:
        """Only DevTool owns controls. The browser window and the Unity window
        draw their own, which is why neither is clicked by caption."""
        if hwnd != self.devtool.hwnd:
            return []
        return [
            _control("Test Button", (254, 219, 375, 242), enabled=True),
            _control("Attendant Key", ATTENDANT_RECT, enabled=self.connected),
            _control("Disconnect", (619, 131, 733, 165), enabled=self.connected),
            _control(self.connect_caption, CONNECT_RECT, enabled=not self.connected),
        ]

    def client_to_screen(self, hwnd: int, x: int, y: int) -> tuple[int, int]:
        origin = {
            self.devtool.hwnd: DEVTOOL_ORIGIN,
            self.admin.hwnd: ADMIN_ORIGIN,
            self.game.hwnd: GAME_ORIGIN,
        }[hwnd]
        return origin[0] + x, origin[1] + y

    def get_cursor_pos(self) -> tuple[int, int]:
        return self._cursor

    def set_cursor_pos(self, x: int, y: int) -> None:
        self._cursor = (x, y)

    def window_at(self, x: int, y: int) -> int:
        """Whichever window covers the point -- how injected input really lands.

        A window asked to come forward but not there yet answers as whatever is
        still on top, which is what makes the click path wait."""
        if self.pending_front is not None:
            if self.polls_until_front > 0:
                self.polls_until_front -= 1
            else:
                self.front = self.pending_front
                self.pending_front = None

        hit = self._window_over(x, y)
        if hit == 0:
            return 0
        if self.front is not None and hit != self.front:
            return self.front
        return hit

    def _window_over(self, x: int, y: int) -> int:
        for window, origin, client, present in (
            (self.devtool, DEVTOOL_ORIGIN, DEVTOOL_CLIENT, self.has_devtool),
            (self.admin, ADMIN_ORIGIN, ADMIN_CLIENT, self.admin_open),
            (self.game, GAME_ORIGIN, GAME_CLIENT, self.has_game),
        ):
            if not present:
                continue
            if (
                origin[0] <= x <= origin[0] + client[0]
                and origin[1] <= y <= origin[1] + client[1]
            ):
                return window.hwnd
        return 0

    def foreground_window(self) -> int:
        """Which window is *activated*, which is not the same question as which
        one covers a point -- see `window_ui.focus_window`."""
        if self.pending_front is not None:
            if self.polls_until_front > 0:
                self.polls_until_front -= 1
            else:
                self.front = self.pending_front
                self.pending_front = None
        return self.front or 0

    def bring_to_front(self, hwnd: int) -> bool:
        """Asked to raise a window. Honoured after `raises_after` polls, or
        never, which is a covered window nothing can shift."""
        self.fronted.append(hwnd)
        if hwnd == self.game.hwnd and not self.game_focuses:
            # Windows refusing a foreground change to the game.
            return False
        if hwnd == self.admin.hwnd and not self.admin_focuses:
            return False
        if self.never_raises:
            return False
        if self.raises_after <= 0:
            self.front = hwnd
            self.pending_front = None
            return True
        self.pending_front = hwnd
        self.polls_until_front = self.raises_after
        return False

    def inject_left_down(self) -> None:
        pass

    def inject_left_up(self) -> None:
        """React to wherever the cursor is, as the real windows do on release."""
        x, y = self._cursor
        self.points.append((x, y))
        hit = self.window_at(x, y)

        if hit == self.devtool.hwnd:
            for control in self.descendants(self.devtool.hwnd):
                at_x, at_y = control.center
                if abs(at_x - x) <= HIT_RADIUS and abs(at_y - y) <= HIT_RADIUS:
                    self._press(control.text)
                    return
            self.pressed.append("devtool:nothing")
            return

        if hit == self.admin.hwnd:
            # Nothing should click the menu's window on screen; if anything
            # does, a test has to be able to see it.
            self.pressed.append("admin-window:clicked")
            return

        if hit == self.game.hwnd:
            self._press_game(x, y)
            return

        self.pressed.append("nothing")

    def _press(self, caption: str) -> None:
        self.pressed.append(caption)
        if caption == self.connect_caption:
            self.connected = True
        elif caption == "Attendant Key" and self.connected and self.menu_opens:
            self.menu.open = True
            self.menu.screen = "home"
            self.menu.reads = 0

    def _press_game(self, x: int, y: int) -> None:
        at_x = GAME_ORIGIN[0] + round(EXIT_GAMEPLAY[0] * GAME_CLIENT[0])
        at_y = GAME_ORIGIN[1] + round(EXIT_GAMEPLAY[1] * GAME_CLIENT[1])
        if abs(at_x - x) <= HIT_RADIUS and abs(at_y - y) <= HIT_RADIUS:
            self.pressed.append("exit_gameplay")
            # Exiting the game play view puts the attendant menu back -- and
            # it comes back *drawing itself*, which is the moment a read of it
            # catches a box the page has already moved on from.
            if self.menu_opens:
                self.menu.open = True
                self.menu.screen = "records"
                self.menu.drifting = self.menu.drifts_on_return
            return
        self.pressed.append("game:nothing")


class FakeObs:
    """Stands in for the OBS service: connects, re-points its window capture,
    and hands back a screenshot.

    ``blank_until`` models the failure the design has to survive -- OBS reports
    a *successful write* of a frame it rendered nothing into, so the file has
    to be read back. ``writes_file`` models the other one: a shot that comes
    back inline with nothing on disk, which is nothing to probe *and* nothing
    to serve. It still returns ``image_data`` either way, because the real
    service always does -- and the record deliberately drops it.
    """

    def __init__(
        self,
        *,
        blank_until: int = 0,
        writes_file: bool = True,
        selects: bool = True,
        source: str = "FortuneOx Window",
    ) -> None:
        self.blank_until = blank_until
        self.writes_file = writes_file
        self.selects = selects
        self.source = source
        self.connects = 0
        self.selections = 0
        self.shots: list[str] = []

    async def connect(self) -> None:
        self.connects += 1

    async def select_current_game_window(self) -> ObsGameWindowSelection:
        if not self.selects:
            raise ObsRequestError("no window-capture source in the active scene")
        self.selections += 1
        return ObsGameWindowSelection(
            game=GAME_TITLE,
            process="FortuneOx.exe",
            scene="GamePlay",
            source_name="UnityGame",
            window_title=GAME_TITLE,
        )

    async def take_screenshot(self, payload: Any) -> ScreenshotResult:
        self.shots.append(payload.file_name)
        return ScreenshotResult(
            source_name=self.source,
            image_format=payload.image_format,
            image_data=f"data:image/png;base64,SHOT{len(self.shots)}",
            file_path=(
                str(Path(SHOT_DIR) / f"{payload.file_name}.png")
                if self.writes_file
                else None
            ),
        )

    def is_blank(self, path: Path) -> bool:  # noqa: ARG002 - stands in by name
        """The written copy, which is the one probed and the one served."""
        return len(self.shots) <= self.blank_until


class FakeSession:
    """Stands in for :class:`app.utils.cdp.Session`."""

    def __init__(self, menu: FakeMenu) -> None:
        self.menu = menu
        self.page = cdp.PageTarget(
            target_id="ABC123",
            title=ADMIN_TITLE,
            url=PAGE_URL,
            websocket_url="ws://127.0.0.1:9999/devtools/page/ABC123",
        )

    async def click(self, x: float, y: float) -> None:
        self.menu.click(x, y)


def install(monkeypatch: pytest.MonkeyPatch, cabinet: FakeCabinet) -> FakeCabinet:
    """Make ``cabinet`` the Win32 layer, and its menu the page layer."""
    for name in (
        "is_supported",
        "find_window",
        "describe",
        "can_post",
        "restore",
        "descendants",
        "client_to_screen",
        "get_cursor_pos",
        "set_cursor_pos",
        "window_at",
        "bring_to_front",
        "foreground_window",
        "inject_left_down",
        "inject_left_up",
    ):
        monkeypatch.setattr(win32, name, getattr(cabinet, name))

    monkeypatch.setattr(obs_service, "connect", cabinet.obs.connect)
    monkeypatch.setattr(
        obs_service,
        "select_current_game_window",
        cabinet.obs.select_current_game_window,
    )
    monkeypatch.setattr(obs_service, "take_screenshot", cabinet.obs.take_screenshot)
    monkeypatch.setattr(roi_service, "is_blank", cabinet.obs.is_blank)

    menu = cabinet.menu

    @contextlib.asynccontextmanager
    async def open_page(
        base_url: str,
        *,
        url_contains: str = "",
        timeout: float = 5.0,
    ) -> AsyncIterator[FakeSession]:
        # Arguments spelled out because this stands in for the real signature,
        # and unused because the fake is the browser.
        del base_url, url_contains, timeout
        if not menu.reachable:
            raise cdp.CdpError(
                f"No debuggable browser answered at {CDP_URL}/json/list: refused. "
                "The page is driven through its browser's debugging port."
            )
        menu.sessions += 1
        yield FakeSession(menu)

    async def elements(session: FakeSession) -> list[cdp.Element]:
        return session.menu.elements()

    async def hit_test(
        session: FakeSession, x: float, y: float
    ) -> list[cdp.HitElement]:
        return session.menu.hit_chain(x, y)

    monkeypatch.setattr(cdp, "open_page", open_page)
    monkeypatch.setattr(cdp, "elements", elements)
    monkeypatch.setattr(cdp, "hit_test", hit_test)
    return cabinet


@pytest.fixture
def replay_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Point both services at temp config and make every wait instant."""
    games = tmp_path / "games"
    games.mkdir()
    (games / f"{GAME_TITLE}.json").write_text(json.dumps(GAME_CONFIG), encoding="utf-8")
    monkeypatch.setattr(settings, "IDECK_GAME_CONFIG_DIR", games)
    save_active_game(settings.ideck_active_game_path, GAME_TITLE)

    monkeypatch.setattr(settings, "GAME_INPUT_WINDOW_TITLE", "")
    monkeypatch.setattr(settings, "GAME_INPUT_WINDOW_CLASS", GAME_CLASS)
    monkeypatch.setattr(settings, "GAME_INPUT_VERIFY_CLICKS", False)
    monkeypatch.setattr(settings, "GAME_INPUT_CLICK_HOLD_SECONDS", 0.0)

    monkeypatch.setattr(settings, "REPLAY_DEVTOOL_WINDOW_TITLE", DEVTOOL_TITLE)
    monkeypatch.setattr(
        settings, "REPLAY_DEVTOOL_WINDOW_CLASS_PREFIX", "WindowsForms10.Window"
    )
    monkeypatch.setattr(settings, "REPLAY_ADMIN_WINDOW_TITLE", ADMIN_TITLE)
    monkeypatch.setattr(
        settings, "REPLAY_ADMIN_WINDOW_CLASS_PREFIX", "Chrome_WidgetWin"
    )
    monkeypatch.setattr(settings, "REPLAY_ADMIN_CDP_URL", CDP_URL)
    monkeypatch.setattr(settings, "REPLAY_ADMIN_PAGE_URL_CONTAINS", "localhost:9002")
    monkeypatch.setattr(settings, "REPLAY_CLICK_HOLD_SECONDS", 0.0)
    monkeypatch.setattr(settings, "REPLAY_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(settings, "REPLAY_WINDOW_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(settings, "REPLAY_CONTROL_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(settings, "REPLAY_DOM_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(settings, "REPLAY_RECORDS_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(settings, "REPLAY_CDP_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(settings, "REPLAY_FOCUS_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(settings, "REPLAY_POLL_SECONDS", 0.0)
    monkeypatch.setattr(settings, "REPLAY_SCREENSHOT_WIDTH", 1280)
    monkeypatch.setattr(settings, "REPLAY_SCREENSHOT_SUBDIR", "replay")
    monkeypatch.setattr(settings, "REPLAY_SCREENSHOT_BLANK_RETRIES", 2)
    # The shipped ending: photograph the replay, then put the cabinet back.
    monkeypatch.setattr(settings, "REPLAY_EXIT_AFTER_SCREENSHOT", True)
    monkeypatch.setattr(settings, "REPLAY_GAME_EXIT_TARGET", "exit_gameplay")
    monkeypatch.setattr(settings, "REPLAY_GAME_SPIN_TARGET", "spin")
    monkeypatch.setattr(settings, "REPLAY_LOG_LIMIT", 400)
    monkeypatch.setattr(window_ui, "FOCUS_POLL_SECONDS", 0.0)
    yield


@pytest.fixture
def cabinet(monkeypatch: pytest.MonkeyPatch) -> FakeCabinet:
    return install(monkeypatch, FakeCabinet())


def steps_of(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """A run's steps, keyed for assertion."""
    return {step["key"]: step for step in data["steps"]}


def logged(data: dict[str, Any], text: str) -> bool:
    """Whether the run said something, anywhere in its log."""
    return any(text in entry["message"] for entry in data["logs"])


async def walk() -> dict[str, Any]:
    """Run the whole sequence and hand back its record the way the API does.

    Straight through `replay.run()` rather than over HTTP, because the endpoint
    deliberately does *not* wait: it starts a background walk so a caller can
    watch it. `run()` is the whole script and the thing worth testing here; the
    endpoint's own starting and polling is tested separately, further down.
    """
    return (await replay_service.run()).model_dump(mode="json")


async def status_of(client: AsyncClient) -> dict[str, Any]:
    """One poll of the endpoint a caller follows a run on."""
    response = await client.get("/api/replay/status")
    assert response.status_code == 200
    return assert_success(response.json())


async def until(
    client: AsyncClient, ready: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    """Poll the status endpoint until the live run satisfies `ready`.

    Bounded, because a test that hangs waiting for a background walk is worse
    than one that fails saying what it last saw."""
    last: dict[str, Any] | None = None
    for _ in range(500):
        data = await status_of(client)
        last = data["run"]
        if last is not None and ready(last):
            return last
        await asyncio.sleep(0)
    raise AssertionError(f"the run never got there; last saw {last}")


async def settled(client: AsyncClient) -> dict[str, Any]:
    """Poll until no sequence is walking, and hand back the whole status."""
    for _ in range(500):
        data = await status_of(client)
        if not data["running"]:
            return data
        await asyncio.sleep(0)
    raise AssertionError("the run never finished")


def write_png(path: Path) -> bytes:
    """A real PNG on disk, for the route that serves one."""
    Image.new("RGB", (4, 4), (30, 0, 50)).save(path)
    return path.read_bytes()


# --- the whole sequence ---------------------------------------------------


async def test_run_presses_every_button_in_order(
    replay_env: None, cabinet: FakeCabinet
) -> None:
    """The sequence is the deliverable, so this is the test that matters.

    Both halves are asserted by what was *felt* rather than by what the service
    says it sent: the cabinet records the clicks on windows, and the page
    records the clicks in it.
    """
    data = await walk()

    assert cabinet.pressed == ["Connect", "Attendant Key", "exit_gameplay"]
    assert cabinet.menu.clicked == [
        "Events / History",
        "Game Play",
        "button:View",
        "exit",
    ]
    assert data["state"] == "completed"
    assert [step["key"] for step in data["steps"]] == [
        "open-devtool",
        "connect",
        "attendant-key",
        "focus-menu",
        "events-history",
        "game-play",
        "view-latest",
        "focus-game",
        "screenshot",
        "exit-gameplay",
        "exit-attendant",
    ]
    assert all(step["state"] == "completed" for step in data["steps"]), data["steps"]
    # The picture was taken before either Exit, which is the ordering that
    # makes the whole thing worth running.
    assert data["screenshot"]["file_name"].startswith("replay-")
    # And the cabinet is back where it was found: out of the replay, out of
    # the menu.
    assert cabinet.menu.open is False


async def test_the_menus_window_is_never_clicked_on_screen(
    replay_env: None, cabinet: FakeCabinet
) -> None:
    """A page is driven through its DOM, not by aiming at its window.

    This is the whole reason there is nothing to measure. The window *is*
    raised -- so that a person can watch, and so a covered browser is not left
    for Chromium to throttle -- but no click is ever aimed at it, which is the
    part that would need a measurement."""
    await walk()

    assert "admin-window:clicked" not in cabinet.pressed
    assert "page:nothing" not in cabinet.menu.clicked
    # Raised, but by the step whose whole job that is, and never clicked.
    assert cabinet.admin.hwnd in cabinet.fronted


async def test_every_step_proves_its_click_except_the_one_that_cannot(
    replay_env: None, cabinet: FakeCabinet
) -> None:
    """Reading the page back is what turns a sent click into a confirmed one.

    *View* is the exception and says so: what it starts happens in the game's
    window, which the menu's page knows nothing about."""
    steps = steps_of(await walk())

    for key in (
        "connect",
        "attendant-key",
        "events-history",
        "game-play",
        "focus-game",
        "screenshot",
    ):
        assert steps[key]["confirmed"] is True, key
    assert steps["view-latest"]["confirmed"] is False


# --- how a button is found ------------------------------------------------


async def test_an_id_is_preferred_to_matching_text(
    replay_env: None, cabinet: FakeCabinet
) -> None:
    """The nav button and the Quick Links anchor both read *Events / History*.

    The nav button carries ``id="Events / History"``, so the id settles it --
    exactly, and without depending on which the page happens to list first."""
    steps = steps_of(await walk())

    assert cabinet.menu.clicked[0] == "Events / History"
    assert "found by id" in steps["events-history"]["detail"]
    assert "found by id" in steps["game-play"]["detail"]


async def test_a_button_with_no_id_is_found_by_its_text(
    replay_env: None, cabinet: FakeCabinet
) -> None:
    """*View* is a plain MUI button with no id, so text is all there is."""
    steps = steps_of(await walk())

    assert "found by text" in steps["view-latest"]["detail"]
    assert cabinet.menu.clicked[2] == "button:View"


async def test_the_innermost_element_is_clicked_not_the_panel_around_it(
    replay_env: None, cabinet: FakeCabinet
) -> None:
    """Each record row wraps its *View* in a panel that reads the same.

    Clicking the wrapper is how a click lands on padding, so the narrowing has
    to keep the button -- which the fake proves by recording the tag it felt."""
    await walk()

    assert "button:View" in cabinet.menu.clicked
    assert "div:View" not in cabinet.menu.clicked


async def test_the_newest_record_is_the_first_of_several(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every row carries the same label, so the count is reported: "first of
    five" is a claim a reader can check, where "clicked View" is not."""
    cabinet = install(monkeypatch, FakeCabinet(menu=FakeMenu(rows=5)))
    steps = steps_of(await walk())

    assert "first of 5" in steps["view-latest"]["detail"]
    assert cabinet.menu.clicked.count("button:View") == 1


async def test_a_row_scrolled_out_of_view_is_refused_rather_than_clicked(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CSS-visible is not the same as on screen.

    A row below the fold is drawn, and its coordinates belong to whatever *is*
    at that point in the viewport -- so clicking there would press something
    else entirely. Nothing is clicked instead."""
    cabinet = install(monkeypatch, FakeCabinet(menu=FakeMenu(rows_in_view=False)))
    steps = steps_of(await walk())

    assert steps["view-latest"]["state"] == "failed"
    assert steps["view-latest"]["error_code"] == "REPLAY_CONTROL_NOT_FOUND"
    assert "scrolled out of view" in steps["view-latest"]["error"]
    assert "button:View" not in cabinet.menu.clicked


# --- waiting for the page -------------------------------------------------


async def test_the_page_is_waited_for_rather_than_timed(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The menu's window opens well before the React app inside it renders.

    Asking the page whether the button is there yet replaces guessing at a
    grace period: a menu that takes several polls to come up has to produce an
    identical run, with nothing clicked into the empty page on the way."""
    cabinet = install(monkeypatch, FakeCabinet(menu=FakeMenu(loads_after=5)))
    data = await walk()

    assert data["state"] == "completed"
    assert "page:nothing" not in cabinet.menu.clicked
    assert cabinet.menu.clicked[0] == "Events / History"


async def test_a_button_that_never_appears_says_what_the_page_was_showing(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A step that cannot find its button is either on the wrong page or
    looking for a renamed one, and only the labels say which."""
    install(monkeypatch, FakeCabinet(menu=FakeMenu(tab_opens=False)))
    steps = steps_of(await walk())

    assert steps["events-history"]["state"] == "failed"
    assert steps["events-history"]["error_code"] == "REPLAY_STEP_NOT_CONFIRMED"
    assert "Game Play" in steps["events-history"]["error"]
    # The labels that *were* there, so a reader can see what it was looking at.
    assert "Diagnostics" in steps["events-history"]["error"]


async def test_a_tab_that_lists_no_records_fails_the_game_play_step(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, FakeCabinet(menu=FakeMenu(records_list=False)))
    steps = steps_of(await walk())

    assert steps["game-play"]["state"] == "failed"
    assert "'View'" in steps["game-play"]["error"]
    assert steps["view-latest"]["state"] == "pending"


async def test_an_unreachable_page_names_the_debugging_port(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The menu is driven through its browser's debugging port, so a browser
    started without one is a deployment fact, not something to retry."""
    install(monkeypatch, FakeCabinet(menu=FakeMenu(reachable=False)))
    data = await walk()
    steps = steps_of(data)

    assert data["state"] == "failed"
    assert steps["events-history"]["error_code"] == "REPLAY_MENU_UNREACHABLE"
    assert "debugging port" in steps["events-history"]["error"]
    # The three window steps before it still ran, and still worked.
    assert steps["attendant-key"]["state"] == "completed"


async def test_one_page_session_serves_the_whole_run(
    replay_env: None, cabinet: FakeCabinet
) -> None:
    """Reconnecting per click would be four chances to lose the page."""
    await walk()
    assert cabinet.menu.sessions == 1


# --- Connect is idempotent ------------------------------------------------


async def test_connect_is_skipped_when_the_hub_is_already_connected(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipped, not failed, and not pressed: a connected DevTool greys Connect
    out and lights Attendant Key up, and that state is readable."""
    cabinet = install(monkeypatch, FakeCabinet(connected=True))
    data = await walk()
    steps = steps_of(data)

    assert steps["connect"]["state"] == "skipped"
    assert "already connected" in steps["connect"]["detail"]
    assert "Connect" not in cabinet.pressed
    assert data["state"] == "completed"


async def test_a_renamed_connect_button_says_what_was_there_instead(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, FakeCabinet(connect_caption="Attach"))
    steps = steps_of(await walk())

    assert steps["connect"]["state"] == "failed"
    assert steps["connect"]["error_code"] == "REPLAY_CONTROL_NOT_FOUND"
    assert "Attach" in steps["connect"]["error"]


async def test_captions_match_past_whitespace_and_accelerators(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WinForms captions carry ``&`` accelerators and whatever spacing a
    designer typed; neither changes which button a human means."""
    cabinet = install(monkeypatch, FakeCabinet(connect_caption="&Connect "))
    await walk()
    assert "&Connect " in cabinet.pressed


# --- the windows the sequence does click ----------------------------------


async def test_missing_devtool_names_what_to_launch(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, FakeCabinet(devtool=False))
    data = await walk()
    steps = steps_of(data)

    assert data["state"] == "failed"
    assert steps["open-devtool"]["error_code"] == "REPLAY_WINDOW_NOT_FOUND"
    assert "DevTool.exe" in steps["open-devtool"]["error"]


async def test_a_failure_leaves_the_rest_of_the_sequence_pending(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unreached is a different fact from skipped, and both differ from
    failed -- so every step is on the record either way."""
    install(monkeypatch, FakeCabinet(menu_opens=False))
    steps = steps_of(await walk())

    assert steps["attendant-key"]["state"] == "failed"
    assert steps["attendant-key"]["error_code"] == "REPLAY_STEP_NOT_CONFIRMED"
    for later in ("events-history", "game-play", "view-latest", "screenshot"):
        assert steps[later]["state"] == "pending"
        assert steps[later]["error"] is None


async def test_windows_blocking_input_explains_the_integrity_level(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cabinet tools really do run elevated on these machines, so this is
    the common failure -- and its fix is a deployment detail, not a retry."""
    install(monkeypatch, FakeCabinet(can_interact=False))
    steps = steps_of(await walk())

    assert steps["open-devtool"]["error_code"] == "REPLAY_ACCESS_DENIED"
    assert "elevated" in steps["open-devtool"]["error"]


async def test_a_background_window_is_raised_and_waited_for(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """These windows sit *behind* something, and `bring_to_front` returns
    before the desktop has restacked.

    Clicking on the strength of that return value drives whatever is still on
    top, so the click path waits for the window to really be under the cursor.
    A run where every raise takes several polls has to come out identical."""
    cabinet = install(monkeypatch, FakeCabinet(raises_after=3))
    data = await walk()

    assert data["state"] == "completed"
    assert cabinet.pressed[:2] == ["Connect", "Attendant Key"]
    assert "devtool:nothing" not in cabinet.pressed


async def test_a_window_that_will_not_come_forward_refuses_the_click(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Waiting is not the same as assuming: something holding the foreground
    still has to stop the click rather than have it fire into that window."""
    cabinet = install(monkeypatch, FakeCabinet(never_raises=True, raises_after=99))
    steps = steps_of(await walk())

    assert steps["connect"]["error_code"] == "REPLAY_WINDOW_NOT_FOUND"
    assert "holding the foreground" in steps["connect"]["error"]
    assert cabinet.pressed == []


async def test_a_minimized_devtool_is_restored_rather_than_refused(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    cabinet = install(monkeypatch, FakeCabinet(devtool_minimized=True))
    data = await walk()

    assert data["state"] == "completed"
    assert cabinet.devtool.minimized is False


# --- the replay is left on screen, and pictured -------------------------


async def test_the_game_is_brought_to_the_front_before_the_screenshot(
    replay_env: None, cabinet: FakeCabinet
) -> None:
    """The replay starts *behind* the attendant menu, so the point of the step
    is that someone watching sees it -- and that OBS captures a window which
    is not covered."""
    steps = steps_of(await walk())

    assert cabinet.foreground_window() == cabinet.game.hwnd
    assert steps["focus-game"]["confirmed"] is True
    assert steps["focus-game"]["target"] == GAME_TITLE
    # Asked for *after* the game came forward, not before.
    assert cabinet.obs.shots


async def test_the_screenshot_is_on_the_run_for_the_card_to_show(
    replay_env: None, cabinet: FakeCabinet
) -> None:
    """One picture of one moment, and the thing a reader of a replay wants --
    so it is a block on the run rather than a field of the step that took it."""
    data = await walk()

    shot = data["screenshot"]
    assert shot is not None
    assert shot["source_name"] == "FortuneOx Window"
    # No data URI on the record: it is polled while the run walks on, and the
    # picture is served from the file that was probed. Carrying both would let
    # the shown copy and the checked copy be different pictures, which is
    # exactly how a card displays a black rectangle over a passing step.
    assert "image_data" not in shot
    assert shot["blank"] is False
    assert shot["attempts"] == 1
    assert shot["file_name"].startswith("replay-")
    assert cabinet.obs.connects == 1


async def test_devtool_is_focused_before_every_click_on_it(
    replay_env: None, cabinet: FakeCabinet
) -> None:
    """Not once at the start: before *each* of its clicks.

    A minimized DevTool reports its control rectangles at -32000, so a click
    aimed at one goes nowhere near the button -- and the window can be
    minimized at any point, not only before the run. Pressing the attendant key
    also hands the foreground to the menu."""
    await walk()

    # Three DevTool steps, each of which raised it: open, Connect, Attendant Key.
    assert cabinet.fronted.count(cabinet.devtool.hwnd) >= 3


async def test_a_minimized_devtool_is_restored_before_its_controls_are_read(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case that forces the focus: a minimized window's controls are
    off-screen, so reading them before restoring it aims at nothing."""
    cabinet = install(monkeypatch, FakeCabinet(devtool_minimized=True))
    data = await walk()

    assert data["state"] == "completed"
    assert cabinet.devtool.minimized is False
    assert cabinet.pressed[:2] == ["Connect", "Attendant Key"]


async def test_the_obs_window_capture_is_pointed_at_the_game_first(
    replay_env: None, cabinet: FakeCabinet
) -> None:
    """Without this the shot is of whatever the source was last aimed at --
    which comes back a plausible-looking picture of the wrong thing."""
    steps = steps_of(await walk())

    assert cabinet.obs.selections == 1
    assert "re-pointed" in steps["screenshot"]["detail"]


async def test_a_scene_with_no_window_source_still_gets_a_screenshot(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is the *aim* that is unverified, not the capture, so a scene that
    cannot be re-pointed is noted rather than fatal."""
    install(monkeypatch, FakeCabinet(obs=FakeObs(selects=False)))
    data = await walk()

    assert data["state"] == "completed"
    assert data["screenshot"]["file_name"].startswith("replay-")
    assert "re-pointed" not in (steps_of(data)["screenshot"]["detail"] or "")


async def test_the_picture_shown_is_the_picture_that_was_probed(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this is here for, measured on the real OBS.

    ``GetSourceScreenshot`` (the data URI) and ``SaveSourceScreenshot`` (the
    file) are two calls, and the first after a connect or a re-point hands back
    an all-black frame while the file written a moment later holds the real
    picture. Checking one and showing the other is how a card ends up
    displaying a black rectangle over a step that passed -- so the record
    carries a *file name* and nothing else, and that file is both what is
    probed and what gets served."""
    cabinet = install(monkeypatch, FakeCabinet(obs=FakeObs(blank_until=1)))
    data = await walk()
    shot = data["screenshot"]

    assert len(cabinet.obs.shots) == 2
    assert shot["attempts"] == 2
    assert shot["blank"] is False
    # The name is the whole of the picture on the record, and it is the second
    # shot -- the one that came back with something in it.
    assert shot["file_name"] == f"{cabinet.obs.shots[-1]}.png"
    assert shot["file_path"].endswith(shot["file_name"])
    assert data["state"] == "completed"


async def test_a_blank_screenshot_is_read_back_and_retried(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OBS reports a *successful write* of a frame it rendered nothing into, so
    the file is probed rather than trusted -- the lesson `analyze_spin` learned
    about its own captures."""
    cabinet = install(monkeypatch, FakeCabinet(obs=FakeObs(blank_until=2)))
    data = await walk()

    assert data["state"] == "completed"
    assert data["screenshot"]["blank"] is False
    assert data["screenshot"]["attempts"] == 3
    assert len(cabinet.obs.shots) == 3


async def test_a_screenshot_that_stays_blank_fails_its_step_without_raising(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A black picture of a replay is worse than none, because it looks like a
    result -- so it is marked, the step fails, and the run still finishes."""
    install(monkeypatch, FakeCabinet(obs=FakeObs(blank_until=99)))
    data = await walk()
    steps = steps_of(data)

    assert steps["screenshot"]["state"] == "failed"
    assert steps["screenshot"]["confirmed"] is False
    assert "empty" in steps["screenshot"]["error"]
    assert data["screenshot"]["blank"] is True
    # The verdict comes from the steps, not only from something raising.
    assert data["state"] == "failed"
    # And the earlier steps still stand.
    assert steps["view-latest"]["state"] == "completed"


async def test_a_game_windows_refuses_to_come_forward_without_failing(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows can refuse a foreground change. The replay is running either
    way, so the screenshot is still worth taking -- reported, not failed.

    Exiting is turned off because the in-game Exit is a click *at a point*, and
    a point in a window that could not be raised is a different failure with
    its own test; this one is about the focus step's own verdict."""
    monkeypatch.setattr(settings, "REPLAY_EXIT_AFTER_SCREENSHOT", False)
    cabinet = install(monkeypatch, FakeCabinet(game_focuses=False))
    data = await walk()
    steps = steps_of(data)

    assert steps["focus-game"]["state"] == "completed"
    assert steps["focus-game"]["confirmed"] is False
    assert "kept the foreground elsewhere" in steps["focus-game"]["detail"]
    assert data["state"] == "completed"
    assert cabinet.obs.shots


async def test_a_closed_game_window_fails_the_focus_step(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, FakeCabinet(game=False))
    steps = steps_of(await walk())

    assert steps["focus-game"]["state"] == "failed"
    assert steps["focus-game"]["error_code"] == "REPLAY_WINDOW_NOT_FOUND"
    assert steps["screenshot"]["state"] == "pending"


# --- how a run ends -----------------------------------------------------


async def test_the_screenshot_is_taken_before_either_exit(
    replay_env: None, cabinet: FakeCabinet
) -> None:
    """The ordering the whole sequence exists for.

    Exit is pressed at a point measured off a replay, so the picture has to be
    taken while that replay is still on screen -- and it lands on the record
    there too, rather than at the end, so it can be looked at while the run is
    still putting the cabinet back."""
    data = await walk()
    steps = steps_of(data)

    assert steps["screenshot"]["finished_at"] <= steps["exit-gameplay"]["started_at"]
    assert data["screenshot"]["blank"] is False
    assert cabinet.menu.open is False


async def test_the_exit_steps_are_absent_when_exiting_is_turned_off(
    replay_env: None, cabinet: FakeCabinet, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not listed as skipped: they are not part of what was asked for, and
    `pending` on a finished run has to keep meaning *unreached*."""
    monkeypatch.setattr(settings, "REPLAY_EXIT_AFTER_SCREENSHOT", False)
    data = await walk()
    keys = {step["key"] for step in data["steps"]}

    assert "exit-gameplay" not in keys
    assert "exit-attendant" not in keys
    assert "exit_gameplay" not in cabinet.pressed
    assert "exit" not in cabinet.menu.clicked
    # Left as the replay is being watched: the game in front, the menu open.
    assert cabinet.foreground_window() == cabinet.game.hwnd
    assert cabinet.menu.open is True


async def test_a_menu_that_will_not_close_fails_the_last_step(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The menu closing is the one thing the ending insists on: a run that
    leaves the cabinet sitting in the attendant menu has not finished."""
    install(monkeypatch, FakeCabinet(menu=FakeMenu(exit_closes=False)))
    data = await walk()
    steps = steps_of(data)

    assert steps["exit-attendant"]["state"] == "failed"
    assert steps["exit-attendant"]["error_code"] == "REPLAY_STEP_NOT_CONFIRMED"
    assert data["state"] == "failed"


# --- refusals to start ----------------------------------------------------


async def test_a_second_run_is_refused_while_one_is_in_progress(
    client: AsyncClient,
    replay_env: None,
    cabinet: FakeCabinet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two sequences would fight over the cursor and the foreground window."""
    monkeypatch.setattr(replay_service, "_running", True)
    response = await client.post("/api/replay/run")

    assert response.status_code == 409
    assert_failure(response.json(), code="REPLAY_ALREADY_RUNNING")
    assert cabinet.pressed == []


async def test_a_host_without_the_windows_api_refuses_to_start(
    client: AsyncClient, replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, FakeCabinet(supported=False))
    response = await client.post("/api/replay/run")

    assert response.status_code == 503
    assert_failure(response.json(), code="SERVICE_UNAVAILABLE")


# --- status ---------------------------------------------------------------


async def test_status_lists_the_windows_and_their_captions(
    client: AsyncClient, replay_env: None, cabinet: FakeCabinet
) -> None:
    """A step that cannot find *Attendant Key* is either a missing window or a
    renamed button, and only the list says which."""
    response = await client.get("/api/replay/status")
    data = assert_success(response.json())
    windows = {window["window"]: window for window in data["windows"]}

    assert windows["devtool"]["state"] == "ready"
    assert "Attendant Key" in windows["devtool"]["controls"]
    # Closed while no attendant menu is open, which is the normal state.
    assert windows["system-admin"]["state"] == "not_found"
    # A browser window and a Unity canvas own no controls; that is why neither
    # is clicked by caption.
    assert windows["game"]["controls"] == []
    assert data["running"] is False
    assert data["game_exit_configured"] is True


async def test_status_reports_a_closed_menu_as_a_state_not_a_failure(
    client: AsyncClient, replay_env: None, cabinet: FakeCabinet
) -> None:
    """The menu is closed for most of the day, and a closed menu has no page."""
    response = await client.get("/api/replay/status")
    data = assert_success(response.json())

    assert data["menu"]["reachable"] is True
    assert data["menu"]["labels"] == []
    assert data["menu"]["cdp_url"] == CDP_URL


async def test_status_lists_the_labels_an_open_menu_is_showing(
    client: AsyncClient, replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The counterpart of a window's control captions: what a step that could
    not find its button was looking at."""
    menu = FakeMenu()
    menu.open = True
    install(monkeypatch, FakeCabinet(menu=menu))

    response = await client.get("/api/replay/status")
    data = assert_success(response.json())

    assert data["menu"]["reachable"] is True
    assert "Events / History" in data["menu"]["labels"]
    assert data["menu"]["page_title"] == ADMIN_TITLE
    assert data["menu"]["label_count"] == len(data["menu"]["labels"])


async def test_status_reports_an_unreachable_browser_without_failing(
    client: AsyncClient, replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, FakeCabinet(menu=FakeMenu(reachable=False)))
    response = await client.get("/api/replay/status")
    data = assert_success(response.json())

    assert data["menu"]["reachable"] is False
    assert "debuggable browser" in data["menu"]["error"]


async def test_status_reports_a_game_with_no_exit_target(
    client: AsyncClient,
    replay_env: None,
    cabinet: FakeCabinet,
    tmp_path: Path,
) -> None:
    """The in-game Exit is a target in the game config like any other, so a
    game that has not had it measured is a readable state rather than a
    surprise seven steps in."""
    without = dict(GAME_CONFIG)
    without["button_targets"] = {"take_win": [0.07, 0.97]}
    (tmp_path / "games" / f"{GAME_TITLE}.json").write_text(
        json.dumps(without), encoding="utf-8"
    )
    game_input_service.reset_game_config()

    response = await client.get("/api/replay/status")
    data = assert_success(response.json())
    assert data["game_exit_configured"] is False


async def test_status_says_whether_a_run_will_exit(
    client: AsyncClient, replay_env: None, cabinet: FakeCabinet
) -> None:
    """Which ending a run will have, and both replay-only targets it needs for
    it -- so a card can say what pressing the button is about to do."""
    response = await client.get("/api/replay/status")
    data = assert_success(response.json())

    assert data["exits_after_screenshot"] is True
    assert data["game_exit_target"] == "exit_gameplay"
    assert data["game_exit_configured"] is True
    # Never pressed by a run, and reported anyway: it is measured in the same
    # place, off the same frame, and exists only in the same state.
    assert data["game_spin_target"] == "spin"
    assert data["game_spin_configured"] is False


# --- following a run while it runs ---------------------------------------


async def test_the_menu_window_is_raised_before_its_page_is_driven(
    replay_env: None, cabinet: FakeCabinet
) -> None:
    """Asked for by the script, and worth having for two reasons that are not
    about the clicks: somebody watching should see the menu being walked, and a
    covered browser window is one Chromium may throttle."""
    data = await walk()
    steps = steps_of(data)

    assert steps["focus-menu"]["state"] == "completed"
    assert steps["focus-menu"]["confirmed"] is True
    assert cabinet.admin.hwnd in cabinet.fronted
    # Raised before the first click into the page, not after it.
    assert steps["focus-menu"]["finished_at"] <= steps["events-history"]["started_at"]


async def test_a_menu_window_that_will_not_come_forward_does_not_fail_the_run(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every remaining click goes into the page, which does not care what the
    desktop is doing -- so a refused foreground change is reported, not fatal.
    Failing here would throw a run away over the part that is decoration."""
    install(monkeypatch, FakeCabinet(admin_focuses=False))
    data = await walk()
    steps = steps_of(data)

    assert steps["focus-menu"]["state"] == "completed"
    assert steps["focus-menu"]["confirmed"] is False
    assert "kept the foreground elsewhere" in steps["focus-menu"]["detail"]
    assert data["state"] == "completed"


async def test_the_records_are_waited_for_rather_than_assumed(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening the Game Play tab sends the menu's server off to fetch history,
    so the table is empty for a while after the click lands. The page is asked
    whether a row has arrived, which is why a slow fetch costs its own time and
    nothing else -- and why the run does not click into an empty table."""
    cabinet = install(monkeypatch, FakeCabinet(menu=FakeMenu(records_after=3)))
    data = await walk()
    steps = steps_of(data)

    assert steps["game-play"]["state"] == "completed"
    assert "3 'View' buttons on it" in steps["game-play"]["detail"]
    assert logged(data, "waiting up to")
    # It really did read the empty table before the full one.
    assert cabinet.menu.record_reads > 3
    assert data["state"] == "completed"


async def test_a_record_list_that_never_arrives_says_which_of_the_two_it_was(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tab with no rows is either a server that did not answer or a cabinet
    with no history to replay, and the step cannot tell -- so it names both
    rather than picking one."""
    install(monkeypatch, FakeCabinet(menu=FakeMenu(records_after=10_000)))
    data = await walk()
    steps = steps_of(data)

    assert steps["game-play"]["state"] == "failed"
    assert "did not answer in time" in steps["game-play"]["error"]
    assert "no game-play history" in steps["game-play"]["error"]
    assert steps["view-latest"]["state"] == "pending"


async def test_the_run_logs_every_step_as_it_happens(
    replay_env: None, cabinet: FakeCabinet
) -> None:
    """The live log is the progress report, so every step has to be in it --
    which is why it is written by the step wrapper rather than by each step
    remembering to announce itself."""
    data = await walk()

    assert data["logs"]
    assert [entry["sequence"] for entry in data["logs"]] == list(
        range(len(data["logs"]))
    )
    for step in data["steps"]:
        assert any(entry["step"] == step["key"] for entry in data["logs"]), step["key"]
    # The run's own lines carry no step: they are about the run, not a step.
    assert data["logs"][0]["step"] is None
    assert data["logs"][0]["level"] == "info"
    assert "starting: 11 steps" in data["logs"][0]["message"]
    assert "finished in" in data["logs"][-1]["message"]


async def test_the_log_is_capped_but_keeps_its_numbering(
    replay_env: None, cabinet: FakeCabinet, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Held in memory and polled, so the oldest lines go. The numbering comes
    off a counter rather than the list's length, so dropping them does not
    renumber the lines a poller has already shown."""
    monkeypatch.setattr(settings, "REPLAY_LOG_LIMIT", 6)
    data = await walk()
    sequences = [entry["sequence"] for entry in data["logs"]]

    assert len(data["logs"]) == 6
    assert sequences[0] > 0, "the lines kept are the newest ones"
    assert sequences == list(range(sequences[0], sequences[0] + 6))


async def test_a_retaken_screenshot_says_so_at_warning_level(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retry is load-bearing, so it is worth being able to see that it
    happened on a run that otherwise looks unremarkable."""
    install(monkeypatch, FakeCabinet(obs=FakeObs(blank_until=1)))
    data = await walk()
    warnings = [entry for entry in data["logs"] if entry["level"] == "warning"]

    assert any("came back empty; retaking it" in entry["message"] for entry in warnings)
    assert data["state"] == "completed"


async def test_the_endpoint_starts_the_walk_rather_than_waiting_for_it(
    client: AsyncClient, replay_env: None, cabinet: FakeCabinet
) -> None:
    """The sequence takes tens of seconds, most of them somebody else's wait,
    so the request answers with the run at step one and the caller follows it."""
    response = await client.post("/api/replay/run")
    assert response.status_code == 200
    started = assert_success(response.json())

    assert started["state"] == "running"
    assert started["finished_at"] is None
    assert len(started["steps"]) == 11
    assert all(step["state"] == "pending" for step in started["steps"])
    assert started["screenshot"] is None

    finished = (await settled(client))["run"]
    assert finished["run_id"] == started["run_id"]
    assert finished["state"] == "completed"
    assert cabinet.menu.open is False


async def test_status_carries_the_run_and_leaves_its_page_alone(
    client: AsyncClient, replay_env: None, cabinet: FakeCabinet
) -> None:
    """One endpoint answers both questions, so following a run is polling the
    thing that was already there. While one walks, the menu is *not* re-read:
    reading every label off the DOM once a second, to answer what the run's own
    log is answering, is work on the machine driving the cabinet."""
    before = await status_of(client)
    assert before["run"] is None
    assert before["menu"]["probed"] is True

    await client.post("/api/replay/run")
    during = await status_of(client)
    assert during["running"] is True
    assert during["run"]["state"] == "running"
    assert during["menu"]["probed"] is False
    assert during["menu"]["labels"] == []

    after = await settled(client)
    assert after["run"]["state"] == "completed"
    assert after["menu"]["probed"] is True


async def test_the_screenshot_is_on_the_record_before_the_run_ends(
    client: AsyncClient,
    replay_env: None,
    cabinet: FakeCabinet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ask this was built for: the picture shows up in the card when it is
    taken, not when the sequence finishes -- and the sequence goes on to press
    two more buttons after taking it."""
    gate = asyncio.Event()
    exiting = replay_service._exit_gameplay

    async def held(run: Any) -> None:
        await gate.wait()
        await exiting(run)

    monkeypatch.setattr(replay_service, "_exit_gameplay", held)
    await client.post("/api/replay/run")
    run = await until(client, lambda record: record["screenshot"] is not None)

    assert run["state"] == "running"
    assert run["screenshot"]["file_name"].startswith("replay-")
    assert steps_of(run)["exit-gameplay"]["state"] in {"pending", "running"}

    gate.set()
    assert (await settled(client))["run"]["state"] == "completed"


async def test_a_second_run_is_refused_the_moment_the_first_one_starts(
    client: AsyncClient, replay_env: None, cabinet: FakeCabinet
) -> None:
    """Not only while it is deep in the sequence: the flag is set before the
    walk is scheduled, with no await in between, which is the whole of the
    mutual exclusion."""
    first = await client.post("/api/replay/run")
    second = await client.post("/api/replay/run")

    assert first.status_code == 200
    assert second.status_code == 409
    assert_failure(second.json(), code="REPLAY_ALREADY_RUNNING")
    await settled(client)


async def test_the_screenshot_is_served_as_a_file(
    client: AsyncClient,
    replay_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An <img> src cannot unwrap the envelope, and a data URI on a record that
    is polled would cost more per poll than the sequence it reports."""
    monkeypatch.setattr(settings, "OBS_SCREENSHOT_DIR", tmp_path)
    directory = tmp_path / "replay"
    directory.mkdir()
    written = write_png(directory / "replay-abc123-1.png")

    response = await client.get("/api/replay/screenshot/replay-abc123-1.png")

    assert response.status_code == 200
    assert response.content == written


async def test_a_screenshot_name_from_a_url_cannot_escape_its_directory(
    client: AsyncClient,
    replay_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The name comes off a URL, and a filename joined to a capture directory
    is still a perfectly good path to somewhere else."""
    monkeypatch.setattr(settings, "OBS_SCREENSHOT_DIR", tmp_path)
    write_png(tmp_path / "secret.png")

    # A separator, and a bare name that resolves inside the directory but to
    # nothing: both 404, and neither reaches for the file one level up.
    for name in ("..%5Csecret.png", "secret"):
        response = await client.get(f"/api/replay/screenshot/{name}")
        assert response.status_code == 404, name
        assert_failure(response.json(), code="REPLAY_SCREENSHOT_NOT_FOUND")

    # The forms a URL path cannot carry as far as the route, asked of the
    # service directly rather than left untested.
    for name in ("..", "../secret.png", r"C:\Windows\win.ini"):
        with pytest.raises(ReplayScreenshotNotFoundError):
            replay_service.screenshot_path(name)


async def test_the_menus_exit_is_found_by_its_id_not_by_the_text(
    replay_env: None, cabinet: FakeCabinet
) -> None:
    """The failure this is here for, measured on the real menu.

    That page writes ``id="exit"`` on the button and *also* has something else
    reading "Exit" -- so an id comparison that insisted on the case missed it,
    fell through to the text, and clicked the thing that closes nothing. The
    step then failed saying the menu was still open, which was true and not the
    problem. An id differing from its label only in case is still the exact
    answer.
    """
    data = await walk()
    steps = steps_of(data)

    assert steps["exit-attendant"]["state"] == "completed"
    assert "found by id" in steps["exit-attendant"]["detail"]
    assert cabinet.menu.clicked[-1] == "exit"
    assert cabinet.menu.open is False


async def test_a_menu_exit_that_resolved_by_text_says_so_when_it_fails(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Because resolving by text is the half that was wrong, and a step that
    reports only "the menu is still open" sends a reader to the wrong place."""
    monkeypatch.setattr(settings, "REPLAY_EXIT_LABEL", "Exit")
    install(monkeypatch, FakeCabinet(menu=FakeMenu(exit_closes=False)))
    data = await walk()
    error = steps_of(data)["exit-attendant"]["error"]

    assert "found by id" in error
    assert "REPLAY_EXIT_LABEL" in error


async def test_a_click_waits_for_the_page_to_stop_moving(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure this is here for, measured on the real menu.

    Leaving the game play view brings the attendant menu back, and its drawer
    is still sliding in when the page is read -- so the Exit button's box is
    hundreds of pixels from where it settles. Every layer then reports success:
    the element was found, by its id, and the click was dispatched without
    error. Only the step's own confirmation catches it, twenty seconds later,
    saying the menu is still open -- which is true and says nothing about why.
    """
    cabinet = install(monkeypatch, FakeCabinet(menu=FakeMenu(exit_drifts=1)))
    data = await walk()

    assert steps_of(data)["exit-attendant"]["state"] == "completed"
    assert cabinet.menu.clicked[-1] == "exit"
    assert cabinet.menu.open is False
    assert logged(data, "not yet at its own coordinates")


async def test_a_page_that_never_settles_refuses_the_click(
    replay_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal naming what is at that point, not a click sent in hope -- the
    same bargain `window_ui.click_at` makes when a window will not come
    forward. Clicking anyway is how a sequence presses something nobody chose.
    """
    cabinet = install(monkeypatch, FakeCabinet(menu=FakeMenu(exit_drifts=10_000)))
    data = await walk()
    step = steps_of(data)["exit-attendant"]

    assert step["state"] == "failed"
    assert step["error_code"] == "REPLAY_CONTROL_NOT_FOUND"
    assert "what is there is" in step["error"]
    assert "Nothing was clicked" in step["error"]
    # And nothing was: the menu is untouched rather than half-driven.
    assert "exit" not in cabinet.menu.clicked
    assert cabinet.menu.open is True
