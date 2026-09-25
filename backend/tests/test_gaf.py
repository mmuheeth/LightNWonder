"""Driving the game through GAF.

No real game and no real NRobot server is ever touched. ``FakeGame`` stands in
for the whole keyword layer, installed over :func:`app.services.gaf._try` --
the single door every call goes through -- so a test asserts the *order* the
service puts keywords in, which is the only thing this service owns.

The fake counts state reads rather than sleeping, because the two waits the
service performs are defined by what the game answers and not by how long it
takes to answer: a spin that departs idle on the third read is the same spin
whether that took 300ms or three seconds.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.config.gaf import (
    DEFAULT_GENERAL_QUERIES,
    DEFAULT_GENERIC_QUERIES,
    GafSettings,
    GafTargetError,
    resolve_target,
)
from app.config.game_config import load_game_config, save_active_game
from app.config.runtime import settings
from app.exceptions.base import GafNotIdleError
from app.schemas.gaf import GafState, SpinOutcome
from app.services import gaf as gaf_service
from app.utils import gaf_objects
from app.utils.nrobot import KeywordFailure, KeywordReply, RemoteLibrary, as_argument
from tests.asserts import assert_failure, assert_success

# --- the protocol ---------------------------------------------------------


def _reply(**overrides: Any) -> KeywordReply:
    """One ``run_keyword`` reply with everything but the interesting bit."""
    base: dict[str, Any] = {
        "keyword": "SOMEKEYWORD",
        "status": "PASS",
        "value": "True",
        "output": "",
        "error": "",
        "traceback": "",
    }
    return KeywordReply(**{**base, **overrides})


def test_a_failed_keyword_is_a_reply_not_a_fault() -> None:
    """The subtlest failure mode of this protocol: FAIL arrives as a 200."""
    reply = _reply(status="FAIL", value="", error="the game said no")

    assert reply.passed is False
    with pytest.raises(KeywordFailure) as caught:
        reply.raise_for_status()
    assert "the game said no" in str(caught.value)


def test_a_passing_keyword_can_still_answer_no() -> None:
    """`PASS` says the keyword ran; `truthy` says it did the thing."""
    assert _reply(value="True").truthy is True
    assert _reply(value="true").truthy is True
    assert _reply(value="False").truthy is False
    # Ran fine, answered no -- pressing on regardless is how a press that
    # never happened gets reported as a success.
    assert _reply(status="PASS", value="False").passed is True


def test_a_failed_keyword_is_never_truthy() -> None:
    """Even when the failure reply happens to carry the word True."""
    assert _reply(status="FAIL", value="True").truthy is False


def test_a_returned_list_reads_as_one_string() -> None:
    """Several keywords answer with a list of cells rather than a value."""
    assert _reply(value=["PLAY", "300", "CREDITS"]).text == "PLAY, 300, CREDITS"
    assert _reply(value=None).text == ""


def test_arguments_are_spelled_the_way_the_dotnet_side_parses_them() -> None:
    """Robot's remote protocol has no types, so every argument is a string."""
    assert as_argument(True) == "True"
    assert as_argument(False) == "False"
    assert as_argument(9090) == "9090"
    # Not "None": several keywords read empty as "not given" and would try to
    # use the literal.
    assert as_argument(None) == ""


def test_a_library_addresses_one_endpoint() -> None:
    library = RemoteLibrary("http://127.0.0.1:8270/", "RFTestCode.IDeck.IDeckLibrary")
    assert library.url == "http://127.0.0.1:8270/RFTestCode.IDeck.IDeckLibrary"


def test_an_absent_server_is_unreachable_rather_than_an_exception() -> None:
    """Port 1 is never listening; `reachable` is what a status poll calls."""
    assert (
        RemoteLibrary("http://127.0.0.1:1", "Anything", timeout=1.0).reachable()
        is False
    )


def test_a_probe_hands_back_why_it_could_not_reach_the_server() -> None:
    """The verdict alone cannot distinguish the two faults that produce it.

    Port 1 refuses on some hosts and silently drops on others, so which
    transport failure this takes is not the assertion -- that the reason
    names the library it could not reach is.
    """
    reason = RemoteLibrary("http://127.0.0.1:1", "Anything", timeout=1.0).probe()

    assert reason is not None
    assert "Anything" in reason


# --- the object-query files -----------------------------------------------


def _write_query(path: Path, entries: dict[str, Any], *, bom: bool = True) -> None:
    """Write one object-query file the way AGTF ships them: with a BOM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8-sig" if bom else "utf-8")


def test_a_query_file_is_read_through_its_byte_order_mark(tmp_path: Path) -> None:
    """They ship with a BOM and plain utf-8 throws on the first byte."""
    one = tmp_path / "one.json"
    _write_query(one, {"SpinButton": {"GameObjectIdentifier": "Spin"}})

    queries = gaf_objects.load_query_set([one], [])

    assert queries.general == {"SpinButton": {"GameObjectIdentifier": "Spin"}}


def test_later_files_overwrite_earlier_keys(tmp_path: Path) -> None:
    """The merge order is the meaning: game-specific overrides common."""
    common = tmp_path / "common.json"
    specific = tmp_path / "specific.json"
    _write_query(common, {"Take": {"GameObjectIdentifier": "old"}, "Spin": {}})
    _write_query(specific, {"Take": {"GameObjectIdentifier": "new"}})

    queries = gaf_objects.load_query_set([common, specific], [])

    assert queries.general["Take"] == {"GameObjectIdentifier": "new"}
    # The common file is not replaced wholesale, only overridden key by key.
    assert "Spin" in queries.general


def test_the_two_groups_never_merge_into_each_other(tmp_path: Path) -> None:
    """They feed two different client calls and are two dictionaries."""
    general = tmp_path / "general.json"
    generic = tmp_path / "generic.json"
    _write_query(general, {"OnlyGeneral": {}})
    _write_query(generic, {"OnlyGeneric": {}})

    queries = gaf_objects.load_query_set([general], [generic])

    assert set(queries.general) == {"OnlyGeneral"}
    assert set(queries.generic) == {"OnlyGeneric"}
    assert queries.names() == ("OnlyGeneral", "OnlyGeneric")


def test_every_missing_file_is_reported_at_once(tmp_path: Path) -> None:
    """Half a dictionary fails much later with a key error naming nothing, so
    the absent files are collected rather than reported one attempt at a time."""
    present = tmp_path / "present.json"
    _write_query(present, {"A": {}})
    absent_one = tmp_path / "gone-one.json"
    absent_two = tmp_path / "gone-two.json"

    with pytest.raises(gaf_objects.ObjectQueryMissing) as caught:
        gaf_objects.load_query_set([present, absent_one], [absent_two])

    assert caught.value.missing == (absent_one, absent_two)


def test_a_relative_entry_resolves_against_the_workspace_root(tmp_path: Path) -> None:
    resolved = gaf_objects.resolve(tmp_path, ["GameCommon/one.json"])
    assert resolved == (tmp_path / "GameCommon/one.json",)


def test_an_absolute_entry_is_left_where_it_points(tmp_path: Path) -> None:
    """So one file can be moved without moving the whole workspace."""
    elsewhere = tmp_path / "elsewhere" / "one.json"
    assert gaf_objects.resolve(tmp_path, [str(elsewhere)]) == (elsewhere,)


# --- resolving a game's target --------------------------------------------

GAF_SETTINGS = GafSettings()


def test_an_omitted_setting_falls_back_to_the_environment(tmp_path: Path) -> None:
    target = resolve_target("AGame", {"object_query_root": str(tmp_path)}, GAF_SETTINGS)

    assert (target.host, target.port) == (GAF_SETTINGS.GAF_HOST, GAF_SETTINGS.GAF_PORT)
    assert target.gdk_version == GAF_SETTINGS.GAF_GDK_VERSION
    assert target.take_win_button == GAF_SETTINGS.GAF_TAKE_WIN_BUTTON
    # All eight default files, general first, game-specific last in each group.
    assert len(target.query_files) == 8
    assert all(one.is_absolute() for one in target.query_files)


def test_a_game_names_its_own_endpoint(tmp_path: Path) -> None:
    """The whole point of the block: one backend, several games, several ports."""
    target = resolve_target(
        "AGame",
        {"host": "10.0.0.7", "port": 9191, "object_query_root": str(tmp_path)},
        GAF_SETTINGS,
    )

    assert target.endpoint == "10.0.0.7:9191"


def test_an_unknown_game_type_is_refused_by_name(tmp_path: Path) -> None:
    """A typo otherwise selects a different wrapper and fails much later."""
    with pytest.raises(GafTargetError) as caught:
        resolve_target(
            "AGame",
            {"game_type": "BallyStile", "object_query_root": str(tmp_path)},
            GAF_SETTINGS,
        )

    assert "BallyStyle" in str(caught.value) and "ShuffleStyle" in str(caught.value)


def test_a_game_type_is_matched_without_regard_to_case(tmp_path: Path) -> None:
    target = resolve_target(
        "AGame",
        {"game_type": "ballystyle", "object_query_root": str(tmp_path)},
        GAF_SETTINGS,
    )
    assert target.game_type == "BallyStyle"


@pytest.mark.parametrize(
    "block",
    [
        pytest.param({"port": 0}, id="port-below-range"),
        pytest.param({"port": 70000}, id="port-above-range"),
        pytest.param({"port": True}, id="port-is-a-bool"),
        pytest.param({"port": "9090"}, id="port-is-a-string"),
        pytest.param({"host": ""}, id="empty-host"),
        pytest.param({"general_queries": []}, id="no-general-files"),
        pytest.param({"generic_queries": "one.json"}, id="generic-is-not-a-list"),
        pytest.param({"prot": 9090}, id="misspelled-key"),
    ],
)
def test_an_unusable_block_is_refused(block: dict[str, Any], tmp_path: Path) -> None:
    with pytest.raises(GafTargetError):
        resolve_target(
            "AGame", {"object_query_root": str(tmp_path), **block}, GAF_SETTINGS
        )


def test_relative_files_need_a_root_to_resolve_against() -> None:
    """There is no sensible default for it: it is a Perforce workspace."""
    with pytest.raises(GafTargetError):
        resolve_target("AGame", {"host": "127.0.0.1"}, GAF_SETTINGS)


# --- the service ----------------------------------------------------------


class FakeGame:
    """Stands in for the whole keyword layer.

    Every call the service makes lands on :meth:`reply`. ``idle`` is what the
    idle machine answers until the spin button is pressed; from the press on,
    state reads are *counted* and ``idle_at`` scripts what it answers from the
    nth read after the press -- which is how a test says "the spin starts on
    the second poll and finishes on the fifth" without waiting for either.

    Keyed off the press rather than off the first read because the service
    reads the state once *before* pressing, to check the session is alive and
    the game is idle; a script that started counting there would answer the
    guard rather than the spin.
    """

    def __init__(
        self,
        *,
        idle: str = "stateIdleWithCredits",
        idle_at: dict[int, str] | None = None,
        win_offered: bool = False,
        win_offered_at: int | None = None,
        take_win_interactable: bool = False,
        fail: set[str] | None = None,
    ) -> None:
        self.idle = idle
        self.idle_at = idle_at or {}
        self.win_offered = win_offered
        self.win_offered_at = win_offered_at
        self.take_win_interactable = take_win_interactable
        self.fail = fail or set()
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []
        self.idle_reads = 0
        self.spun = False

    @property
    def keywords(self) -> list[str]:
        """Just the keyword names, in the order they were called."""
        return [keyword for _, keyword, _ in self.calls]

    def _idle(self) -> str:
        if not self.spun:
            return self.idle
        self.idle_reads += 1
        if self.idle_reads in self.idle_at:
            self.idle = self.idle_at[self.idle_reads]
        if self.win_offered_at is not None and self.idle_reads >= self.win_offered_at:
            self.win_offered = True
        return self.idle

    async def reply(self, library: str, keyword: str, *args: Any) -> KeywordReply:
        self.calls.append((library, keyword, args))
        if keyword in self.fail:
            return _reply(keyword=keyword, status="FAIL", value="", error="refused")

        if keyword == "GETCURRENTSTATE":
            machine = args[0]
            if machine == gaf_service.IDLE_MACHINE:
                return _reply(keyword=keyword, value=self._idle())
            if machine == gaf_service.GAMBLE_MACHINE:
                return _reply(
                    keyword=keyword,
                    value=gaf_service.OFFER if self.win_offered else "waitForOKToOffer",
                )
            return _reply(keyword=keyword, value="stateIdle")

        if keyword == "ISNONWAGERBUTTONINTERACTABLE":
            offered = self.win_offered or self.take_win_interactable
            return _reply(keyword=keyword, value="True" if offered else "False")

        if keyword == "METERINFO":
            return _reply(
                keyword=keyword,
                value={"CreditMeter": "$995.80"}.get(str(args[0]), "0.00"),
            )

        if keyword == "PRESSMECHANICALSPINBUTTON":
            # The script is relative to the press, and a second spin re-runs it.
            self.spun = True
            self.idle_reads = 0

        return _reply(keyword=keyword, value="True")


@pytest.fixture
def game_with_gaf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An active game declaring a `gaf` block, with its query files present."""
    root = tmp_path / "workspace"
    for index, relative in enumerate(
        (*DEFAULT_GENERAL_QUERIES, *DEFAULT_GENERIC_QUERIES)
    ):
        _write_query(root / relative, {f"Control{index}": {}})

    games = tmp_path / "games"
    games.mkdir()
    (games / "TestGame.json").write_text(
        json.dumps(
            {
                "name": "TestGame",
                "gaf": {
                    "host": "127.0.0.1",
                    "port": 9090,
                    "object_query_root": str(root),
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "IDECK_GAME_CONFIG_DIR", games)
    save_active_game(settings.ideck_active_game_path, "TestGame")
    gaf_service.reset()
    yield games
    gaf_service.reset()


@pytest.fixture
def fast_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Poll without sleeping, and give up quickly when nothing happens."""
    monkeypatch.setattr(settings, "GAF_SETTLE_POLL_SECONDS", 0.0)
    monkeypatch.setattr(settings, "GAF_SPIN_START_SECONDS", 0.05)
    monkeypatch.setattr(settings, "GAF_SETTLE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(settings, "GAF_READ_METERS", False)


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeGame) -> FakeGame:
    """Put the fake behind every keyword the service calls."""
    monkeypatch.setattr(gaf_service, "_try", fake.reply)
    return fake


async def test_a_spin_opens_a_session_before_it_presses_anything(
    game_with_gaf: Path, fast_waits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The four connect steps are order-sensitive, and the press comes last."""
    fake = install(
        monkeypatch, FakeGame(idle_at={1: "statePlaying", 3: "stateIdleWithCredits"})
    )

    await gaf_service.spin()

    opened = fake.keywords[:4]
    assert opened == [
        "INIT",
        "CONNECTGAMECLIENTTOSERVER",
        "INITIALIZEGAMECLIENT",
        "INITIALIZEGENERICGAMECLIENT",
    ]
    assert "PRESSMECHANICALSPINBUTTON" in fake.keywords
    assert fake.keywords.index("PRESSMECHANICALSPINBUTTON") > 3


async def test_a_spin_waits_for_the_game_to_leave_idle_before_judging_it(
    game_with_gaf: Path, fast_waits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The press is acknowledged before the game moves.

    Without the departure wait the settle loop's first question -- "are we
    idle?" -- is answered yes by the state the game was *already* in, and a
    spin still turning is reported as finished.
    """
    fake = install(
        monkeypatch,
        # Still idle for two reads after the press, then playing, then done.
        FakeGame(idle_at={3: "statePlaying", 6: "stateIdleWithCredits"}),
    )

    result = await gaf_service.spin()

    assert result.outcome is SpinOutcome.IDLE
    assert result.idle_state == "stateIdleWithCredits"
    # It did not stop at the first read; it saw the spin run.
    assert fake.idle_reads > 3


async def test_a_win_holds_the_game_and_is_reported_as_a_finished_spin(
    game_with_gaf: Path, fast_waits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A winning spin never returns to idle, so waiting only for idle would
    time out on exactly the spins worth having."""
    install(monkeypatch, FakeGame(idle_at={1: "statePlaying"}, win_offered_at=3))

    result = await gaf_service.spin()

    assert result.outcome is SpinOutcome.WIN_OFFERED
    assert result.win_offered is True
    assert result.idle_state == "statePlaying"
    assert "collect" in result.detail.lower()


async def test_a_spin_that_never_settles_is_a_result_not_an_error(
    game_with_gaf: Path, fast_waits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state it is stuck in is the diagnosis, so it travels on a 200."""
    install(monkeypatch, FakeGame(idle_at={1: "statePlaying"}))

    result = await gaf_service.spin()

    assert result.outcome is SpinOutcome.TIMEOUT
    assert result.pressed is True
    assert result.idle_state == "statePlaying"


async def test_an_unsettled_spin_presses_and_returns_immediately(
    game_with_gaf: Path, fast_waits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install(monkeypatch, FakeGame())

    result = await gaf_service.spin(settle=False)

    assert (result.pressed, result.settled) == (True, False)
    # Nothing is asked of the game after the press: that is what "not
    # settled" means, and it is the difference between a press and a result.
    after = fake.keywords[fake.keywords.index("PRESSMECHANICALSPINBUTTON") + 1 :]
    assert after == []


async def test_a_declined_press_is_a_failure_not_a_spin(
    game_with_gaf: Path, fast_waits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The keyword ran and answered False -- treating that as success is how a
    press that never happened gets reported as a spin."""
    install(monkeypatch, FakeGame(fail={"PRESSMECHANICALSPINBUTTON"}))

    with pytest.raises(Exception) as caught:
        await gaf_service.spin()

    assert "PRESSMECHANICALSPINBUTTON" in str(caught.value)


async def test_a_game_still_playing_is_not_spun_on_top_of(
    game_with_gaf: Path, fast_waits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured, not theoretical: a free-spin bonus outlasts the settle
    timeout, and the spin after the one that timed out lands mid-bonus. Its
    result would belong to the spin that has not finished."""
    fake = install(monkeypatch, FakeGame(idle="statePlaying"))

    with pytest.raises(GafNotIdleError):
        await gaf_service.spin()

    assert "PRESSMECHANICALSPINBUTTON" not in fake.keywords


async def test_an_uncollected_win_blocks_the_next_spin(
    game_with_gaf: Path, fast_waits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same hold that makes a win readable makes the next press wrong."""
    install(monkeypatch, FakeGame(idle_at={1: "statePlaying"}, win_offered_at=3))
    first = await gaf_service.spin()
    assert first.outcome is SpinOutcome.WIN_OFFERED

    with pytest.raises(GafNotIdleError):
        await gaf_service.spin()


async def test_forcing_spins_into_a_game_that_is_still_playing(
    game_with_gaf: Path, fast_waits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The escape hatch is explicit, so the hazard is never silent."""
    fake = install(monkeypatch, FakeGame(idle="statePlaying"))

    result = await gaf_service.spin(force=True)

    assert result.pressed is True
    assert "PRESSMECHANICALSPINBUTTON" in fake.keywords


async def test_nothing_to_collect_is_a_result_rather_than_an_error(
    game_with_gaf: Path, fast_waits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different fact from a press that failed, so it is not an exception."""
    fake = install(monkeypatch, FakeGame(take_win_interactable=False))

    result = await gaf_service.take_win()

    assert (result.pressed, result.interactable) == (False, False)
    assert "PRESSNONWAGERBUTTON" not in fake.keywords


async def test_take_win_presses_the_button_the_game_config_names(
    game_with_gaf: Path, fast_waits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = install(monkeypatch, FakeGame(take_win_interactable=True))

    result = await gaf_service.take_win()

    assert result.pressed is True
    pressed = [
        args for _, keyword, args in fake.calls if keyword == "PRESSNONWAGERBUTTON"
    ]
    assert pressed == [("TakeWinButton",)]


async def test_forcing_presses_a_button_the_game_says_is_dead(
    game_with_gaf: Path, fast_waits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For a theme whose button does not advertise itself."""
    fake = install(monkeypatch, FakeGame(take_win_interactable=False))

    result = await gaf_service.take_win(force=True)

    assert (result.pressed, result.interactable) == (True, False)
    assert "PRESSNONWAGERBUTTON" in fake.keywords


async def test_a_game_with_no_gaf_block_cannot_be_driven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    games = tmp_path / "games"
    games.mkdir()
    (games / "Plain.json").write_text(json.dumps({"name": "Plain"}), encoding="utf-8")
    monkeypatch.setattr(settings, "IDECK_GAME_CONFIG_DIR", games)
    save_active_game(settings.ideck_active_game_path, "Plain")
    gaf_service.reset()

    state = await gaf_service.status()

    assert state.state is GafState.NOT_CONFIGURED
    assert state.connected is False


async def test_a_missing_query_file_is_reported_before_anything_connects(
    game_with_gaf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the dictionary the client cannot resolve one control by name."""
    target = gaf_service._resolve()
    target.query_files[0].unlink()
    monkeypatch.setattr(
        gaf_service,
        "_library",
        lambda name: _AlwaysReachable(),  # noqa: ARG005
    )

    state = await gaf_service.status()

    assert state.state is GafState.FILES_MISSING
    assert [one.present for one in state.query_files].count(False) == 1


class _AlwaysReachable:
    """A library handle that answers the one question status asks of it."""

    def probe(self) -> str | None:
        return None

    def reachable(self) -> bool:
        return True


class _AnswersButNotForThisLibrary:
    """A server is up; it just does not host the automation keywords.

    What launching ``NRobot.Server.exe`` outside its own directory produces.
    """

    # The live server's own wording, measured against NRobot 0.45.2.321: a
    # library it has not loaded comes back as an XML-RPC *fault*, not a 404.
    reason = (
        "RFTestCode.ConnectGame.ConnectGameLibrary.get_keyword_names faulted: "
        "<Fault 1: 'Type RFTestCode.ConnectGame.ConnectGameLibrary is not loaded'>"
    )

    def probe(self) -> str | None:
        return self.reason

    def reachable(self) -> bool:
        return False


async def test_an_unreachable_server_reports_which_fault_it_was(
    game_with_gaf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two faults share this state and have different fixes -- a server that
    is not running, and one running from the wrong directory -- so the reason
    has to travel or the state cannot be acted on."""
    monkeypatch.setattr(
        gaf_service,
        "_library",
        lambda name: _AnswersButNotForThisLibrary(),  # noqa: ARG005
    )

    state = await gaf_service.status()

    assert state.state is GafState.UNREACHABLE
    assert "is not loaded" in state.detail
    assert "NRobotStartUpScript.bat" in state.detail


async def test_reading_status_never_opens_a_session(
    game_with_gaf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dashboard polls this; connecting to the game as a side effect of
    looking at it would be a surprising thing for a status read to do."""
    fake = install(monkeypatch, FakeGame())
    monkeypatch.setattr(
        gaf_service,
        "_library",
        lambda name: _AlwaysReachable(),  # noqa: ARG005
    )

    state = await gaf_service.status()

    assert state.state is GafState.DISCONNECTED
    assert "CONNECTGAMECLIENTTOSERVER" not in fake.keywords


async def test_switching_game_reopens_the_session_against_the_new_one(
    game_with_gaf: Path, fast_waits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A held session is bound to the old game's endpoint."""
    fake = install(monkeypatch, FakeGame(idle_at={1: "statePlaying", 3: "stateIdle"}))
    await gaf_service.spin()
    opened_once = fake.keywords.count("CONNECTGAMECLIENTTOSERVER")

    gaf_service.reset_game_config()
    await gaf_service.spin()

    assert fake.keywords.count("CONNECTGAMECLIENTTOSERVER") == opened_once + 1
    assert "DESTROYGAMECLIENT" in fake.keywords


# --- the endpoints --------------------------------------------------------


async def test_status_is_always_two_hundred(
    client: AsyncClient, game_with_gaf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check `data.state`, not the status code -- nothing running is a state."""
    monkeypatch.setattr(
        gaf_service,
        "_library",
        lambda name: _AlwaysReachable(),  # noqa: ARG005
    )

    response = await client.get("/api/gaf/status")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["state"] == GafState.DISCONNECTED.value
    assert data["port"] == 9090


async def test_spinning_takes_no_body_at_all(
    client: AsyncClient,
    game_with_gaf: Path,
    fast_waits: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Which game and where it listens come from the config, not the request."""
    install(monkeypatch, FakeGame(idle_at={1: "statePlaying", 3: "stateIdle"}))

    response = await client.post("/api/gaf/spin")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["pressed"] is True
    assert data["outcome"] == SpinOutcome.IDLE.value


async def test_a_game_without_gaf_answers_a_conflict(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """409 rather than 5xx: something has to be edited, not retried."""
    games = tmp_path / "games"
    games.mkdir()
    (games / "Plain.json").write_text(json.dumps({"name": "Plain"}), encoding="utf-8")
    monkeypatch.setattr(settings, "IDECK_GAME_CONFIG_DIR", games)
    save_active_game(settings.ideck_active_game_path, "Plain")
    gaf_service.reset()

    response = await client.post("/api/gaf/spin")

    assert response.status_code == 409
    assert_failure(response.json(), code="GAF_NOT_CONFIGURED")


async def test_take_win_reports_nothing_to_collect_on_a_two_hundred(
    client: AsyncClient,
    game_with_gaf: Path,
    fast_waits: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, FakeGame(take_win_interactable=False))

    response = await client.post("/api/gaf/take-win")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["pressed"] is False
    assert data["button"] == "TakeWinButton"


def test_the_shipped_config_declares_a_drivable_game() -> None:
    """HuffNPuffHighRise is the game this was measured against, so its config
    has to resolve to a target without the workspace being present."""
    path = settings.ideck_game_config_dir / "HuffNPuffHighRise.json"
    if not path.is_file():  # pragma: no cover - the file is gitignored here
        pytest.skip("HuffNPuffHighRise.json is not present in this checkout")

    target = resolve_target(
        "HuffNPuffHighRise", load_game_config(path).gaf, GAF_SETTINGS
    )

    assert (target.game_type, target.gdk_version) == ("BallyStyle", "12")
    assert target.port == 9090
    assert len(target.query_files) == 8
