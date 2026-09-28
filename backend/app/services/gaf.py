"""Driving the running game through GAF.

Where :mod:`app.services.game_input` aims a synthetic click at a measured
point and :mod:`app.services.ideck` posts a mouse message at the button panel,
this calls the game's own methods: the simulator hosts an automation service,
a local ``NRobot.Server.exe`` translates Robot Framework keywords into it, and
the game answers as if a finger had touched the glass. No coordinates to
re-measure, no cursor to steal, and no integrity-level bargain -- the calls
are XML-RPC over loopback, not injected input.

What the module owns is the **order** and the **session**. The protocol lives
in :mod:`app.utils.nrobot` and the object dictionary in
:mod:`app.utils.gaf_objects`; neither knows this is a slot machine.

Six things it exists to get right:

* **A win holds the game in play.** A losing spin leaves ``statePlaying`` on
  its own; a winning one stays there until the win is collected. A settle
  loop waiting only for idle therefore times out on exactly the spins worth
  having, and looks like a hang. :func:`spin` waits for *either*.
* **Nothing is spun on top of a spin that has not finished.** The same hold
  that makes a win readable makes the *next* press dangerous: its result
  belongs to the spin still running. Measured -- a free-spin bonus outlasted
  the settle timeout and the spin after it landed mid-bonus. The pre-press
  state read is what catches it, so the check is free.
* **A spin does not start the instant the button is pressed.** The game is
  still idle for a beat afterwards, so a settle loop that starts by asking
  "are we idle?" answers yes and reports a spin that never happened.
  :func:`_await_departure` waits for the game to leave the state it was in
  before the press, and only then does the settle loop run.
* **Nothing is retried after the press.** The session is checked for life
  *before* anything is pressed -- one cheap state read, which doubles as the
  pre-press state the departure check needs -- so a dead session is found and
  replaced while that is still free. Past the press there is no automatic
  retry, because a retried spin is a second spin.
* **A failed keyword is not an exception on the wire.** It is a reply whose
  status says FAIL, and several keywords answer a plain ``"False"`` on a
  PASS. Both are checked; see :mod:`app.utils.nrobot`.
* **Two processes here are not ours.** ``NRobot.Server.exe`` is started by
  its own batch file and the object-query dictionary lives in a Perforce
  workspace outside this repo. :func:`status` says plainly when either is
  absent, the same bargain ``/api/obs/status`` and ``/api/ocr/status`` make.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from app.config.gaf import GafTarget, GafTargetError, resolve_target
from app.config.game_config import (
    ActiveGameSelectionError,
    GameConfig,
    GameConfigError,
    load_game_config,
)
from app.config.runtime import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    GafKeywordError,
    GafNotConfiguredError,
    GafNotIdleError,
    GafSessionError,
    GafUnavailableError,
)
from app.schemas.gaf import (
    GafMeters,
    GafQueryFile,
    GafState,
    GafStatus,
    SpinOutcome,
    SpinResult,
    TakeWinResult,
)
from app.utils import gaf_objects
from app.utils.nrobot import KeywordReply, RemoteError, RemoteLibrary

logger = get_logger("gaf")

# --- the libraries this service speaks to ---------------------------------
# One endpoint per library on the NRobot server. Only ConnectGame needs
# initialising; every other library resolves the live client per call, which
# is why there is no per-library setup here.

CONNECT = "RFTestCode.ConnectGame.ConnectGameLibrary"
IDECK = "RFTestCode.IDeck.IDeckLibrary"
STATE = "RFTestCode.GameState.GameStateLibrary"
METER = "RFTestCode.GameMeter.GameMeterLibrary"

IDLE_MACHINE = "IdleStateMachine"
SLOT_MACHINE = "SlotGameStateMachine"
GAMBLE_MACHINE = "GambleOfferStateMachine"

PLAYING = "statePlaying"
"""The idle machine's state while a spin is running *or* a win is uncollected."""

OFFER = "offerState"
"""The gamble machine's state while a win is waiting to be taken."""


@dataclass(frozen=True, slots=True)
class _Session:
    """One open automation session."""

    target: GafTarget
    object_count: int
    opened_at: float


# --- module state ---------------------------------------------------------
# Singletons, like every other service here: the session, the resolved target
# and the merged object dictionary, all dropped by `reset()`.

_session: _Session | None = None
_target: GafTarget | None = None
_queries: gaf_objects.QuerySet | None = None
_lock: asyncio.Lock | None = None
_stale = False
"""Set when the active game changes under a live session. Dropping the
session needs the network and `games.select()` is synchronous, so the next
action tears it down and reconnects instead."""


def _get_lock() -> asyncio.Lock:
    """The module lock, created lazily -- each test gets a fresh event loop."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


# --- configuration --------------------------------------------------------


def _game_config() -> GameConfig:
    """Load the active game's config, as a GAF-shaped error."""
    try:
        active = settings.ideck_active_game
        return load_game_config(settings.ideck_game_config_path_for(active))
    except ActiveGameSelectionError as exc:
        raise GafNotConfiguredError(str(exc)) from exc
    except GameConfigError as exc:
        raise GafNotConfiguredError(f"Config for the active game: {exc}") from exc


def _resolve() -> GafTarget:
    """The active game's automation target, cached until the game changes."""
    global _target
    if _target is None:
        config = _game_config()
        if not config.gaf:
            raise GafNotConfiguredError(
                f"{config.name} declares no 'gaf' block in {config.path}, so it "
                "cannot be driven through GAF. Add one naming the game's "
                "automation host and port and the root of its AGTF "
                "object-query workspace."
            )
        try:
            _target = resolve_target(config.name, config.gaf, settings)
        except GafTargetError as exc:
            raise GafNotConfiguredError(str(exc)) from exc
    return _target


def _load_queries(target: GafTarget) -> gaf_objects.QuerySet:
    """The merged object dictionary, cached until the game changes.

    Read on the calling thread: it is a few hundred kilobytes of JSON off a
    local disk, and it happens once per game rather than once per call.
    """
    global _queries
    if _queries is None:
        try:
            _queries = gaf_objects.load_query_set(
                target.general_queries, target.generic_queries
            )
        except gaf_objects.ObjectQueryMissing as exc:
            raise GafUnavailableError(
                f"{exc} Is the AGTF Perforce workspace synced, and is "
                f"'gaf.object_query_root' in the config for {target.game} "
                "pointing at it?"
            ) from exc
        except gaf_objects.ObjectQueryError as exc:
            raise GafSessionError(str(exc)) from exc
    return _queries


# --- calling keywords -----------------------------------------------------


def _library(name: str) -> RemoteLibrary:
    """A handle on one keyword library. Cheap; holds no connection."""
    return RemoteLibrary(
        settings.GAF_SERVER_URL,
        name,
        timeout=settings.GAF_REQUEST_TIMEOUT_SECONDS,
    )


def _unreachable(exc: RemoteError) -> GafUnavailableError:
    """The one failure that looks like a bug but is a deployment detail."""
    return GafUnavailableError(
        f"{exc} NRobot.Server.exe hosts the automation keywords and is started "
        "by NRobotStartUpScript.bat, not by this backend -- start it, then try "
        "again."
    )


async def _try(library: str, keyword: str, *args: Any) -> KeywordReply:
    """Run a keyword, returning its reply whether it passed or failed.

    Blocking XML-RPC, so it goes on a worker thread. Only the transport
    failure is translated here; a FAIL is the caller's to interpret.
    """
    remote = _library(library)
    try:
        return await asyncio.to_thread(remote.try_run, keyword, *args)
    except RemoteError as exc:
        raise _unreachable(exc) from exc


async def _run(library: str, keyword: str, *args: Any) -> KeywordReply:
    """Run a keyword that must pass."""
    reply = await _try(library, keyword, *args)
    if not reply.passed:
        raise GafKeywordError(f"{keyword}: {reply.error or 'the game gave no reason'}")
    return reply


async def _run_true(library: str, keyword: str, *args: Any) -> KeywordReply:
    """Run a keyword that must pass *and* answer yes.

    Several of these answer ``"False"`` on a PASS -- the keyword ran fine and
    the game declined. Treating that as success is the subtlest way to report
    a press that never happened.
    """
    reply = await _run(library, keyword, *args)
    if not reply.truthy:
        raise GafKeywordError(
            f"{keyword} ran but answered {reply.text or 'nothing'!r}; the game "
            "declined to do it"
        )
    return reply


async def _state(machine: str) -> str | None:
    """One state machine's current state, or ``None`` if it cannot be read."""
    reply = await _try(STATE, "GETCURRENTSTATE", machine)
    if not reply.passed:
        return None
    return reply.text.strip() or None


# --- the session ----------------------------------------------------------


async def _teardown() -> None:
    """Close the session on the game's side. Never raises.

    Best effort on purpose: it runs on the failure path and at shutdown, and
    a teardown that throws would mask whatever it was cleaning up after.
    """
    for keyword in ("DESTROYGAMECLIENT", "DISCONNECTGAMECLIENTFROMSERVER"):
        try:
            reply = await _try(CONNECT, keyword)
        except GafUnavailableError:
            return  # The server is gone; there is nothing left to close.
        if not reply.passed:
            logger.debug("%s during teardown: %s", keyword, reply.error)


async def _open(target: GafTarget) -> _Session:
    """Open a session against one game, retrying a cold start.

    The four steps are order-sensitive: ``INIT`` seeds the host and port and
    without it the connect raises "no session has been initialized"; the two
    initialise calls take the two object dictionaries, which are separate and
    both required.
    """
    queries = _load_queries(target)
    attempts = max(1, settings.GAF_CONNECT_ATTEMPTS)
    last = ""

    for attempt in range(1, attempts + 1):
        if attempt > 1:
            # A half-open session from a crashed client blocks the next one,
            # so a retry clears the ground first. Not done on the first
            # attempt: that would evict a session another tool legitimately
            # holds every time this service connects.
            await _teardown()
            await asyncio.sleep(settings.GAF_CONNECT_RETRY_SECONDS)
        try:
            await _run(CONNECT, "INIT", target.host, target.port)
            await _run_true(
                CONNECT, "CONNECTGAMECLIENTTOSERVER", target.host, target.port
            )
            await _run(
                CONNECT,
                "INITIALIZEGAMECLIENT",
                target.game_type,
                queries.general_json(),
            )
            await _run(
                CONNECT,
                "INITIALIZEGENERICGAMECLIENT",
                queries.generic_json(),
                "",
                target.gdk_version,
            )
        except GafKeywordError as exc:
            last = exc.message
            logger.info(
                "GAF connect to %s attempt %d/%d failed: %s",
                target.endpoint,
                attempt,
                attempts,
                last,
            )
            continue

        count = len(queries.names())
        logger.info(
            "GAF session open to %s (%s, GDK %s, %d objects)",
            target.endpoint,
            target.game_type,
            target.gdk_version,
            count,
        )
        return _Session(target=target, object_count=count, opened_at=time.monotonic())

    raise GafSessionError(
        f"Could not open an automation session against {target.game} at "
        f"{target.endpoint} in {attempts} attempt(s). Last failure -- {last} "
        "Is the game running, and is anything else (RUSTClient.exe) holding a "
        "session against it?"
    )


async def _ensure_session() -> tuple[_Session, str | None]:
    """Return a live session and the game's current idle state.

    The state read is not incidental: it is the liveness check *and* the
    before-picture the departure wait needs, so reusing a session costs one
    keyword rather than two. A session that fails it is torn down and
    reopened -- which is safe here precisely because nothing has been pressed
    yet.
    """
    global _session, _stale

    target = _resolve()
    if _session is not None and (_stale or _session.target != target):
        logger.info("Active game changed under a GAF session; reopening")
        await _teardown()
        _session = None
    _stale = False

    if _session is not None:
        reply = await _try(STATE, "GETCURRENTSTATE", IDLE_MACHINE)
        if reply.passed:
            return _session, reply.text.strip() or None
        logger.info("Held GAF session no longer answers (%s); reopening", reply.error)
        await _teardown()
        _session = None

    _session = await _open(target)
    return _session, await _state(IDLE_MACHINE)


# --- waiting --------------------------------------------------------------


async def _win_offered(target: GafTarget) -> bool:
    """Whether a win is sitting there waiting to be collected.

    Asked two ways because either can answer first: the gamble machine moves
    to its offer state, and the take-win button becomes interactable. A win
    that is offered but whose button has not lit yet is still a win.
    """
    if await _state(GAMBLE_MACHINE) == OFFER:
        return True
    reply = await _try(IDECK, "ISNONWAGERBUTTONINTERACTABLE", target.take_win_button)
    return reply.truthy


async def _await_departure(before: str | None, target: GafTarget) -> bool:
    """Wait for the game to actually start playing after a press.

    The press is acknowledged before the game has moved, so without this the
    settle loop's first question -- "are we idle?" -- is answered yes by the
    state the game was *already* in, and a spin still turning is reported as
    finished. Returns whether the game was seen to move.
    """
    poll = settings.GAF_SETTLE_POLL_SECONDS
    deadline = time.monotonic() + settings.GAF_SPIN_START_SECONDS
    while time.monotonic() < deadline:
        current = await _state(IDLE_MACHINE)
        if current == PLAYING or (current is not None and current != before):
            return True
        # A spin short enough to finish inside this window still leaves a win
        # on the table, which is a departure by any useful definition.
        if await _win_offered(target):
            return True
        await asyncio.sleep(poll)
    return False


async def _settle(target: GafTarget, timeout: float) -> tuple[SpinOutcome, str | None]:
    """Wait for a spin to finish, either way it can finish."""
    poll = settings.GAF_SETTLE_POLL_SECONDS
    deadline = time.monotonic() + timeout
    idle = await _state(IDLE_MACHINE)
    while True:
        if idle is not None and idle != PLAYING:
            return SpinOutcome.IDLE, idle
        if await _win_offered(target):
            return SpinOutcome.WIN_OFFERED, idle
        if time.monotonic() >= deadline:
            return SpinOutcome.TIMEOUT, idle
        await asyncio.sleep(poll)
        idle = await _state(IDLE_MACHINE)


async def _await_idle(timeout: float) -> str | None:
    """Wait for the game to leave play; used after a win is collected."""
    poll = settings.GAF_SETTLE_POLL_SECONDS
    deadline = time.monotonic() + timeout
    while True:
        idle = await _state(IDLE_MACHINE)
        if idle is not None and idle != PLAYING:
            return idle
        if time.monotonic() >= deadline:
            return idle
        await asyncio.sleep(poll)


# --- meters ---------------------------------------------------------------


async def _meters() -> GafMeters | None:
    """Read all three meters, or ``None`` if none of them could be read.

    Never raises: a meter reading is a bonus on top of an action that has
    already happened, and losing it must not turn a completed spin into a
    failure.
    """
    values: dict[str, str | None] = {}
    for field, meter in (
        ("credit", "CreditMeter"),
        ("bet", "BetMeter"),
        ("win", "WinMeter"),
    ):
        try:
            reply = await _try(METER, "METERINFO", meter, "value")
        except GafUnavailableError:
            return None
        values[field] = reply.text.strip() or None if reply.passed else None
    if not any(values.values()):
        return None
    return GafMeters(**values)


async def _maybe_meters(requested: bool | None) -> GafMeters | None:
    """Read the meters unless this call, or the environment, says not to."""
    wanted = settings.GAF_READ_METERS if requested is None else requested
    return await _meters() if wanted else None


# --- public API -----------------------------------------------------------


async def status() -> GafStatus:
    """Report what the service can see of the chain.

    Never raises and never opens a session -- a dashboard polls this, and a
    status read that connected to the game as a side effect would be a
    surprising thing for a status read to do.
    """
    base: dict[str, Any] = {
        "game": "",
        "server_url": settings.GAF_SERVER_URL,
        "host": settings.GAF_HOST,
        "port": settings.GAF_PORT,
        "game_type": settings.GAF_GAME_TYPE,
        "gdk_version": settings.GAF_GDK_VERSION,
        "connected": _session is not None,
    }
    try:
        base["game"] = settings.ideck_active_game
    except ActiveGameSelectionError as exc:
        return GafStatus(state=GafState.NOT_CONFIGURED, detail=str(exc), **base)

    try:
        target = _resolve()
    except (GafNotConfiguredError, GafUnavailableError) as exc:
        return GafStatus(state=GafState.NOT_CONFIGURED, detail=exc.message, **base)

    base |= {
        "host": target.host,
        "port": target.port,
        "game_type": target.game_type,
        "gdk_version": target.gdk_version,
    }
    files = [
        GafQueryFile(path=str(path), group=group, present=path.is_file())
        for group, paths in (
            ("general", target.general_queries),
            ("generic", target.generic_queries),
        )
        for path in paths
    ]
    base["query_files"] = files

    # The reason travels, not just the verdict: a refused connection and a
    # server that does not host the automation keywords are the same state
    # with different fixes -- start it, versus start it from its own
    # directory. Only the reason says which.
    unreachable = await asyncio.to_thread(_library(CONNECT).probe)
    if unreachable is not None:
        return GafStatus(
            state=GafState.UNREACHABLE,
            detail=(
                f"{unreachable} NRobot.Server.exe is started by "
                "NRobotStartUpScript.bat, not by this backend."
            ),
            **base,
        )

    absent = [one for one in files if not one.present]
    if absent:
        return GafStatus(
            state=GafState.FILES_MISSING,
            detail=(
                f"{len(absent)} of {len(files)} object-query files are missing "
                f"under {target.query_root}. Without them the game client "
                "cannot resolve a single control by name."
            ),
            **base,
        )

    held = _session
    if held is None:
        return GafStatus(
            state=GafState.DISCONNECTED,
            detail=(
                f"Ready to drive {target.game} at {target.endpoint}; no session "
                "is open yet. Spinning opens one."
            ),
            **base,
        )

    base["object_count"] = held.object_count
    # Deliberately not taken: a spin holds the lock for the length of the
    # spin, and a status poll must not queue behind it.
    if _get_lock().locked():
        return GafStatus(
            state=GafState.READY,
            detail=f"Connected to {target.game} at {target.endpoint}; busy.",
            **base,
        )
    idle = await _state(IDLE_MACHINE)
    return GafStatus(
        state=GafState.READY,
        idle_state=idle,
        detail=(
            f"Connected to {target.game} at {target.endpoint}"
            + (f"; {idle}" if idle else "")
        ),
        **base,
    )


async def connect() -> GafStatus:
    """Open a session now, rather than on the next action."""
    async with _get_lock():
        await _ensure_session()
    return await status()


async def disconnect() -> GafStatus:
    """Close the session, leaving the game alone.

    Worth having its own door: a session left open blocks the next client,
    so a run that is finished with the game should say so rather than hold it
    until this process exits.
    """
    global _session
    async with _get_lock():
        if _session is not None:
            await _teardown()
            _session = None
    return await status()


async def spin(
    *,
    settle: bool = True,
    force: bool = False,
    timeout_seconds: float | None = None,
    read_meters: bool | None = None,
) -> SpinResult:
    """Spin the reels, and wait for the spin to finish.

    Everything the caller needs is done here: a session is opened if there
    isn't one, the game is checked to be idle, the mechanical spin button is
    pressed, and the wait allows for the two ways a spin can end -- back to
    idle, or held in play with a win to collect. ``settle=False`` returns at
    the press, which is a press and not a result.
    """
    started = time.monotonic()
    timeout = timeout_seconds or settings.GAF_SETTLE_TIMEOUT_SECONDS

    async with _get_lock():
        session, before = await _ensure_session()
        target = session.target
        if before == PLAYING and not force:
            raise _still_playing(target)

        # The one irreversible step. Nothing past here is retried.
        await _run_true(IDECK, "PRESSMECHANICALSPINBUTTON")
        logger.info("GAF spin pressed on %s (was %s)", target.game, before or "unknown")

        if not settle:
            return SpinResult(
                game=target.game,
                pressed=True,
                outcome=SpinOutcome.IDLE,
                settled=False,
                win_offered=False,
                meters=None,
                elapsed_ms=round((time.monotonic() - started) * 1000),
                detail="Spin pressed; the result was not waited for.",
            )

        departed = await _await_departure(before, target)
        outcome, idle = await _settle(target, timeout)
        game_state = await _state(SLOT_MACHINE)
        meters = await _maybe_meters(read_meters)

    elapsed_ms = round((time.monotonic() - started) * 1000)
    detail = _spin_detail(outcome, idle, departed, elapsed_ms)
    logger.info("GAF spin on %s: %s in %dms", target.game, outcome.value, elapsed_ms)
    return SpinResult(
        game=target.game,
        pressed=True,
        outcome=outcome,
        settled=True,
        idle_state=idle,
        game_state=game_state,
        win_offered=outcome is SpinOutcome.WIN_OFFERED,
        meters=meters,
        elapsed_ms=elapsed_ms,
        detail=detail,
    )


def _still_playing(target: GafTarget) -> GafNotIdleError:
    """Refuse to spin on top of a game that has not finished.

    Measured, not theoretical: a free-spin bonus holds ``statePlaying`` well
    past the settle timeout, and the spin after the one that timed out landed
    while the bonus was still running. An uncollected win holds it the same
    way. Pressing into either is a press whose result belongs to the previous
    spin, so it is refused with the two things that release the game.
    """
    return GafNotIdleError(
        f"{target.game} is still in {PLAYING}, so spinning now would press "
        "into a spin that has not finished. Either a win is waiting to be "
        "collected -- take it first -- or a bonus is still running, which can "
        "outlast GAF_SETTLE_TIMEOUT_SECONDS. Spin with force to press anyway."
    )


def _spin_detail(
    outcome: SpinOutcome, idle: str | None, departed: bool, elapsed_ms: int
) -> str:
    """Say what happened in the terms someone reading a panel needs."""
    seconds = elapsed_ms / 1000
    if outcome is SpinOutcome.WIN_OFFERED:
        return (
            f"Spin finished in {seconds:.1f}s with a win waiting to be "
            "collected. Take the win to release the game."
        )
    if outcome is SpinOutcome.TIMEOUT:
        return (
            f"Spin did not settle within {seconds:.0f}s; the game is still in "
            f"{idle or 'an unreadable state'}. The usual cause is a bonus: a "
            "free-spin round holds the game in play for as long as it takes "
            "to play out, which can be well past GAF_SETTLE_TIMEOUT_SECONDS. "
            "The next spin is refused until it finishes."
        )
    if not departed:
        return (
            f"Spin pressed and the game reported {idle or 'idle'} without ever "
            f"leaving idle ({seconds:.1f}s). The press was accepted, so this is "
            "most likely a spin too short to observe -- check the credit meter."
        )
    return f"Spin finished in {seconds:.1f}s with no win ({idle or 'idle'})."


async def take_win(
    *,
    force: bool = False,
    settle: bool = True,
    timeout_seconds: float | None = None,
    read_meters: bool | None = None,
) -> TakeWinResult:
    """Collect a win that is waiting to be taken.

    Nothing to collect is a *result*, not an error: the button is reported as
    not interactable and nothing is pressed, which is a different fact from a
    press that failed. ``force`` presses anyway, for a theme whose button
    does not advertise itself.
    """
    started = time.monotonic()
    timeout = timeout_seconds or settings.GAF_SETTLE_TIMEOUT_SECONDS

    async with _get_lock():
        session, _ = await _ensure_session()
        target = session.target
        button = target.take_win_button

        probe = await _try(IDECK, "ISNONWAGERBUTTONINTERACTABLE", button)
        interactable = probe.truthy
        if not interactable and not force:
            return TakeWinResult(
                game=target.game,
                button=button,
                interactable=False,
                pressed=False,
                settled=False,
                idle_state=await _state(IDLE_MACHINE),
                meters=None,
                elapsed_ms=round((time.monotonic() - started) * 1000),
                detail=(
                    f"{button} is not interactable, so there is nothing to "
                    "collect. Spin first, or force the press."
                ),
            )

        await _run_true(IDECK, "PRESSNONWAGERBUTTON", button)
        logger.info("GAF pressed %s on %s", button, target.game)

        idle = await _await_idle(timeout) if settle else None
        meters = await _maybe_meters(read_meters)

    elapsed_ms = round((time.monotonic() - started) * 1000)
    collected = idle is not None and idle != PLAYING
    detail = f"Pressed {button}"
    if not settle:
        detail += "; the result was not waited for."
    elif collected:
        detail += f" and the game returned to {idle} in {elapsed_ms / 1000:.1f}s."
    else:
        detail += (
            f" but the game is still in {idle or 'an unreadable state'} after "
            f"{elapsed_ms / 1000:.0f}s. There may be a second award to collect."
        )
    return TakeWinResult(
        game=target.game,
        button=button,
        interactable=interactable,
        pressed=True,
        settled=settle,
        idle_state=idle,
        meters=meters,
        elapsed_ms=elapsed_ms,
        detail=detail,
    )


def reset() -> None:
    """Drop every cache and the lock, without touching the network.

    Called autouse by the test suite, so it must stay offline. The live
    session is dropped, not closed -- :func:`shutdown` is what closes one.
    """
    global _session, _target, _queries, _lock, _stale
    if _session is not None:
        logger.warning("Dropping a live GAF session without closing it")
    _session = None
    _target = None
    _queries = None
    _lock = None
    _stale = False


def reset_game_config() -> None:
    """Forget the per-game target after a runtime game switch.

    A held session is bound to the *old* game's endpoint, so it is marked
    stale rather than dropped: closing it needs the network and the caller
    (``games.select``) is synchronous, so the next action does it.
    """
    global _target, _queries, _stale
    _target = None
    _queries = None
    _stale = _session is not None


async def shutdown() -> None:
    """Close a live session on the way out. Never raises.

    Worth doing at shutdown rather than leaving to the garbage collector: a
    session left open on the game's side blocks the next client, so a backend
    restart would otherwise cost a game restart too.
    """
    global _session
    if _session is not None:
        logger.info("Closing the GAF session for %s", _session.target.game)
        try:
            await _teardown()
        except Exception:
            logger.warning("GAF teardown failed during shutdown", exc_info=True)
    _session = None
    _target = None
    _queries = None
    _stale = False
