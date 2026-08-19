"""The game log reader.

Every ``REAL_*`` line in this module is copied verbatim from a real log --
``C:\\logs\\Game\\FortuneOx\\Logs\\FortuneOx_Client.log`` and its HuffNPuffLink
sibling. That matters more than usual here: the rules exist to match one
specific foreign format, so a test written against invented lines would prove
only that the regexes match themselves.
"""

from __future__ import annotations

import re

import pytest

from app.utils.game_log import (
    DEFAULT_RULES,
    EventRule,
    LogRuleError,
    compile_rules,
    match,
    parse_line,
    resolve_rules,
)

# --- verbatim log lines ---------------------------------------------------

REAL_SPIN_PUBLISH = (
    "08/18/26 19:46:20.196 00 FortuneOx:9244 DBG: [MessageQueue.Publish] "
    "msg[GDK.Common.ServerAPI.SpinButtonMsg]"
)
REAL_SPIN_TRANSITION = (
    "08/18/26 14:59:47.010 00 HuffNPuffLink:12844 INF: StateMachine[IdleStateMachine] "
    "transitioned from [stateIdleWithCredits] to [statePlaying] on event "
    "[GDK.Common.ServerAPI.SpinButtonMsg]"
)
# The shape that must NOT match: every state machine that ignored the message
# logs one of these, so matching them turns one spin into five events.
REAL_SPIN_NOT_HANDLED = (
    "08/18/26 19:46:20.197 01 FortuneOx:9244 INF: StateMachine[GameStateMachine] "
    "[Non-queued] [GDK.Common.ServerAPI.SpinButtonMsg] not handled by state [stateEnd]"
)
REAL_REELS_STOPPED = (
    "08/18/26 19:40:29.554 01 FortuneOx:9244 INF: StateMachine[SlotGameStateMachine] "
    "transitioned from [stateSpinWithStops] to [stateReelSpinDone] on event "
    "[GDK.Common.ServerAPI.SpinDoneMsg]"
)
REAL_REEL_STOPS = (
    "08/18/26 19:40:26.932 00 FortuneOx:9244 DBG: [VideoReels] "
    "ReelSet.SetStops(ReelsStopData): [25,134,11,58,163]"
)
REAL_BET_CHANGED = (
    "08/18/26 19:40:16.546 00 FortuneOx:9244 DBG: ServerProxy.ClientToServerSend: "
    "GDK.Common.ServerAPI.BetChangeMsg betData: denom: 100.000 units: 5 unitCost: 5 "
    "betsPerUnit: 100.000 customTotalBet: 0.000 totalBetCost: 1000.000 "
    "totalBetValue: 1000.000"
)
REAL_BET_APPLIED = (
    "08/18/26 19:40:17.266 04 FortuneOx:9244 INF: [BetManager.UpdateCurrentBet]"
    "[CurrentBet {{ BetsPerUnit:100.000, UnitData:[ units: 5, cost: 10 ], "
    "TotalBetCost:1000.000, TotalBetValue:1000.000, DirectPlayData:{{ "
    "DirectPlayType:NotDirectPlay, BonusID:, BonusIndex:0, BonusOption:0 }} }}]"
)
REAL_DENOM_CHANGED = (
    "08/18/26 19:38:07.702 00 FortuneOx:9244 INF: [WagerGameApp.UpdateDenom] "
    "New denom[1.000] Did denom Change[True]"
)
REAL_WIN_SYNCED = (
    "08/18/26 19:40:17.540 00 FortuneOx:9244 DBG: "
    "WagerGameApp.HandleSyncWinAmountMessage() SyncWinAmountMessage contents = "
    "{{ WinAmount(Unitless) :0, hideWin:False, stopCycleResults:True, "
    "syncAfterDoubleUp:False, wasSystemBonus:False }}"
)
REAL_GAMBLE_OFFERED = (
    "08/18/26 19:40:31.280 00 FortuneOx:9244 DBG: [MessageQueue.Publish] "
    "msg[GDK.Common.Gamble.Core.OfferMsg]"
)
REAL_GAMBLE_DECLINED = (
    "08/18/26 19:46:20.258 00 FortuneOx:9244 DBG: [MessageQueue.Publish] "
    "msg[GDK.Common.Gamble.Core.DontPlayMsg]"
)
REAL_GAME_STARTED = (
    "08/18/26 19:38:00.679 00 FortuneOx:9244 INF: "
    "------------------------------ FortuneOx Client Start "
    "------------------------------"
)
# These four are real, parseable lines the game writes constantly, but none of
# them is a rule any more: each landed on a frame indistinguishable from the
# visual event beside it, so keeping them was pure noise on a run. See
# `test_internal_bookkeeping_lines_are_not_captured` below.
REAL_GAME_IDLE = (
    "08/18/26 19:40:31.180 03 FortuneOx:9244 INF: StateMachine[IdleStateMachine] "
    "transitioned from [statePlaying] to [stateIdleWithCredits] on event "
    "[GDK.Common.ServerAPI.HitAnimsDoneMsg]"
)


def detect(raw: str) -> str | None:
    """The event name the default rules give a line, if any."""
    line = parse_line(raw)
    assert line is not None, f"line did not parse: {raw!r}"
    found = match(line, DEFAULT_RULES)
    return None if found is None else found.event


# --- parsing --------------------------------------------------------------


def test_parse_line_splits_the_prefix_from_the_message() -> None:
    line = parse_line(REAL_SPIN_PUBLISH)

    assert line is not None
    assert line.level == "DBG"
    assert line.process == "FortuneOx"
    assert line.message.startswith("[MessageQueue.Publish]")
    assert line.raw == REAL_SPIN_PUBLISH
    assert line.timestamp is not None
    # The game logs US-style month/day, which is the easiest thing to get wrong.
    assert (line.timestamp.month, line.timestamp.day) == (8, 18)
    assert line.timestamp.microsecond == 196_000


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("", id="blank"),
        pytest.param("   ", id="whitespace"),
        pytest.param("    continuation of a multi-line payload", id="continuation"),
        pytest.param("not a log line at all", id="prose"),
    ],
)
def test_parse_line_ignores_anything_that_is_not_a_log_line(raw: str) -> None:
    assert parse_line(raw) is None


def test_parse_line_keeps_a_line_whose_timestamp_is_unusable() -> None:
    """The message is still the interesting part."""
    line = parse_line("99/99/99 19:38:00.679 00 FortuneOx:9244 INF: something happened")

    assert line is not None
    assert line.timestamp is None
    assert line.message == "something happened"


def test_every_line_of_the_shipped_logs_parses() -> None:
    """A format change should fail here, not silently stop capturing events."""
    for raw in (
        REAL_SPIN_PUBLISH,
        REAL_SPIN_TRANSITION,
        REAL_SPIN_NOT_HANDLED,
        REAL_REELS_STOPPED,
        REAL_BET_CHANGED,
        REAL_BET_APPLIED,
        REAL_GAME_STARTED,
    ):
        assert parse_line(raw) is not None, raw


# --- the shipped rules ----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "event"),
    [
        pytest.param(REAL_GAME_STARTED, "game-started", id="game-started"),
        pytest.param(REAL_SPIN_PUBLISH, "spin-started", id="spin-via-publish"),
        pytest.param(REAL_SPIN_TRANSITION, "spin-started", id="spin-via-transition"),
        pytest.param(REAL_REELS_STOPPED, "reels-stopped", id="reels-stopped"),
        pytest.param(REAL_BET_CHANGED, "bet-changed", id="bet-changed"),
        pytest.param(REAL_DENOM_CHANGED, "denomination-changed", id="denom"),
        pytest.param(REAL_GAMBLE_OFFERED, "gamble-offered", id="gamble-offered"),
        pytest.param(REAL_GAMBLE_DECLINED, "gamble-declined", id="gamble-declined"),
    ],
)
def test_default_rules_recognise_real_lines(raw: str, event: str) -> None:
    assert detect(raw) == event


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(REAL_REEL_STOPS, id="reel-stops-data"),
        pytest.param(REAL_BET_APPLIED, id="bet-confirmation"),
        pytest.param(REAL_WIN_SYNCED, id="win-amount-sync"),
        pytest.param(REAL_GAME_IDLE, id="idle-transition"),
    ],
)
def test_internal_bookkeeping_lines_are_not_captured(raw: str) -> None:
    """Only visually distinct events get a screenshot.

    Each of these is real and parses fine, but landed on a frame the person
    watching the screen could not tell apart from the visual event right next
    to it -- the reel-stop data before the animation plays, the bet
    confirmation a beat after the bet that caused it, the win total synced
    before the meter animates, the engine settling back to idle after a round
    that already showed everything worth seeing.
    """
    assert detect(raw) is None


def test_the_not_handled_echo_is_ignored() -> None:
    """The whole reason rules anchor on publishes and transitions.

    Each published message is echoed by one of these per state machine that
    ignored it, so matching them would turn one spin into five screenshots.
    """
    assert detect(REAL_SPIN_NOT_HANDLED) is None


def test_a_rule_lifts_its_named_groups_into_fields() -> None:
    line = parse_line(REAL_BET_CHANGED)
    assert line is not None

    found = match(line, DEFAULT_RULES)

    assert found is not None
    assert found.fields == {"denom": "100.000", "units": "5", "total_bet": "1000.000"}
    assert found.summary == "Bet changed to 1000.000 (5 units at denom 100.000)"


def test_reels_stopped_waits_for_the_animation() -> None:
    """SpinDoneMsg lands when the result does, before the reels settle."""
    line = parse_line(REAL_REELS_STOPPED)
    assert line is not None

    found = match(line, DEFAULT_RULES)

    assert found is not None
    assert found.delay_ms > 0


def test_default_rule_names_are_unique() -> None:
    names = [rule.event for rule in DEFAULT_RULES]
    assert len(names) == len(set(names))


# --- the rest of the visual events ----------------------------------------
#
# Also verbatim. Several come from the HuffNPuffLink theme log, which is where
# the features the FortuneOx session never reached actually happened -- free
# spins, a bonus, a progressive award, a lockup.

REAL_GAMBLE_ACCEPTED = (
    "08/19/26 03:39:18.580 00 FortuneOx:9244 DBG: [MessageQueue.Publish] "
    "msg[double_up_offer_accept]"
)
REAL_GAMBLE_PICKED = (
    "08/19/26 03:39:21.334 00 FortuneOx:9244 DBG: [MessageQueue.Publish] "
    "msg[RED_BLACK_RED_CARD]"
)
REAL_GAMBLE_RESULT = (
    "08/19/26 03:39:21.395 01 FortuneOx:9244 INF: StateMachine[GambleStateMachine] "
    "transitioned from [pickedState] to [displayResultsState] on event "
    "[GDK.Common.Gamble.RedBlack.RedBlackResultsMsg]"
)
REAL_FREE_SPINS_ENTERED = (
    "08/06/26 00:14:27.320 00 HuffNPuffLink:20212 INF: "
    "StateMachine[FreeSpinStateMachineCoinOnReelFS] transitioned from [stateIdle] "
    "to [stateStart] on event [EVENT_TRIGGER_SUBSTATE_MACHINE]"
)
REAL_FREE_SPIN_REELS_STOPPED = (
    "08/06/26 00:14:39.447 02 HuffNPuffLink:20212 INF: "
    "StateMachine[FreeSpinStateMachineCoinOnReelFS] transitioned from "
    "[stateSpinWithStops] to [stateReelSpinDone] on event "
    "[GDK.Common.ServerAPI.FreeSpinDoneMsg_CoinOnReelFS]"
)
REAL_FREE_SPINS_ENDED = (
    "08/06/26 00:15:25.529 00 HuffNPuffLink:20212 INF: "
    "StateMachine[FreeSpinStateMachineCoinOnReelFS] transitioned from "
    "[StateEndDecisionHoldAndSpinEndWin] to [stateTransitionOut] on event "
    "[GDK.Common.ServerAPI.FreeSpinCompleteMsg_CoinOnReelFS]"
)
REAL_BONUS_TRIGGERED = (
    "08/06/26 00:14:27.317 02 HuffNPuffLink:20212 INF: "
    "StateMachine[BonusTriggerStateMachine] transitioned from "
    "[stateBonusTriggerDecision] to [stateBonusTriggered] on event "
    "[GDK.Common.ServerAPI.BonusTriggerMsg]"
)
# The transition the same machine makes on every spin that triggers nothing.
REAL_BONUS_NOT_TRIGGERED = (
    "08/18/26 19:40:31.147 02 FortuneOx:9244 INF: "
    "StateMachine[BonusTriggerStateMachine] transitioned from "
    "[stateBonusTriggerDecision] to [stateEnd] on event "
    "[GDK.Common.ServerAPI.NoBonusTriggerMsg]"
)
REAL_HELP_OPENED = (
    "08/18/26 14:39:56.496 00 HuffNPuffLink:10160 INF: "
    "StateMachine[FeatureSceneLoaderStateMachineHelp] transitioned from "
    "[statePreLoad] to [statePlay] on event [GO_TO_STATE_PLAY]"
)
REAL_FEATURE_SHOWN = (
    "08/06/26 00:14:27.318 03 HuffNPuffLink:20212 INF: "
    "StateMachine[FeatureSceneLoaderStateMachineCoinOnReelFS] transitioned from "
    "[statePreLoad] to [statePlay] on event [GO_TO_STATE_PLAY]"
)
REAL_ATTRACT_STARTED = (
    "08/18/26 19:39:29.249 02 FortuneOx:9244 INF: "
    "StateMachine[AttractStateMachine] transitioned from [stateDisable] to "
    "[stateSequenceStartDelay] on event [GDK.Common.ServerAPI.AttractEnabledMsg]"
)
REAL_ATTRACT_ENDED = (
    "08/18/26 19:39:35.672 00 FortuneOx:9244 INF: "
    "StateMachine[AttractStateMachine] transitioned from [stateSequenceStartDelay] "
    "to [stateDisable] on event [GDK.Common.ServerAPI.AttractDisabledMsg]"
)
# Attract cycling to its next scene, which it does until somebody plays.
REAL_ATTRACT_LOOPED = (
    "08/18/26 19:40:07.545 00 FortuneOx:9244 INF: "
    "StateMachine[AttractStateMachine] transitioned from [stateSequenceEnd] to "
    "[stateSequenceStartDelay] on event "
    "[GDK.Common.ServerAPI.AttractSequenceEndCompleted]"
)
REAL_SERVICE_REQUESTED = (
    "08/18/26 14:41:59.974 00 HuffNPuffLink:15864 INF: "
    "[SyncMessagePublisher.Publish] msg[GDK.Server.PlatformAPI.ServiceRequestedMsg]"
)
REAL_LOCKED_UP = (
    "08/07/26 11:54:15.449 00 HuffNPuffLink:8640 INF: "
    "[SyncMessagePublisher.Publish] msg[GDK.Server.PlatformAPI.LockUpMsg]"
)
REAL_PROGRESSIVE_AWARDED = (
    "08/07/26 11:44:18.495 00 HuffNPuffLink:18860 INF: "
    "[SyncMessagePublisher.Publish] "
    "msg[GDK.Server.PlatformAPI.ProgressiveAwardResponseMsg]"
)
REAL_WIN_COLLECTED = (
    "08/18/26 19:40:31.483 00 FortuneOx:9244 DBG: [MessageQueue.Publish] "
    "msg[WinBangDone]"
)
REAL_CREDITS_CHANGED = (
    "08/18/26 19:38:07.773 00 FortuneOx:9244 DBG: [MessageQueue.Publish] "
    "msg[GDK.Common.ServerAPI.CreditMeterSetMsg]"
)
REAL_PAYTABLE_CHANGED = (
    "08/18/26 19:38:07.701 00 FortuneOx:9244 DBG: [WagerGameApp.UpdatePayTable] "
    "current denom[1.000] current paytableId[FortuneOx-1101YX-1c-90] current "
    "supported denoms[1.000,2.000,5.000,10.000,100.000,200.000]"
)


@pytest.mark.parametrize(
    ("raw", "event"),
    [
        pytest.param(REAL_GAMBLE_ACCEPTED, "gamble-accepted", id="gamble-accepted"),
        pytest.param(REAL_GAMBLE_PICKED, "gamble-picked", id="gamble-picked"),
        pytest.param(REAL_GAMBLE_RESULT, "gamble-result", id="gamble-result"),
        pytest.param(REAL_FREE_SPINS_ENTERED, "free-spins-entered", id="fs-entered"),
        pytest.param(
            REAL_FREE_SPIN_REELS_STOPPED, "free-spin-reels-stopped", id="fs-reels"
        ),
        pytest.param(REAL_FREE_SPINS_ENDED, "free-spins-ended", id="fs-ended"),
        pytest.param(REAL_BONUS_TRIGGERED, "bonus-triggered", id="bonus"),
        pytest.param(REAL_HELP_OPENED, "help-opened", id="help"),
        pytest.param(REAL_FEATURE_SHOWN, "feature-scene-shown", id="feature"),
        pytest.param(REAL_ATTRACT_STARTED, "attract-started", id="attract-started"),
        pytest.param(REAL_ATTRACT_ENDED, "attract-ended", id="attract-ended"),
        pytest.param(REAL_ATTRACT_LOOPED, "attract-looped", id="attract-looped"),
        pytest.param(REAL_SERVICE_REQUESTED, "service-requested", id="service"),
        pytest.param(REAL_LOCKED_UP, "locked-up", id="lockup"),
        pytest.param(REAL_PROGRESSIVE_AWARDED, "progressive-awarded", id="progressive"),
        pytest.param(REAL_WIN_COLLECTED, "win-collected", id="win-collected"),
        pytest.param(REAL_CREDITS_CHANGED, "credits-changed", id="credits"),
        pytest.param(REAL_PAYTABLE_CHANGED, "paytable-changed", id="paytable"),
    ],
)
def test_default_rules_recognise_the_rest_of_the_visual_events(
    raw: str, event: str
) -> None:
    assert detect(raw) == event


def test_the_bonus_decision_every_spin_makes_is_not_a_trigger() -> None:
    """The machine runs on every spin; one of its transitions means a bonus."""
    assert detect(REAL_BONUS_NOT_TRIGGERED) is None


def test_the_free_spin_machine_is_matched_whatever_the_game_calls_it() -> None:
    """FortuneOx names it after the feature, HuffNPuffLink after the mechanic.

    ``FreeSpinStateMachineFreeSpin`` against ``FreeSpinStateMachineCoinOnReelFS``
    -- a rule pinned to either name works on exactly one game.
    """
    fortune_ox = REAL_FREE_SPINS_ENTERED.replace(
        "FreeSpinStateMachineCoinOnReelFS", "FreeSpinStateMachineFreeSpin"
    )

    assert detect(fortune_ox) == "free-spins-entered"


def test_a_feature_scene_says_which_feature() -> None:
    line = parse_line(REAL_FEATURE_SHOWN)
    assert line is not None

    found = match(line, DEFAULT_RULES)

    assert found is not None
    assert found.fields == {"feature": "CoinOnReelFS"}
    assert found.summary == "CoinOnReelFS scene shown"


def test_help_is_not_reported_as_a_generic_feature_scene() -> None:
    """Help loads through the same loader a wheel or a bonus does.

    Which is why its own rules sit above the generic one: somebody looking for
    the paytable should not have to know it is 'the Help feature scene'.
    """
    assert detect(REAL_HELP_OPENED) == "help-opened"


# --- which events are worth a screenshot ----------------------------------


def test_the_events_worth_a_screenshot_include_the_ones_a_run_is_opened_for() -> None:
    """The list is a decision rather than an accident, so it is pinned."""
    captured = {rule.event for rule in DEFAULT_RULES if rule.capture}

    assert {
        "spin-started",
        "reels-stopped",
        "bet-changed",
        "denomination-changed",
        "win-collected",
        "gamble-offered",
        "gamble-accepted",
        "gamble-result",
        "free-spins-entered",
        "bonus-triggered",
    } <= captured


def test_default_rules_all_capture_unless_manually_turned_off() -> None:
    """Every default rule captures until someone flips its flag by hand."""
    assert all(rule.capture for rule in DEFAULT_RULES)


def test_a_detected_event_carries_the_rules_decisions() -> None:
    """The capture service acts on these, so they have to survive the match."""
    line = parse_line(REAL_CREDITS_CHANGED)
    assert line is not None

    found = match(line, DEFAULT_RULES)

    assert found is not None
    assert found.capture is True
    assert found.only_on_change is False


# --- declared rules -------------------------------------------------------


def test_compile_rules_accepts_a_declared_rule() -> None:
    rules = compile_rules(
        [{"event": "jackpot-hit", "pattern": "MoneyLinkOutroSM", "delay_ms": 1200}],
        where="'events.rules'",
    )

    assert len(rules) == 1
    assert rules[0].event == "jackpot-hit"
    assert rules[0].delay_ms == 1200
    # No summary declared, so one is derived from the name.
    assert rules[0].summary == "Jackpot hit"


def test_compile_rules_accepts_nothing() -> None:
    assert compile_rules(None, where="'events.rules'") == ()


@pytest.mark.parametrize(
    "declared",
    [
        pytest.param({"pattern": "x"}, id="no-event-name"),
        pytest.param({"event": "Not Kebab", "pattern": "x"}, id="bad-event-name"),
        pytest.param({"event": "ok", "pattern": "unclosed ("}, id="bad-regex"),
        pytest.param({"event": "ok"}, id="no-pattern"),
        pytest.param({"event": "ok", "pattern": ""}, id="empty-pattern"),
        pytest.param(
            {"event": "ok", "pattern": "x", "delay_ms": -1}, id="negative-delay"
        ),
        pytest.param(
            {"event": "ok", "pattern": "x", "delay_ms": 60_000}, id="huge-delay"
        ),
        pytest.param({"event": "ok", "pattern": "x", "summary": 7}, id="bad-summary"),
    ],
)
def test_compile_rules_rejects_a_malformed_rule(declared: object) -> None:
    """A typo should be reported when the config is read, not mid-run."""
    with pytest.raises(LogRuleError):
        compile_rules([declared], where="'events.rules'")


@pytest.mark.parametrize(
    "declared",
    [
        pytest.param({"rules": []}, id="object-not-array"),
        pytest.param(["not an object"], id="entry-not-an-object"),
    ],
)
def test_compile_rules_rejects_a_malformed_block(declared: object) -> None:
    with pytest.raises(LogRuleError):
        compile_rules(declared, where="'events.rules'")


def test_a_bad_summary_placeholder_falls_back_to_the_template() -> None:
    """A rule with a typo in its summary should still record the event."""
    rule = EventRule(
        event="custom", pattern=re.compile("Client Start"), summary="saw {nonexistent}"
    )
    line = parse_line(REAL_GAME_STARTED)
    assert line is not None

    found = match(line, [rule])

    assert found is not None
    assert found.summary == "saw {nonexistent}"


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        pytest.param("capture", False, id="capture"),
        pytest.param("only_on_change", True, id="only-on-change"),
    ],
)
def test_a_declared_rule_can_set_the_flags(flag: str, value: bool) -> None:
    rules = compile_rules(
        [{"event": "custom", "pattern": "x", flag: value}], where="'events.rules'"
    )

    assert getattr(rules[0], flag) is value


def test_a_declared_rule_is_captured_unless_it_says_otherwise() -> None:
    """A game that bothers to declare a rule wants to see what it matched."""
    rules = compile_rules([{"event": "custom", "pattern": "x"}], where="'events.rules'")

    assert rules[0].capture is True
    assert rules[0].only_on_change is False


@pytest.mark.parametrize(
    "declared",
    [
        pytest.param({"event": "ok", "pattern": "x", "capture": "yes"}, id="capture"),
        pytest.param(
            {"event": "ok", "pattern": "x", "only_on_change": 1}, id="only-on-change"
        ),
    ],
)
def test_compile_rules_rejects_a_flag_that_is_not_a_boolean(declared: object) -> None:
    with pytest.raises(LogRuleError):
        compile_rules([declared], where="'events.rules'")


# --- combining ------------------------------------------------------------


def test_resolve_rules_returns_the_defaults_when_a_game_adds_nothing() -> None:
    assert resolve_rules() == DEFAULT_RULES


def test_a_game_can_disable_a_shipped_rule() -> None:
    resolved = resolve_rules(disabled=["win-collected"])

    names = [rule.event for rule in resolved]
    assert "win-collected" not in names
    assert "spin-started" in names


def test_a_game_rule_wins_over_the_shipped_one_of_the_same_name() -> None:
    """Overriding, not just adding: the game's version must be the one that runs."""
    mine = EventRule(
        event="spin-started", pattern=re.compile("nothing matches this"), summary="mine"
    )

    resolved = resolve_rules(extra=[mine])

    assert [rule for rule in resolved if rule.event == "spin-started"] == [mine]
    # And so the shipped pattern no longer claims the line.
    line = parse_line(REAL_SPIN_PUBLISH)
    assert line is not None
    assert match(line, resolved) is None
