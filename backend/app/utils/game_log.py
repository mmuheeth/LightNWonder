"""Patterns for the log a GDK game client writes, turning lines into
:class:`DetectedEvent` values. Sibling of :mod:`app.utils.panel_log`."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

__all__ = [
    "DEFAULT_RULES",
    "PAYTABLE_LOADED",
    "TOUCH_REGISTERED",
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
    """Whether this event gets a screenshot. ``False`` keeps it in the
    recognised vocabulary without capturing -- the machine's own chrome."""

    only_on_change: bool = False
    """Whether a repeat carrying the same values is the same event -- for lines
    a game re-logs unchanged, e.g. HuffNPuffLink's repeated identical-bet
    ``[BetManager.UpdateCurrentBet]``."""


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
    """Split one log line into its parts, or ``None`` for a blank line or a
    multi-line payload's continuation."""
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
    """Return the first rule that recognises ``line``, if any -- rules are
    ordered so a game's own additions can claim a line before a default does."""
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
    """Combine the shipped rules with one game's additions and removals. A game's own
    rules come first, so they can override a shipped rule rather than only add to it."""
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
    """Match one message being handled: a publish line or the state transition it
    caused, never the "not handled by state" echo."""
    return (
        rf"(?:\[(?:MessageQueue|SyncMessagePublisher)\.Publish\] msg\[{_QUALIFIER}{message_name}\]"
        rf"|transitioned from \[[^\]]+\] to \[[^\]]+\] on event \[{_QUALIFIER}{message_name}\])"
    )


def _state(machine: str, *, to: str, frm: str = r"[^\]]+") -> str:
    """Match one state machine arriving in a state."""
    return rf"StateMachine\[{machine}\] transitioned from \[{frm}\] to \[{to}\]"


def _on(message_name: str) -> str:
    """The ``on event [...]`` tail of a transition, for pinning which one."""
    return rf" on event \[{_QUALIFIER}{message_name}\]"


# Fallback proof a posted click reached the game glass at all, for targets with
# no named ``confirm`` event. Deliberately not a DEFAULT_RULES entry -- a touch
# isn't visible on screen. The leading \b excludes ``ForceTouchMsg``, which is
# the platform injecting a touch *into* the client and would confirm nothing we did.
TOUCH_REGISTERED = re.compile(r"\b(?:TouchMsg|TouchEventNotificationMsg)\b")


# The game names the paytable it loaded here, and ``paytable`` is byte-identical to
# the folder holding that paytable's maths -- which is what lets
# :mod:`app.services.paytable` join a running game to its ``math.xml``. Shared with
# the ``paytable-changed`` rule below rather than written twice: the same line
# answers "did it just change" and "what is loaded now". ``supported`` is optional,
# so an older log that stops after the id still matches.
PAYTABLE_LOADED = re.compile(
    r"\[WagerGameApp\.UpdatePayTable\] current denom\[(?P<denom>[\d.]+)\]"
    r" current paytableId\[(?P<paytable>[^\]]+)\]"
    r"(?: current supported denoms\[(?P<supported>[^\]]*)\])?"
)


# Ordered (first match wins, narrow before broad) and deliberately a *visual* list
# only -- internal bookkeeping the log names but that produces no frame
# distinguishable from its neighbours is left out. ``capture=False`` keeps a rule in
# the vocabulary without it becoming a screenshot.
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
    EventRule(
        event="spin-with-stops",
        pattern=re.compile(
            r"ButtonPanelState transitioned from \[PanelStateTouchToStart\] "
            r"to \[PanelStateSpinWithStops\]"
        ),
        summary="Spin with stops",
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
        pattern=PAYTABLE_LOADED,
        summary="Paytable is now {paytable}",
        only_on_change=True,
        capture=False,
    ),
    EventRule(
        event="credits-changed",
        pattern=re.compile(
            f"{_message('CreditMeterSetMsg')}|{_message('BalanceNotificationMsg')}"
        ),
        summary="Credit meter set",
        capture=False,
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
            capture=False,
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
