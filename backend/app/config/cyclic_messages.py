"""Cyclic Messages runtime settings.

Sibling of :mod:`app.config.event_capture`, and deliberately its own mixin
rather than a reuse of those values: this feature screenshots a strip that
changes every ~8s and records a video of each win presentation alongside, so
its cap and its debounce answer a different question than event capture's do.
"""

from __future__ import annotations

import os
from typing import Literal

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

    # Gap between sampled frames: one frame a second.
    #
    # What this has to clear is the *shortest* time a message stays up, not the
    # typical one. A sample lands inside every window at least as long as the
    # interval, so an interval below the dwell cannot miss a message and one
    # above it drops messages *silently* -- the one failure this feature could
    # never report. Measured on FortuneOx a message dwells 1.3-1.8s, so 1.0s
    # still catches every one of them, but the margin is 1.3x against the 2.6x
    # half-second sampling had: a game whose strip moves faster than
    # FortuneOx's wants this lowered before its readings are trusted, and the
    # per-request `interval_seconds` is there to try one without an edit.
    # How many messages there were is decided by the dedupe in
    # `cyclic_text._group`, never by this.
    CYCLIC_MESSAGES_TEXT_INTERVAL_SECONDS: float = 1.0

    # Which engine reads a caption. The same bargain the orb reader makes
    # (OCR_ORB_PADDLE_ENABLED above): Tesseract wants a crop flattened to
    # greyscale, autocontrasted and upscaled 3x before it will read it, which
    # on a caption drawn over the game's artwork throws away the colour that
    # separates the two; Paddle's recogniser was trained on colour photographs
    # of text and is given the crop as it is.
    #
    # "tesseract" is kept rather than deleted, for a host without Paddle
    # installed (it needs Python 3.13 or lower) and for comparing the two on
    # one clip -- the reading says which engine produced it, so two runs of the
    # same clip are comparable. Unlike the orb reader there is no *fallback*:
    # an engine that cannot start fails the request, because 90 frames read by
    # nothing come back as a strip that showed nothing.
    CYCLIC_MESSAGES_TEXT_ENGINE: Literal["paddle", "tesseract"] = "paddle"

    # Paddle's recognition model. A caption is read recognise-only -- the crop
    # already *is* the line, so there is nothing to detect -- and this is where
    # the time goes.
    #
    # **Measure a candidate on a real clip, never on a rendered sample.** The
    # game draws its strip in a stylised gold face over artwork, and a caption
    # typed in a system font is a different problem: judged that way the
    # mobile models look fine, and on 96 real frames of a FortuneOx win they
    # are not. Same crops, same frames, one model changed:
    #
    #   PP-OCRv6_medium_rec     1.45 s/frame   14 strings   "Line 1 Pays 250" @97.5
    #   en_PP-OCRv5_mobile_rec  0.05 s/frame   19 strings   "Line 1 Pay 250"  @89.4
    #   PP-OCRv4_mobile_rec     0.10 s/frame   17 strings   "Lne 1 Paye 25"   @74.7
    #
    # The mobile models drop characters ("Pays" as "Pay", "250" as "25") on
    # nearly every frame, which is not only a wrong reading -- it is a wrong
    # reading that *differs each frame*, so one caption fragments into a row
    # per frame in the grouped list. The string counts are the tell: fewer
    # distinct strings over the same twelve messages is a steadier read.
    #
    # 1.45s a frame is affordable only because detection is gone -- the full
    # detect-then-recognise pipeline was 2.26s a frame on top of a 21s build,
    # which is what timed the request out before. **Measured end to end on
    # that 95.4s clip: 188s**, of which ~139s is reading and the rest decoding
    # the video. Inside the client's 240s, but only by 52s.
    #
    # So the ceiling is about two seconds of wall clock per second of clip:
    # past ~120s of footage this stops fitting, and the answer then is a
    # bigger `interval_seconds` on the request (which costs messages only if
    # it goes past the ~1.3s dwell) or one of the mobile models above (which
    # costs accuracy). Neither is the default because neither is needed for a
    # clip of the length a win presentation actually makes.
    CYCLIC_MESSAGES_TEXT_PADDLE_MODEL: str = Field(
        default="PP-OCRv6_medium_rec", min_length=1
    )

    # 2x, and unlike the orb reader's 1x this one is earned. A caption band is
    # *thin* -- FortuneOx draws it about 2% of the window's height, so the crop
    # is 194x37 -- and doubling it before recognition cut the frames that did
    # not fit the caption grammar from 16 of 96 to 7, at no cost worth
    # measuring (152.8s against 139s over the whole clip). 3x was no better
    # than 2x and on one frame worse, so this is a plateau rather than a
    # direction: do not keep raising it.
    CYCLIC_MESSAGES_TEXT_PADDLE_UPSCALE: float = Field(default=2.0, gt=0, le=10)

    # --- repairing a reading against the strip's own vocabulary -----------
    # The strip says one of two things -- "GAME PAYS 500", "LINE 12 PAYS 25" --
    # so its entire alphabet is these words and the digits. Tesseract can be
    # told that with `char_whitelist`; Paddle cannot, so `utils/caption_text`
    # applies it afterwards. That is what turns the "Line a Pays 25" this font
    # reliably produces back into "Line 3 Pays 25".
    #
    # Words the strip is known to draw. **Empty turns the repair off**, which
    # is what a game whose strip nobody has described should get: guessing at
    # an unknown grammar is how a repair becomes a corruption.
    CYCLIC_MESSAGES_TEXT_VOCABULARY: str = "Line,Game,Pays"

    # Of those, the ones a number follows. A letter in that slot is a misread
    # by definition, which is what makes substituting a digit there safe --
    # see the module docstring for why the same is never done digit-to-digit.
    CYCLIC_MESSAGES_TEXT_NUMBER_AFTER: str = "Line,Pays"

    @property
    def cyclic_messages_text_vocabulary(self) -> tuple[str, ...]:
        """Words the caption repair knows, as a tuple."""
        return tuple(
            word.strip()
            for word in self.CYCLIC_MESSAGES_TEXT_VOCABULARY.split(",")
            if word.strip()
        )

    @property
    def cyclic_messages_text_number_after(self) -> tuple[str, ...]:
        """Of those, the ones a number follows."""
        return tuple(
            word.strip()
            for word in self.CYCLIC_MESSAGES_TEXT_NUMBER_AFTER.split(",")
            if word.strip()
        )

    # Least per-word confidence a reading is believed at, on Tesseract's 0-100
    # scale -- Paddle's own 0-1 figure is scaled onto it, so `reliable` means
    # one thing whichever engine answered. Not really a tuning knob: it detects
    # the frame the *encoder* ruined. A rejected frame keeps its text and its
    # figure rather than being dropped, because a rejection with nothing behind
    # it is not checkable.
    #
    # **80.0 is a Tesseract measurement**: a legible caption came back at 85-97
    # and one the recording smeared at 45-61 -- "Line 32 Pays 25" arriving as
    # "Liss 32 Pups 29" -- so the two populations separated cleanly in between.
    # Paddle scores a reading it is happy with at ~0.99 and its distribution
    # here is *not* measured, so this is deliberately left where it was rather
    # than moved blind. Re-measure it the way the first one was: read a clip
    # whose messages you know, and look at `frames[].confidence` for the two
    # groups. Until then a low count of `frames_unreadable` on Paddle means
    # "not measured", not "the recording was clean".
    CYCLIC_MESSAGES_TEXT_MIN_CONFIDENCE: float = 80.0

    # Tesseract calls to run at once, and **Tesseract only** -- a Paddle read
    # is in-process against one shared model, so `cyclic_text` reads those one
    # at a time and this does not apply. Each Tesseract call is a subprocess
    # and ``subprocess.run`` releases the GIL while it waits, so threads
    # genuinely overlap there -- the same bargain ``utils/meter.py`` makes. 0
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
