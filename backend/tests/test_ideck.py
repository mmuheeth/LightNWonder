"""Virtual OLED i-deck endpoints.

No real window is ever touched. ``FakePanel`` stands in for
:mod:`app.utils.win32` and is installed over its module functions, matching how
``test_obs.py`` swaps the OBS client.

The fake does more than record calls: it *hit-tests* the coordinates it is given
against the real layout and only writes a "Button Pressed" line to its log if the
press actually landed inside the intended key. So a test that asserts a press was
confirmed is asserting the whole coordinate pipeline -- template sizing, centre,
scaling and message packing -- not just that a function was called.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.config.game_config import save_active_game
from app.core.config import settings
from app.services import ideck as ideck_service
from app.utils import panel_log as panel_log_format
from app.utils import win32
from app.utils.log_tail import LogTail
from app.utils.panel_xml import PanelLayout, PanelXmlError, parse_panel
from tests.asserts import assert_failure, assert_success

# A faithful copy of the shipped virtual_oled.xml, comments stripped. The
# geometry here is what the panel service logs when it parses the real file.
PANEL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<PanelConfig>
  <ButtonTemplates>
      <ButtonTemplate id="small_button" bezel_width="5" >
        <TextBox x="5" y="5" width="96" height="64" />
      </ButtonTemplate>
      <ButtonTemplate id="large_button" bezel_width="5" >
        <TextBox x="5" y="5" width="128" height="64" />
      </ButtonTemplate>
  </ButtonTemplates>
  <Panel id="Virtual OLED" width="849" height="183" usbmech_required="false">
    <Buttons>
      <Button id="Service" button_id="11" template_id="small_button" x="10"  y="15"/>
      <Button id="Line1"   button_id="0"  template_id="small_button" x="136" y="15"/>
      <Button id="Line2"   button_id="1"  template_id="small_button" x="247" y="15"/>
      <Button id="Line3"   button_id="2"  template_id="small_button" x="358" y="15"/>
      <Button id="Line4"   button_id="3"  template_id="small_button" x="469" y="15"/>
      <Button id="Line5"   button_id="4"  template_id="small_button" x="580" y="15"/>
      <Button id="Rebet"   button_id="10" template_id="large_button" x="701" y="15"/>
      <Button id="Collect" button_id="12" template_id="small_button" x="10"  y="94"/>
      <Button id="Hold1"   button_id="5"  template_id="small_button" x="136" y="94"/>
      <Button id="Hold2"   button_id="6"  template_id="small_button" x="247" y="94"/>
      <Button id="Hold3"   button_id="7"  template_id="small_button" x="358" y="94"/>
      <Button id="Hold4"   button_id="8"  template_id="small_button" x="469" y="94"/>
      <Button id="Hold5"   button_id="9"  template_id="small_button" x="580" y="94"/>
      <Button id="Maxbet"  button_id="13" template_id="large_button" x="701" y="94"/>
    </Buttons>
  </Panel>
</PanelConfig>
"""

GAME_CONFIG = """{
  "name": "HuffNPuffLink",
  "ideck": {
    "panel": "virtual_oled"
  }
}
"""

PANEL_WIDTH = 849
PANEL_HEIGHT = 183

# Every key, with the rect the panel service logs for it. Used both as a
# parser expectation and to hit-test presses.
EXPECTED_BUTTONS: list[tuple[str, int, int, int, int, int]] = [
    ("Service", 11, 10, 15, 106, 74),
    ("Line1", 0, 136, 15, 106, 74),
    ("Line2", 1, 247, 15, 106, 74),
    ("Line3", 2, 358, 15, 106, 74),
    ("Line4", 3, 469, 15, 106, 74),
    ("Line5", 4, 580, 15, 106, 74),
    ("Rebet", 10, 701, 15, 138, 74),
    ("Collect", 12, 10, 94, 106, 74),
    ("Hold1", 5, 136, 94, 106, 74),
    ("Hold2", 6, 247, 94, 106, 74),
    ("Hold3", 7, 358, 94, 106, 74),
    ("Hold4", 8, 469, 94, 106, 74),
    ("Hold5", 9, 580, 94, 106, 74),
    ("Maxbet", 13, 701, 94, 138, 74),
]

READY_WINDOW = win32.WindowInfo(
    hwnd=0x505C4,
    title="Virtual OLED",
    class_name="SDL_app",
    minimized=False,
    client_width=PANEL_WIDTH,
    client_height=PANEL_HEIGHT,
)
MINIMIZED_WINDOW = dataclasses.replace(
    READY_WINDOW, minimized=True, client_width=0, client_height=0
)

# The device-level transition record. Bit 0x100 marks the press; the release
# repeats the value without it. Copied from a real press of Hold1.
PRESS_LINE = (
    "08/18/26 16:46:23.273 00 Oled:6668 INF: evt=1,ard={value:08x} "
    "// GUIBPDevice.cpp:252 GUIBPDevice::guiPanelEventCB()"
)
RELEASE_LINE = PRESS_LINE
# The panel writes this alongside every press, but in a *legacy* numbering:
# switch 5 logs ID=3. It must never be what confirms a press.
LEGACY_LINE = (
    "08/18/26 16:46:23.273 02 Oled:6668 INF: OledSubsystem -  Button Pressed "
    "ID={id:x} // OledSubsystem.cpp:134 OledSubsystem::HandleButtonPressed()"
)


def press_line(button_id: int) -> str:
    return PRESS_LINE.format(value=0x100 | button_id)


def release_line(button_id: int) -> str:
    return RELEASE_LINE.format(value=button_id)


CROSSING_LINE = (
    "08/18/26 14:40:29.100 00 Oled:6668 INF: Mouse entered window 1 "
    "// SDLEvent.cpp:186 SDLEvent::OnWindowEvent()"
)


class FakePanel:
    """Stands in for :mod:`app.utils.win32` and the panel behind it.

    ``accepts`` models the open question the design has to survive: an SDL
    window that ignores posted input writes nothing to its log. ``accepts_after
    _focus`` models the narrower case where only the first, unfocused click is
    swallowed.
    """

    def __init__(
        self,
        log_path: Path,
        layout: PanelLayout,
        *,
        window: win32.WindowInfo | None = READY_WINDOW,
        supported: bool = True,
        accepts: bool = True,
        accepts_after_focus: bool = False,
        restore_works: bool = True,
        can_interact: bool = True,
    ):
        self.log_path = log_path
        self.layout = layout
        self.window = window
        self.supported = supported
        self.accepts = accepts
        self.accepts_after_focus = accepts_after_focus
        self.restore_works = restore_works
        self.can_interact = can_interact
        self.calls: list[tuple[str, int, int]] = []

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
                client_width=PANEL_WIDTH,
                client_height=PANEL_HEIGHT,
            )
        return True

    def focus(self, hwnd: int) -> bool:
        self.calls.append(("focus", hwnd, 0))
        if self.accepts_after_focus:
            self.accepts = True
        return True

    def post_mouse_move(self, hwnd: int, x: int, y: int) -> None:  # noqa: ARG002
        self.calls.append(("move", x, y))
        if self.accepts:
            self._append(CROSSING_LINE)

    def post_left_down(self, hwnd: int, x: int, y: int) -> None:  # noqa: ARG002
        self.calls.append(("down", x, y))
        if not self.accepts:
            return
        hit = self._hit_test(x, y)
        if hit is not None:
            self._append(press_line(hit))
            # The panel logs this too, in its own numbering. Emitting it keeps
            # the fixture honest about what confirmation has to ignore.
            self._append(LEGACY_LINE.format(id=hit))

    def post_left_up(self, hwnd: int, x: int, y: int) -> None:  # noqa: ARG002
        self.calls.append(("up", x, y))
        if not self.accepts:
            return
        hit = self._hit_test(x, y)
        if hit is not None:
            self._append(release_line(hit))

    # --- helpers ---

    def _append(self, line: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _hit_test(self, x: int, y: int) -> int | None:
        """Which switch a client-space point lands on, or None if it misses."""
        window = self.window
        if window is None or not window.client_width or not window.client_height:
            return None
        panel_x = x * self.layout.width / window.client_width
        panel_y = y * self.layout.height / window.client_height
        for button in self.layout.buttons:
            if (
                button.x <= panel_x < button.x + button.width
                and button.y <= panel_y < button.y + button.height
            ):
                return button.button_id
        return None

    def kinds(self) -> list[str]:
        return [kind for kind, _, _ in self.calls]

    def points(self, kind: str) -> list[tuple[int, int]]:
        return [(x, y) for name, x, y in self.calls if name == kind]


@pytest.fixture
def panel_xml(tmp_path: Path) -> Path:
    path = tmp_path / "virtual_oled.xml"
    path.write_text(PANEL_XML, encoding="utf-8")
    return path


@pytest.fixture
def panel_log(tmp_path: Path) -> Path:
    path = tmp_path / "OledPanelSvc.log"
    path.write_text("", encoding="utf-8")
    return path


@pytest.fixture
def ideck_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, panel_xml: Path, panel_log: Path
) -> Iterator[None]:
    """Point the service at temp config, and make its waits instant."""
    games = tmp_path / "games"
    games.mkdir()
    (games / "HuffNPuffLink.json").write_text(GAME_CONFIG, encoding="utf-8")

    monkeypatch.setattr(settings, "IDECK_PANEL_XML", panel_xml)
    monkeypatch.setattr(settings, "IDECK_LOG_PATH", panel_log)
    monkeypatch.setattr(settings, "IDECK_GAME_CONFIG_DIR", games)
    save_active_game(settings.ideck_active_game_path, "HuffNPuffLink")
    monkeypatch.setattr(settings, "IDECK_PRESS_HOLD_SECONDS", 0.0)
    monkeypatch.setattr(settings, "IDECK_VERIFY_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(settings, "IDECK_VERIFY_PRESSES", True)
    monkeypatch.setattr(settings, "IDECK_RESTORE_IF_MINIMIZED", True)
    monkeypatch.setattr(settings, "IDECK_FOCUS_ON_RETRY", True)
    monkeypatch.setattr(ideck_service, "_POLL_SECONDS", 0.0)
    monkeypatch.setattr(ideck_service, "_RESTORE_ATTEMPTS", 2)
    yield


def install(monkeypatch: pytest.MonkeyPatch, panel: FakePanel) -> FakePanel:
    """Make ``panel`` the Win32 layer the service talks to."""
    for name in (
        "is_supported",
        "find_window",
        "describe",
        "can_post",
        "restore",
        "focus",
        "post_mouse_move",
        "post_left_down",
        "post_left_up",
    ):
        monkeypatch.setattr(win32, name, getattr(panel, name))
    return panel


@pytest.fixture
def panel(
    monkeypatch: pytest.MonkeyPatch, panel_xml: Path, panel_log: Path
) -> FakePanel:
    return install(monkeypatch, FakePanel(panel_log, parse_panel(panel_xml)))


# --- the layout parser ----------------------------------------------------


def test_parser_reproduces_every_rect_the_panel_logged(panel_xml: Path) -> None:
    """Template sizing must land on the same numbers the service prints.

    The real service logs `POS: x=.. y=.. w=106 h=74` per key while parsing;
    those are the values asserted here, so a change in how bezels are applied
    shows up as a failure rather than as presses landing slightly off.
    """
    layout = parse_panel(panel_xml)
    assert (layout.panel_id, layout.width, layout.height) == (
        "Virtual OLED",
        PANEL_WIDTH,
        PANEL_HEIGHT,
    )
    actual = [
        (b.xml_id, b.button_id, b.x, b.y, b.width, b.height) for b in layout.buttons
    ]
    assert actual == EXPECTED_BUTTONS


def test_parser_finds_the_spin_key_by_name(panel_xml: Path) -> None:
    layout = parse_panel(panel_xml)
    rebet = layout.by_xml_id("rebet")
    assert rebet is not None
    assert rebet.button_id == 10
    assert rebet.center == (770, 52)


@pytest.mark.parametrize(
    ("mangle", "expected"),
    [
        # Line2 already owns switch 1, and two keys on one switch would make a
        # press unverifiable: the log names the switch, never the key.
        ('button_id="1"  template_id="small_button" x="136"', "reuses button_id"),
        ('button_id="0"  template_id="nope" x="136"', "unknown template"),
        ('button_id="0"  template_id="small_button" x="oops"', "non-numeric"),
        ('button_id="0"  x="136"', "missing the 'template_id'"),
        ('template_id="small_button" x="136"', "missing the 'button_id'"),
    ],
)
def test_parser_rejects_a_broken_layout(
    tmp_path: Path, mangle: str, expected: str
) -> None:
    broken = PANEL_XML.replace(
        '"Line1"   button_id="0"  template_id="small_button" x="136"',
        f'"Line1"   {mangle}',
    )
    path = tmp_path / "broken.xml"
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(PanelXmlError, match=expected):
        parse_panel(path)


def test_parser_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PanelXmlError, match="not found"):
        parse_panel(tmp_path / "absent.xml")


# --- message packing ------------------------------------------------------


@pytest.mark.parametrize(
    ("x", "y", "expected"),
    [(0, 0, 0x00000000), (770, 52, 0x00340302), (1, 1, 0x00010001)],
)
def test_lparam_packs_the_point_windows_expects(x: int, y: int, expected: int) -> None:
    """``lParam`` is the high word for y and the low word for x."""
    assert win32._lparam(x, y) == expected


# --- status ---------------------------------------------------------------


async def test_status_is_ok_when_the_panel_is_open(
    client: AsyncClient, ideck_env: None, panel: FakePanel
) -> None:
    data = assert_success((await client.get("/api/ideck/status")).json())
    assert data["state"] == "ready"
    assert data["hwnd"] == READY_WINDOW.hwnd
    assert (data["panel_width"], data["panel_height"]) == (PANEL_WIDTH, PANEL_HEIGHT)
    assert data["button_count"] == len(EXPECTED_BUTTONS)


async def test_status_reports_a_closed_panel_rather_than_failing(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    """A panel nobody launched is a state, not a failed request."""
    install(monkeypatch, FakePanel(panel_log, parse_panel(panel_xml), window=None))
    response = await client.get("/api/ideck/status")
    assert response.status_code == 200
    assert assert_success(response.json())["state"] == "not_found"


async def test_status_reports_a_minimized_panel(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    install(
        monkeypatch,
        FakePanel(panel_log, parse_panel(panel_xml), window=MINIMIZED_WINDOW),
    )
    assert assert_success((await client.get("/api/ideck/status")).json())["state"] == (
        "minimized"
    )


async def test_status_reports_an_unsupported_platform(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    install(
        monkeypatch,
        FakePanel(panel_log, parse_panel(panel_xml), supported=False),
    )
    assert assert_success((await client.get("/api/ideck/status")).json())["state"] == (
        "unsupported"
    )


# --- listing --------------------------------------------------------------


async def test_buttons_lists_the_whole_deck_with_live_coordinates(
    client: AsyncClient, ideck_env: None, panel: FakePanel
) -> None:
    data = assert_success((await client.get("/api/ideck/buttons")).json())
    assert len(data) == len(EXPECTED_BUTTONS)

    rebet = next(b for b in data if b["xml_id"] == "Rebet")
    # The client point is the key's centre, because this window shows the panel
    # at exactly its declared size.
    assert rebet["button_id"] == 10
    assert (rebet["client_x"], rebet["client_y"]) == (770, 52)


async def test_buttons_omit_coordinates_while_the_panel_is_closed(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    """The layout is still worth listing with nothing running."""
    install(monkeypatch, FakePanel(panel_log, parse_panel(panel_xml), window=None))
    data = assert_success((await client.get("/api/ideck/buttons")).json())
    assert len(data) == len(EXPECTED_BUTTONS)
    assert all(b["client_x"] is None and b["client_y"] is None for b in data)


# --- pressing -------------------------------------------------------------


async def test_press_sends_move_then_down_then_up(
    client: AsyncClient, ideck_env: None, panel: FakePanel
) -> None:
    """The move is not decoration.

    SDL takes a click's position from the last motion event, not from the button
    message, so dropping the move would land the press wherever the panel last
    believed the pointer was.
    """
    data = assert_success(
        (await client.post("/api/ideck/press", json={"button": "Rebet"})).json()
    )
    assert panel.kinds() == ["move", "down", "up"]
    assert panel.points("down") == [(770, 52)]
    assert (data["xml_id"], data["button_id"]) == ("Rebet", 10)
    assert data["confirmed"] is True
    # 0x10a == pressed | switch 10.
    assert "ard=0000010a" in data["evidence"]


@pytest.mark.parametrize(
    ("name", "xml_id", "button_id"),
    [
        ("Rebet", "Rebet", 10),
        ("rebet", "Rebet", 10),
        ("REBET", "Rebet", 10),
        ("  Rebet  ", "Rebet", 10),
        ("Maxbet", "Maxbet", 13),
        ("collect", "Collect", 12),
        ("hold1", "Hold1", 5),
        ("line3", "Line3", 2),
    ],
)
async def test_press_resolves_layout_names_whatever_their_casing(
    client: AsyncClient,
    ideck_env: None,
    panel: FakePanel,
    name: str,
    xml_id: str,
    button_id: int,
) -> None:
    data = assert_success(
        (await client.post("/api/ideck/press", json={"button": name})).json()
    )
    assert (data["xml_id"], data["button_id"]) == (xml_id, button_id)
    assert data["confirmed"] is True


async def test_press_scales_to_a_resized_window(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    """A panel shown at double size still gets pressed on the right key."""
    doubled = dataclasses.replace(
        READY_WINDOW, client_width=PANEL_WIDTH * 2, client_height=PANEL_HEIGHT * 2
    )
    live = install(
        monkeypatch, FakePanel(panel_log, parse_panel(panel_xml), window=doubled)
    )
    data = assert_success(
        (await client.post("/api/ideck/press", json={"button": "Rebet"})).json()
    )
    assert live.points("down") == [(1540, 104)]
    # The fake hit-tests in panel space, so confirmation proves the scaled
    # point still lands inside Rebet.
    assert data["confirmed"] is True


async def test_press_restores_a_minimized_panel_first(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    """A minimized window has no client area, so there is nothing to aim at."""
    live = install(
        monkeypatch,
        FakePanel(panel_log, parse_panel(panel_xml), window=MINIMIZED_WINDOW),
    )
    data = assert_success(
        (await client.post("/api/ideck/press", json={"button": "Rebet"})).json()
    )
    assert live.kinds() == ["restore", "move", "down", "up"]
    assert data["restored"] is True
    assert data["confirmed"] is True


async def test_press_refuses_a_minimized_panel_when_restoring_is_off(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    monkeypatch.setattr(settings, "IDECK_RESTORE_IF_MINIMIZED", False)
    live = install(
        monkeypatch,
        FakePanel(panel_log, parse_panel(panel_xml), window=MINIMIZED_WINDOW),
    )
    response = await client.post("/api/ideck/press", json={"button": "Rebet"})
    assert response.status_code == 409
    assert_failure(response.json(), code="IDECK_WINDOW_NOT_FOUND")
    assert live.kinds() == []


async def test_press_rejects_an_unknown_button(
    client: AsyncClient, ideck_env: None, panel: FakePanel
) -> None:
    response = await client.post("/api/ideck/press", json={"button": "nudge"})
    assert response.status_code == 404
    payload = response.json()
    assert_failure(payload, code="IDECK_BUTTON_NOT_FOUND")
    # The message lists what *is* pressable, so a typo is self-correcting.
    assert "Rebet" in payload["message"]
    # Nothing was posted: the name is checked before any window is touched.
    assert panel.kinds() == []


async def test_press_fails_when_the_panel_is_closed(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    install(monkeypatch, FakePanel(panel_log, parse_panel(panel_xml), window=None))
    response = await client.post("/api/ideck/press", json={"button": "Rebet"})
    assert response.status_code == 409
    assert_failure(response.json(), code="IDECK_WINDOW_NOT_FOUND")


async def test_press_fails_off_windows(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    install(monkeypatch, FakePanel(panel_log, parse_panel(panel_xml), supported=False))
    response = await client.post("/api/ideck/press", json={"button": "Rebet"})
    assert response.status_code == 503
    assert_failure(response.json(), code="SERVICE_UNAVAILABLE")


# --- confirmation ---------------------------------------------------------


async def test_an_ignored_press_is_reported_rather_than_claimed(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    """The point of verification: a window that swallows input must not 200."""
    live = install(
        monkeypatch,
        FakePanel(panel_log, parse_panel(panel_xml), accepts=False),
    )
    response = await client.post("/api/ideck/press", json={"button": "Rebet"})
    assert response.status_code == 502
    assert_failure(response.json(), code="IDECK_PRESS_NOT_CONFIRMED")
    # It tried again with the window focused before giving up.
    assert live.kinds() == ["move", "down", "up", "focus", "move", "down", "up"]


async def test_a_swallowed_first_click_is_retried_with_focus(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    """SDL can eat the click that focuses a window; the retry recovers it."""
    live = install(
        monkeypatch,
        FakePanel(
            panel_log,
            parse_panel(panel_xml),
            accepts=False,
            accepts_after_focus=True,
        ),
    )
    data = assert_success(
        (await client.post("/api/ideck/press", json={"button": "Rebet"})).json()
    )
    assert data["confirmed"] is True
    assert data["refocused"] is True
    assert "focus" in live.kinds()


async def test_verification_can_be_waived_per_press(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    """Unverified is reported honestly as unconfirmed, not as confirmed."""
    install(monkeypatch, FakePanel(panel_log, parse_panel(panel_xml), accepts=False))
    data = assert_success(
        (
            await client.post(
                "/api/ideck/press", json={"button": "Rebet", "verify": False}
            )
        ).json()
    )
    assert data["verified"] is False
    assert data["confirmed"] is False
    assert data["evidence"] is None


async def test_confirmation_matches_only_the_press_of_the_right_switch(
    ideck_env: None, panel: FakePanel
) -> None:
    """The encoding is easy to get backwards, so pin every direction of it."""
    spin = panel_log_format.press_pattern(10)
    assert spin.search(press_line(10)) is not None
    # The release of the same switch is not a press.
    assert spin.search(release_line(10)) is None
    # A neighbouring switch must not satisfy it: 0x10a vs 0x10b.
    assert spin.search(press_line(11)) is None
    assert panel_log_format.press_pattern(1).search(press_line(10)) is None


async def test_confirmation_ignores_the_legacy_numbering(
    ideck_env: None, panel: FakePanel
) -> None:
    """Pressing switch 5 makes the panel log `Button Pressed ID=3`.

    The two numberings are unrelated, so matching that line would confirm the
    wrong key -- and miss the right one.
    """
    for button_id in (3, 5, 10):
        pattern = panel_log_format.press_pattern(button_id)
        assert pattern.search(LEGACY_LINE.format(id=button_id)) is None


async def test_confirmation_only_reads_what_arrived_after_the_press(
    ideck_env: None, panel: FakePanel, panel_log: Path
) -> None:
    """A press already in the log must not confirm the next one."""
    panel_log.write_text(press_line(10) + "\n", encoding="utf-8")
    panel.accepts = False
    with pytest.raises(Exception, match="logged nothing"):
        await ideck_service.press("Rebet")


async def test_confirmation_survives_the_log_rotating(
    ideck_env: None, panel: FakePanel, panel_log: Path
) -> None:
    """The service caps its logs around 20MB; a shrunk file restarts the cursor."""
    tail = LogTail(panel_log)
    panel_log.write_text("x" * 5000, encoding="utf-8")
    offset = tail.offset()
    panel_log.write_text("rotated\n", encoding="utf-8")
    chunk, cursor = tail.read_since(offset)
    assert "rotated" in chunk
    assert cursor == panel_log.stat().st_size


async def test_verification_without_a_log_is_a_configuration_error(
    client: AsyncClient,
    ideck_env: None,
    panel: FakePanel,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Silently degrading would make every press an unprovable claim."""
    monkeypatch.setattr(settings, "IDECK_LOG_PATH", tmp_path / "absent.log")
    response = await client.post("/api/ideck/press", json={"button": "Rebet"})
    assert response.status_code == 500
    assert_failure(response.json(), code="IDECK_CONFIG_INVALID")


# --- sequences and probing ------------------------------------------------


async def test_a_sequence_presses_each_button_in_order(
    client: AsyncClient, ideck_env: None, panel: FakePanel
) -> None:
    data = assert_success(
        (
            await client.post(
                "/api/ideck/sequence",
                json={"buttons": ["Line1", "Rebet"], "delay_seconds": 0.0},
            )
        ).json()
    )
    assert [r["xml_id"] for r in data] == ["Line1", "Rebet"]
    assert panel.points("down") == [(189, 52), (770, 52)]


async def test_a_sequence_stops_at_the_first_failure(
    client: AsyncClient, ideck_env: None, panel: FakePanel
) -> None:
    response = await client.post(
        "/api/ideck/sequence",
        json={"buttons": ["Line1", "nudge", "Rebet"], "delay_seconds": 0.0},
    )
    assert response.status_code == 404
    # Only the first button was ever posted.
    assert panel.points("down") == [(189, 52)]


async def test_probe_moves_the_pointer_without_pressing_anything(
    client: AsyncClient, ideck_env: None, panel: FakePanel
) -> None:
    """The safe first check: no button-down, so no game side effect."""
    data = assert_success((await client.post("/api/ideck/probe")).json())
    assert data["observed"] is True
    assert panel.kinds() == ["move"]


async def test_probe_reports_a_silent_panel_without_failing(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    install(monkeypatch, FakePanel(panel_log, parse_panel(panel_xml), accepts=False))
    response = await client.post("/api/ideck/probe")
    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["posted"] is True
    assert data["observed"] is False


# --- configuration --------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["../escape", "sub/escape", "..\\escape", "C:/escape", ".."]
)
def test_the_game_name_cannot_escape_the_config_directory(name: str) -> None:
    """It is interpolated into a path, so a separator would read anything."""
    from pydantic import ValidationError

    from app.schemas.games import SelectGameRequest

    with pytest.raises(ValidationError, match="bare name"):
        SelectGameRequest(game=name)


# --- integrity-level blocking ---------------------------------------------


async def test_a_blocked_window_is_diagnosed_not_left_looking_broken(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    """UIPI is the failure most likely to be mistaken for a bug.

    A panel launched elevated refuses input from a normal backend, and every
    symptom -- a restore that does nothing, a press that vanishes -- looks like
    something else. The check has to name the real cause.
    """
    install(
        monkeypatch,
        FakePanel(panel_log, parse_panel(panel_xml), can_interact=False),
    )
    response = await client.post("/api/ideck/press", json={"button": "Rebet"})
    assert response.status_code == 409
    assert_failure(response.json(), code="IDECK_ACCESS_DENIED")
    assert "elevated" in response.json()["message"]


async def test_status_reports_a_blocked_window(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    """The window looks healthy until something tries to drive it."""
    install(
        monkeypatch,
        FakePanel(panel_log, parse_panel(panel_xml), can_interact=False),
    )
    data = assert_success((await client.get("/api/ideck/status")).json())
    assert data["state"] == "access_denied"


async def test_probe_names_the_blocking_cause(
    client: AsyncClient,
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    install(
        monkeypatch,
        FakePanel(panel_log, parse_panel(panel_xml), can_interact=False),
    )
    data = assert_success((await client.post("/api/ideck/probe")).json())
    assert data["posted"] is False
    assert "elevated" in data["detail"]


async def test_someone_else_pressing_does_not_confirm_our_press(
    ideck_env: None,
    monkeypatch: pytest.MonkeyPatch,
    panel_log: Path,
    panel_xml: Path,
) -> None:
    """A human at the machine presses keys while we are waiting.

    Their SPIN landing in the same slice of log must not be read as our HOLD1
    landing, or an ignored press would report success whenever the cabinet is
    in use.
    """
    live = install(
        monkeypatch,
        FakePanel(panel_log, parse_panel(panel_xml), accepts=False),
    )

    def interfere(hwnd: int, x: int, y: int) -> None:
        live.calls.append(("down", x, y))
        # Somebody else pressing Rebet, plus the legacy line for our switch.
        live._append(press_line(10))
        live._append(LEGACY_LINE.format(id=5))

    monkeypatch.setattr(win32, "post_left_down", interfere)

    with pytest.raises(Exception, match="logged nothing"):
        await ideck_service.press("Hold1")
