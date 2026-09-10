"""Cyclic Messages runtime settings.

Sibling of :mod:`app.config.event_capture`, and deliberately its own mixin
rather than a reuse of those values: this feature screenshots a strip that
changes every ~8s and records a video of each win presentation alongside, so
its cap and its debounce answer a different question than event capture's do.
"""

from __future__ import annotations

import os

from pydantic import Field
from pydantic_settings import BaseSettings

__all__ = ["CyclicMessagesSettings"]


class CyclicMessagesSettings(BaseSettings):
    """Runtime options for cyclic message capture."""

    # Subdirectory of the OBS screenshot root that holds one folder per run.
    CYCLIC_MESSAGES_DIR_NAME: str = "cyclic-messages"

    # Latency between the strip changing and its screenshot, not a throughput
    # limit. The strip holds each message for ~8s, so this is generous.
    CYCLIC_MESSAGES_POLL_SECONDS: float = 0.25

    # Collapses one logical event logged twice (the publish line and the state
    # transition it caused) into one. Same 0.3s as event capture, and safe here
    # by a wide margin: two genuine messages are ~8s apart.
    CYCLIC_MESSAGES_DEBOUNCE_SECONDS: float = 0.3

    # Screenshots are evidence, not masters.
    CYCLIC_MESSAGES_SCREENSHOT_WIDTH: int = 1280

    # Past the cap, events still record but stop taking screenshots. A loop is
    # ~3 messages per ~34s, so this is many hours of idle attract.
    CYCLIC_MESSAGES_MAX_EVENTS: int = 2000

    # How many recent events the status endpoint reports, for the panel.
    CYCLIC_MESSAGES_RECENT_EVENTS: int = 5

    # Record one clip per win presentation -- from ``cyclic-game-pays`` to the
    # line messages finishing -- and save each beside the screenshots.
    # Deliberately not the length of the run: the strip spends almost all of a
    # session idling, and a video of that is neither watchable nor small.
    # Off makes this feature event-capture with a narrower rule set, which is a
    # reasonable thing to want on a machine that cannot encode.
    CYCLIC_MESSAGES_RECORD_VIDEO: bool = True

    # Safety stop for the clip whose closing line never arrives, and *only* for
    # that -- a presentation normally ends because the log said the pass
    # finished or because the next spin cleared the strip. What this bounds is
    # the run whose game stopped logging, where the cost is a recording of the
    # rest of the session.
    #
    # So size it against the longest presentation a game can *legitimately*
    # make, not against a typical one. A pass shows every paying line for ~2s,
    # so its length scales with how many paid: measured on FortuneOx, one line
    # is 8.3s end to end but a 40-line win is 83s (4.5s of rack-up, then 78.8s
    # of line messages). At 60s this truncated every big win at about line 29 of
    # 40 -- and a truncated clip looks like a complete one to whoever watches
    # it, which is the reason to be generous here rather than tight.
    CYCLIC_MESSAGES_VIDEO_MAX_SECONDS: float = 6000.0

    # --- sampling the line messages ---------------------------------------
    # The individual "LINE 3 PAYS 20" messages have no log line of their own:
    # the client log is *silent* for the entire pass, so the only two facts it
    # offers are where the pass began (``WinBangDone``) and where it ended
    # (``FirstCycleResultsIterationFinishedMsg``). Off, a run still records
    # both boundaries and the "GAME PAYS" frame between them -- it just cannot
    # show which lines paid, because nothing in either log says so.
    CYCLIC_MESSAGES_SAMPLE_LINE_PAYS: bool = True

    # Interval between sampled frames, and a floor rather than a rate: a frame
    # costs an OBS screenshot, measured at 1.5-7s each while a recording is
    # running, so the loop goes as fast as OBS answers and this only stops it
    # going faster. A message dwelling ~2s is not guaranteed a frame because of
    # that -- the clip is what covers every line; these are the still evidence.
    CYCLIC_MESSAGES_SAMPLE_INTERVAL_SECONDS: float = 0.9

    # Safety stop for the pass whose closing line never arrives -- the player
    # spinning again mid-cycle is the ordinary way that happens, and it is the
    # log, not this, that ends a pass that ends properly. Sized like the clip's
    # own deadline and for the same reason: a pass is ~2s per paying line, so
    # 40 lines is 78.8s on FortuneOx and the old 15s stopped screenshotting a
    # fifth of the way in.
    CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS: float = 150.0

    # --- reading the messages back off a finished clip --------------------
    # The strip's text is never logged, and a screenshot costs an OBS round
    # trip of 1.5-7s, so the stills catch only a fraction of a pass. The clip
    # is the one record that has every message in it; cutting it into frames
    # and reading each is the only way to recover what the strip actually said.

    # Which region of the game window the caption sits in. A name, not a
    # rectangle: where a game draws its strip is a fact about that game, so the
    # rectangle lives in `roi.<name>` in the game config beside every other one.
    CYCLIC_MESSAGES_TEXT_REGION: str = "cyclic_message"

    # Gap between sampled frames. Deliberately far shorter than the 1.3-1.8s a
    # message dwells (measured on FortuneOx): sampling at about the dwell time
    # aliases and drops messages *silently*, which is the one failure this
    # feature cannot report. So it oversamples ~3x and consecutive identical
    # readings are collapsed afterwards -- the dedupe decides how many messages
    # there were, never the interval.
    CYCLIC_MESSAGES_TEXT_INTERVAL_SECONDS: float = 0.5

    # Least per-word confidence a reading is believed at. Not really a tuning
    # knob: it detects the frame the *encoder* ruined. A legible caption comes
    # back at 85-97 and one the recording smeared at 45-61 -- "Line 32 Pays 25"
    # arriving as "Liss 32 Pups 29" -- so the two populations separate cleanly
    # in between. A rejected frame keeps its text and its figure rather than
    # being dropped, because a rejection with nothing behind it is not checkable.
    CYCLIC_MESSAGES_TEXT_MIN_CONFIDENCE: float = 80.0

    # Tesseract calls to run at once. A 90s clip is ~180 frames at ~0.5s each,
    # so serially it is a minute and a half of reading against 12s of decoding.
    # Each call is a subprocess and ``subprocess.run`` releases the GIL while it
    # waits, so threads fit -- the same bargain ``utils/meter.py`` makes. 0
    # resolves to half the machine, like CLASSIFIER_TORCH_THREADS and for the
    # same reason: this process also drives OBS, the i-deck and the game clicks.
    CYCLIC_MESSAGES_TEXT_WORKERS: int = Field(default=0, ge=0, le=64)

    @property
    def cyclic_messages_text_workers(self) -> int:
        """Concurrent Tesseract calls, resolving 0 to half the machine."""
        configured = self.CYCLIC_MESSAGES_TEXT_WORKERS
        if configured > 0:
            return configured
        return max(2, (os.cpu_count() or 4) // 2)
