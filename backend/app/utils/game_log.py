"""Patterns for the log a GDK game client writes.

The sibling of :mod:`app.utils.panel_log`: that module knows the OLED panel
service's log format, this one knows the game's. Both exist so the one place
that understands a foreign file is not buried inside a service.

Nothing here captures anything or knows about OBS. It turns lines appended to a
log into :class:`DetectedEvent` values, which is useful to anything that wants
to react to gameplay -- event capture is only the first caller.

Every game shipped so far logs through the same GDK client logger, so one set of
rules covers all of them::

    08/18/26 19:40:26.721 00 FortuneOx:19864 INF: [MessageQueue.Publish] msg[...SpinMsg]
    |--- date --||-- time ---| seq |-process:pid-| lvl |------- message -------|

Four things learned from reading real logs shape the default rules below, and
all of them are easy to get wrong:

**Anchor on the publish line, not on the message name.** Every published message
is echoed by a handful of ``StateMachine[...] [Non-queued] [<Msg>] not handled by
state [...]`` lines from the state machines that ignored it. Matching the bare
name yields five or more hits for one real event.

**Name the machines by pattern, not by literal.** The interesting state machines
are named after the feature they drive, so the free spin machine is
``FreeSpinStateMachineFreeSpin`` in FortuneOx and
``FreeSpinStateMachineCoinOnReelFS`` in HuffNPuffLink. A rule that pins either
name works on exactly one game.

**Some events are worth waiting for.** ``SpinDoneMsg`` is logged when the server
result arrives, which is a beat before the reels visibly settle. A rule can
declare ``delay_ms`` so a caller that screenshots on the event sees the finished
frame rather than a blurred one.

**Being visible is not the same as being worth a screenshot.** The rules here
are the whole visual vocabulary of the two games -- including the credit meter
after every win and the attract loop cycling to its next scene. A rule says
which of them it is with ``capture``, so a caller reacting to gameplay can have
all of them while event capture takes frames of only the moments a run is opened
for.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

__all__ = [
    "DEFAULT_RULES",
    "DetectedEvent",
    "EventRule",
    "LogLine",
    "LogRuleError",
    "compile_rules",
    "match",
    "parse_line",
    "resolve_rules",
]

# The log's own prefix. The number after the time is a within-millisecond
# counter that no caller needs, so it is matched but not captured.
LINE = re.compile(
    r"^(?P<date>\d{2}/\d{2}/\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2}\.\d{3})\s+\d+\s+"
    r"(?P<process>[^:\s]+):(?P<pid>\d+)\s+(?P<level>[A-Z]{3}):\s?(?P<message>.*)$"
)

_TIMESTAMP_FORMAT = "%m/%d/%y %H:%M:%S.%f"

# An event name becomes part of a filename, so keep it to something safe
# everywhere and readable in a directory listing.
_EVENT_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# A rule that waits longer than this is almost certainly a mistake: the caller
# blocks for that long before it can look at the next line.
MAX_DELAY_MS = 10_000


class LogRuleError(ValueError):
    """A rule declaration is malformed -- bad name, bad regex, or bad delay."""


@dataclass(frozen=True)
class LogLine:
    """One parsed line of the game log."""

    timestamp: datetime | None
    """When the game logged it. ``None`` when the stamp is unparseable."""

    level: str
    """``INF``, ``DBG``, ``WRN`` or ``ERR``."""

    process: str
    """Process name the game logs under, e.g. ``FortuneOx``."""

    message: str
    """Everything after the level, which is what rules match against."""

    raw: str
    """The original line, kept so a record can quote its own evidence."""


@dataclass(frozen=True)
class EventRule:
    """One recognisable event, and how to spot it."""

    event: str
    """Stable kebab-case name, e.g. ``spin-started``."""

    pattern: re.Pattern[str]
    """Matched against :attr:`LogLine.message`. Named groups become fields."""

    summary: str
    """Human sentence. ``{group}`` placeholders are filled from the match."""

    delay_ms: int = 0
    """How long to wait before acting, for events the screen lags behind."""

    capture: bool = True
    """Whether this event gets a screenshot and shows up in a run.

    Manually toggled per rule. ``True`` for every default rule; set to
    ``False`` on specific rules below to exclude that event from capture.
    """

    only_on_change: bool = False
    """Whether a repeat carrying the same values is the same event.

    For lines a game re-logs unchanged. ``[BetManager.UpdateCurrentBet]`` is the
    example: HuffNPuffLink writes it several times a round with an identical
    bet, and only the ones where a value actually moved are events.
    """


@dataclass(frozen=True)
class DetectedEvent:
    """A rule that matched a line, with whatever the pattern pulled out."""

    event: str
    summary: str
    fields: Mapping[str, str]
    line: LogLine
    delay_ms: int
    capture: bool = True
    only_on_change: bool = False


def parse_line(raw: str) -> LogLine | None:
    """Split one log line into its parts.

    Returns ``None`` for anything that is not a log line -- blank lines, and the
    continuation lines of a multi-line payload, both of which appear regularly.
    """
    matched = LINE.match(raw.rstrip("\r\n"))
    if matched is None:
        return None

    stamp = f"{matched['date']} {matched['time']}"
    try:
        # The game logs local time with no offset, so this is deliberately naive.
        timestamp: datetime | None = datetime.strptime(stamp, _TIMESTAMP_FORMAT)
    except ValueError:
        # A malformed stamp is not a reason to discard the line; the message is
        # still the interesting part.
        timestamp = None

    return LogLine(
        timestamp=timestamp,
        level=matched["level"],
        process=matched["process"],
        message=matched["message"],
        raw=raw.rstrip("\r\n"),
    )


def _summarise(template: str, fields: Mapping[str, str]) -> str:
    """Fill a rule's summary template, tolerating a placeholder with no group."""
    try:
        return template.format(**fields)
    except (KeyError, IndexError, ValueError):
        return template


def match(line: LogLine, rules: Sequence[EventRule]) -> DetectedEvent | None:
    """Return the first rule that recognises ``line``, if any.

    First rather than best: rules are ordered, so a game's own additions -- which
    :func:`resolve_rules` puts in front -- can claim a line before a broader
    default rule sees it.
    """
    for rule in rules:
        found = rule.pattern.search(line.message)
        if found is None:
            continue
        fields = {
            name: value
            for name, value in found.groupdict().items()
            if value is not None
        }
        return DetectedEvent(
            event=rule.event,
            summary=_summarise(rule.summary, fields),
            fields=MappingProxyType(fields),
            line=line,
            delay_ms=rule.delay_ms,
            capture=rule.capture,
            only_on_change=rule.only_on_change,
        )
    return None


def _rule_from(raw: Mapping[str, Any], *, where: str) -> EventRule:
    """Validate one declared rule."""
    event = raw.get("event")
    if not isinstance(event, str) or not _EVENT_NAME.match(event):
        raise LogRuleError(
            f"{where}: 'event' must be a lowercase kebab-case name, got {event!r}"
        )

    pattern = raw.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise LogRuleError(f"{where}: 'pattern' must be a non-empty string")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise LogRuleError(f"{where}: 'pattern' is not a valid regex: {exc}") from exc

    summary = raw.get("summary", event.replace("-", " ").capitalize())
    if not isinstance(summary, str):
        raise LogRuleError(f"{where}: 'summary' must be a string")

    delay_ms = raw.get("delay_ms", 0)
    if not isinstance(delay_ms, int) or isinstance(delay_ms, bool):
        raise LogRuleError(f"{where}: 'delay_ms' must be an integer")
    if not 0 <= delay_ms <= MAX_DELAY_MS:
        raise LogRuleError(f"{where}: 'delay_ms' must be between 0 and {MAX_DELAY_MS}")

    flags: dict[str, bool] = {}
    for flag, default in (("capture", True), ("only_on_change", False)):
        value = raw.get(flag, default)
        if not isinstance(value, bool):
            raise LogRuleError(f"{where}: {flag!r} must be true or false")
        flags[flag] = value

    return EventRule(
        event=event,
        pattern=compiled,
        summary=summary,
        delay_ms=delay_ms,
        **flags,
    )


def compile_rules(raw: Any, *, where: str) -> tuple[EventRule, ...]:
    """Validate a declared list of rules, as found in a game config."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise LogRuleError(f"{where} must be a JSON array")

    rules: list[EventRule] = []
    for index, item in enumerate(raw):
        location = f"{where}[{index}]"
        if not isinstance(item, dict):
            raise LogRuleError(f"{location} must be a JSON object")
        rules.append(_rule_from(item, where=location))
    return tuple(rules)


def resolve_rules(
    *,
    extra: Sequence[EventRule] = (),
    disabled: Sequence[str] = (),
) -> tuple[EventRule, ...]:
    """Combine the shipped rules with one game's additions and removals.

    A game's own rules come first so they can claim a line ahead of a default,
    which is how a game overrides a shipped rule rather than only adding to it.
    """
    excluded = {name.strip().casefold() for name in disabled}
    overridden = {rule.event.casefold() for rule in extra}
    return (
        *extra,
        *(
            rule
            for rule in DEFAULT_RULES
            if rule.event.casefold() not in excluded
            and rule.event.casefold() not in overridden
        ),
    )


# Namespace in front of a message class name, when the game logs one.
_QUALIFIER = r"(?:[\w.]+\.)?"


def _message(message_name: str) -> str:
    """Match the authoritative record of one message being handled.

    Two shapes count, and which one a game uses depends on which log it writes.
    The client logs (``FortuneOx_Client.log``) publish through ``MessageQueue``;
    the theme logs (``HuffNPuffLink_Theme.log``) use ``SyncMessagePublisher``,
    and surface some messages only as the state transition they caused::

        [MessageQueue.Publish] msg[GDK.Common.ServerAPI.SpinButtonMsg]
        StateMachine[IdleStateMachine] transitioned from [x] to [y] on event [...SpinButtonMsg]

    What is deliberately *not* matched is the third shape::

        StateMachine[GambleOfferStateMachine] [Non-queued] [...SpinButtonMsg] not handled by state [idleState]

    Every state machine that ignored the message logs one of those, so matching
    them turns one real event into five or more.

    Both accepted shapes can appear for a single event, and several state
    machines can transition on it. They arrive within a few milliseconds of each
    other, so the caller's debounce collapses them; the alternation is about
    working on every game rather than about being hit exactly once.

    The message class is usually fully qualified and the namespace differs
    between features (``GDK.Common.ServerAPI``, ``Theme.Common``,
    ``GDK.Common.Gamble.Core``, ``GDK.Server.PlatformAPI``), so only the
    trailing name is pinned. A few messages carry no namespace at all --
    ``msg[double_up_offer_accept]``, ``msg[WinBangDone]`` -- which is why the
    qualifier is optional rather than required.
    """
    return (
        rf"(?:\[(?:MessageQueue|SyncMessagePublisher)\.Publish\] msg\[{_QUALIFIER}{message_name}\]"
        rf"|transitioned from \[[^\]]+\] to \[[^\]]+\] on event \[{_QUALIFIER}{message_name}\])"
    )


def _state(machine: str, *, to: str, frm: str = r"[^\]]+") -> str:
    """Match one state machine arriving in a state.

    ``machine`` is a regex, not a literal, because the interesting machines are
    named after the feature they drive: the free spin machine is
    ``FreeSpinStateMachineFreeSpin`` in FortuneOx and
    ``FreeSpinStateMachineCoinOnReelFS`` in HuffNPuffLink, and a rule that
    pinned either name would work on exactly one game.
    """
    return rf"StateMachine\[{machine}\] transitioned from \[{frm}\] to \[{to}\]"


def _on(message_name: str) -> str:
    """The ``on event [...]`` tail of a transition, for pinning which one."""
    return rf" on event \[{_QUALIFIER}{message_name}\]"


# Ordered: the first match wins, so anything narrow goes before anything broad.
#
# Deliberately a *visual* list, not everything the log names. The game logs
# plenty of internal bookkeeping between one visible change and the next -- the
# server round trip behind a spin, the raw reel-stop data before the animation
# plays, the bet confirmation a few hundred milliseconds after the bet that
# caused it. None of that is here: each of those produced a frame
# indistinguishable from the visual event either side of it. Every rule below
# corresponds to something a person watching the screen would see change.
#
# Every rule captures by default. Set ``capture=False`` on a rule below to
# exclude that event from screenshots and the captured-events list without
# removing it from the recognised vocabulary.
DEFAULT_RULES: tuple[EventRule, ...] = (
    EventRule(
        event="game-started",
        pattern=re.compile(r"-{4,}\s+(?P<game>.+?)\s+Client Start\s+-{4,}"),
        summary="{game} client started",
    ),
    # --- the spin cycle ---------------------------------------------------
    EventRule(
        event="spin-started",
        pattern=re.compile(_message("SpinButtonMsg")),
        summary="Spin requested",
    ),
    EventRule(
        event="reels-stopped",
        pattern=re.compile(
            _state(
                "SlotGameStateMachine", frm="stateSpinWithStops", to="stateReelSpinDone"
            )
        ),
        summary="Reels stopped",
        # SpinDoneMsg lands when the result does, a beat before the reels settle.
        delay_ms=800,
    ),
    EventRule(
        event="free-spin-reels-stopped",
        pattern=re.compile(
            _state(
                r"FreeSpinStateMachine\w*",
                frm="stateSpinWithStops",
                to="stateReelSpinDone",
            )
        ),
        summary="Free spin reels stopped",
        delay_ms=800,
    ),
    EventRule(
        event="win-collected",
        # Logged bare in FortuneOx and published as a message in HuffNPuffLink,
        # so the brackets are all the two shapes have in common. The free spin
        # version is deliberately not matched: it only ever appears as the
        # per-feature `FreeSpinWinBangDone_CoinOnReelFS`, and only on the
        # `not handled by state` echo lines this module exists to ignore.
        pattern=re.compile(r"\[\w*WinBangDone\]"),
        summary="Win meter finished counting up",
    ),
    # --- what the player set ----------------------------------------------
    EventRule(
        event="bet-changed",
        pattern=re.compile(
            r"BetChangeMsg betData:\s*denom:\s*(?P<denom>[\d.]+)\s+"
            r"units:\s*(?P<units>\d+).*?totalBetValue:\s*(?P<total_bet>[\d.]+)"
        ),
        summary="Bet changed to {total_bet} ({units} units at denom {denom})",
    ),
    EventRule(
        event="denomination-changed",
        pattern=re.compile(
            r"\[WagerGameApp\.UpdateDenom\] New denom\[(?P<denom>[\d.]+)\]"
            r" Did denom Change\[True\]"
        ),
        summary="Denomination changed to {denom}",
    ),
    EventRule(
        event="paytable-changed",
        pattern=re.compile(
            r"\[WagerGameApp\.UpdatePayTable\] current denom\[(?P<denom>[\d.]+)\]"
            r" current paytableId\[(?P<paytable>[^\]]+)\]"
        ),
        summary="Paytable is now {paytable}",
        only_on_change=True,
    ),
    EventRule(
        event="credits-changed",
        pattern=re.compile(
            f"{_message('CreditMeterSetMsg')}|{_message('BalanceNotificationMsg')}"
        ),
        summary="Credit meter set",
    ),
    # --- gamble -----------------------------------------------------------
    EventRule(
        event="gamble-offered",
        pattern=re.compile(_message("OfferMsg")),
        summary="Gamble offered",
        capture=False,
    ),
    EventRule(
        event="gamble-accepted",
        pattern=re.compile(_message("double_up_offer_accept")),
        summary="Gamble accepted",
        # The double-up screen animates in.
        delay_ms=600,
    ),
    EventRule(
        event="gamble-declined",
        pattern=re.compile(
            f"{_message('DontPlayMsg')}|{_message('double_up_offer_decline')}"
        ),
        summary="Gamble declined",
        capture=False,

    ),
    EventRule(
        event="gamble-picked",
        pattern=re.compile(
            r"\[(?:MessageQueue|SyncMessagePublisher)\.Publish\] "
            r"msg\[RED_BLACK_(?P<pick>[A-Z]+)_CARD\]"
        ),
        summary="Gamble pick: {pick}",
    ),
    EventRule(
        event="gamble-result",
        pattern=re.compile(_state("GambleStateMachine", to="displayResultsState")),
        summary="Gamble result shown",
        # The card turns over before the result is readable.
        delay_ms=600,
    ),
    EventRule(
        event="gamble-ended",
        pattern=re.compile(_message("GambleEndMsg")),
        summary="Gamble finished",
    ),
    # --- features ---------------------------------------------------------
    EventRule(
        event="bonus-triggered",
        # The decision transition fires on every spin; arriving in the
        # triggered state is the one that means a bonus was really hit.
        pattern=re.compile(
            _state("BonusTriggerStateMachine", to="stateBonusTriggered")
        ),
        summary="Bonus triggered",
        delay_ms=800,
    ),
    EventRule(
        event="free-spins-entered",
        pattern=re.compile(
            _state(
                r"FreeSpinStateMachine\w*",
                frm="stateIdle",
                to="state(?:Start|Setup)",
            )
        ),
        summary="Free spins entered",
        delay_ms=800,
    ),
    EventRule(
        event="free-spins-ended",
        # The outro is the summary screen: what the feature paid.
        pattern=re.compile(_state(r"FreeSpinStateMachine\w*", to="stateTransitionOut")),
        summary="Free spins finished",
        delay_ms=800,
    ),
    EventRule(
        event="progressive-awarded",
        pattern=re.compile(_message("ProgressiveAwardResponseMsg")),
        summary="Progressive awarded",
        delay_ms=800,
    ),
    EventRule(
        event="mystery-symbols-revealed",
        pattern=re.compile(
            _state(r"MysterySymbolStateMachine\w*", to=r"statePerformMyst\w*Reveal")
        ),
        summary="Mystery symbols revealed",
    ),
    EventRule(
        event="help-opened",
        pattern=re.compile(
            _state(
                "FeatureSceneLoaderStateMachineHelp",
                frm="statePreLoad",
                to="statePlay",
            )
        ),
        summary="Help and paytable screens opened",
        capture=False,

    ),
    EventRule(
        event="help-closed",
        pattern=re.compile(
            _state(
                "FeatureSceneLoaderStateMachineHelp",
                frm="statePlay",
                to="stateUnloadDecision",
            )
        ),
        summary="Help and paytable screens closed",
        capture=False,

    ),
    EventRule(
        event="feature-scene-shown",
        # Everything a game loads over the reels -- a wheel, a bonus, a pick --
        # arrives through one of these loaders, named after the feature. Help
        # is one too, which is why its own rules sit above this one.
        pattern=re.compile(
            _state(
                r"FeatureSceneLoaderStateMachine(?P<feature>\w+)",
                frm="statePreLoad",
                to="statePlay",
            )
        ),
        summary="{feature} scene shown",
        delay_ms=800,
        capture=False,

    ),
    # --- the machine around the game --------------------------------------
    EventRule(
        event="attract-started",
        pattern=re.compile(
            _state("AttractStateMachine", to="stateSequenceStartDelay")
            + _on("AttractEnabledMsg")
        ),
        summary="Attract mode started",
        # The first attract scene takes a moment to come up.
        delay_ms=1000,
        capture=False,

    ),
    EventRule(
        event="attract-ended",
        pattern=re.compile(
            _state("AttractStateMachine", to="stateDisable") + _on("AttractDisabledMsg")
        ),
        summary="Attract mode ended",
        capture=False,

    ),
    EventRule(
        event="attract-looped",
        pattern=re.compile(
            _state(
                "AttractStateMachine",
                frm="stateSequenceEnd",
                to="stateSequenceStartDelay",
            )
        ),
        summary="Attract sequence looped",
        capture=False,

    ),
    EventRule(
        event="service-requested",
        pattern=re.compile(_message("ServiceRequestedMsg")),
        summary="Service requested",
        capture=False,

    ),
    EventRule(
        event="service-cleared",
        pattern=re.compile(_message("ServiceNoLongerRequestedMsg")),
        summary="Service no longer requested",
        capture=False,
        
    ),
    EventRule(
        event="locked-up",
        pattern=re.compile(_message("LockUpMsg")),
        summary="Machine locked up",
        capture=False,

    ),
    EventRule(
        event="lockup-cleared",
        pattern=re.compile(_message("LockUpClearMsg")),
        summary="Lockup cleared",
        capture=False,
    ),
    EventRule(
        event="screen-covered",
        pattern=re.compile(_message("CoverScreenMsg")),
        summary="Screen covered",
        capture=False,
    ),
    EventRule(
        event="game-suspended",
        pattern=re.compile(_message("SuspendMsg")),
        summary="Game suspended",
        capture=False,

    ),
    EventRule(
        event="game-resumed",
        pattern=re.compile(_message("ResumeMsg")),
        summary="Game resumed",
        capture=False,

    ),
    EventRule(
        event="demo-menu-shown",
        pattern=re.compile(_message("DEMO_MENU_SHOWING")),
        summary="Demo menu shown",
        capture=False,

    ),
    EventRule(
        event="demo-menu-hidden",
        pattern=re.compile(_message("DEMO_MENU_HIDING")),
        summary="Demo menu hidden",
        capture=False,

    ),
)
