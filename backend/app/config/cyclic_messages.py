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


def _named(value: str) -> tuple[str, ...]:
    """A comma-separated setting as a tuple of names, blanks dropped."""
    return tuple(name.strip() for name in value.split(",") if name.strip())


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

    # JPEG, not PNG, and this is a *coverage* setting rather than a disk-space
    # one. A frame costs an OBS round trip, and the capture loop has to come
    # round again inside the time one message stays on the strip (1.3-1.8s on
    # FortuneOx) or it skips messages with nothing saying so. PNG is lossless
    # and encodes a 1280-wide frame far more slowly than JPEG does, which is
    # round-trip time spent on fidelity nothing here needs: the clip reader
    # gets the same captions off H.264 video frames, which are compressed far
    # harder than JPEG at this quality, so this cannot be what makes a caption
    # unreadable.
    #
    # "png" is still accepted for a host where the extra fidelity matters more
    # than the interval -- but check `CyclicStatus.sample_rate` afterwards,
    # because that is the trade being made.
    CYCLIC_MESSAGES_SCREENSHOT_FORMAT: str = Field(default="jpeg", min_length=1)

    # Quality for the format above, where it has one; -1 takes the format's own
    # default. 90 is high enough that the caption band is untouched by ringing
    # and low enough to encode quickly.
    CYCLIC_MESSAGES_SCREENSHOT_QUALITY: int = Field(default=90, ge=-1, le=100)

    # Past the cap, events still record but stop taking screenshots. A loop is
    # ~3 messages per ~34s, so this is many hours of idle attract -- but a win
    # pass is sampled at CYCLIC_MESSAGES_SAMPLE_INTERVAL_SECONDS too, and at 2s
    # a frame a 150s pass (the sampler's own deadline) is 75 frames, so this
    # still has plenty of headroom over the fast case as well as the idle one.
    CYCLIC_MESSAGES_MAX_EVENTS: int = 20000

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

    # Interval between sampled frames, and **a floor rather than a rate**: a
    # frame costs an OBS screenshot, so the loop goes as fast as OBS answers
    # and this only stops it going faster. `CyclicStatus.sample_rate` reports
    # what was actually achieved, which is the number to read rather than this
    # setting -- and `CyclicLiveView.sample_interval_seconds` carries this one
    # beside it so the two can be compared without guessing.
    #
    # **1.0s, the same value as CYCLIC_MESSAGES_TEXT_INTERVAL_SECONDS, and for
    # the same reason.** What an interval has to clear is the *shortest* time a
    # message stays on the strip, not the typical one: a sample lands inside
    # every window at least as long as the interval, so an interval below the
    # dwell cannot miss a message and one above it drops messages *silently*.
    # A message dwells 1.3-1.8s on FortuneOx, so 1.0s catches every one of
    # them and the clip reader -- which reads the same strip off the recorded
    # video at exactly this interval -- is the proof of it.
    #
    # It was briefly 2.0s, which is above the dwell and therefore skipped line
    # messages outright: a pass read back off its clip listed captions the
    # stills beside it had never caught. Do not raise it past ~1.3s without
    # accepting that.
    #
    # Whether the loop *achieves* 1.0s is a separate question and an OBS one.
    # Two things were done so it can: the wasted second round trip per frame
    # is gone (`services/obs.take_screenshot`'s `include_image_data`), and
    # frames are JPEG rather than PNG (CYCLIC_MESSAGES_SCREENSHOT_FORMAT). If
    # `sample_rate` still comes back well under 1/s, the frames genuinely are
    # that far apart and the run's own coverage warning says so.
    CYCLIC_MESSAGES_SAMPLE_INTERVAL_SECONDS: float = 1.0

    # Safety stop for the pass whose closing line never arrives -- the player
    # spinning again mid-cycle is the ordinary way that happens, and it is the
    # log, not this, that ends a pass that ends properly. Sized like the clip's
    # own deadline and for the same reason: a pass is ~2s per paying line, so
    # 40 lines is 78.8s on FortuneOx and the old 15s stopped screenshotting a
    # fifth of the way in.
    CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS: float = 150.0

    # --- the between-spins strip ------------------------------------------
    # The other window: what the strip shows once a spin is over, whether it
    # lost or its win has been taken. "GAME OVER", "GAME PAYS n", "PLAY 880
    # CREDITS", round and round until somebody spins again, and none of it
    # logged.

    # How long to keep capturing it. Sized to see the whole cycle rather than
    # a slice of it: three messages at the interval below is a lap, and this
    # is comfortably several, so the run catches each of them even if it joins
    # part-way through one.
    #
    # Bounded at all because this window ends when the player spins again,
    # which they may simply never do -- measured on FortuneOx, one gap between
    # a taken win and the next spin ran to eight and a half minutes. Without a
    # stop, a machine nobody is playing is screenshotted until it is.
    CYCLIC_MESSAGES_IDLE_MAX_SECONDS: float = 90.0

    # Gap between frames of *this* strip, and deliberately not the win pass's.
    # An interval has to clear the shortest time a message stays up, and the
    # two strips are nowhere near each other: a line message dwells 1.3-1.8s
    # while the between-spins strip sits on each of its three for the best
    # part of ten seconds. Sampling that every second would take ten
    # near-identical frames of each, every one of them costing an OBS round
    # trip and an OCR pass to say what the frame before it said.
    #
    # **Not measured as precisely as the win strip's dwell was** -- it is
    # inferred from the attract loop's ~3 messages per ~34s. It is set well
    # under that rather than near it, so the margin absorbs being wrong;
    # `CyclicStatus.sample_rate` and the duplicate counts on the live card are
    # what to check against a real run.
    CYCLIC_MESSAGES_IDLE_INTERVAL_SECONDS: float = 2.0

    # Stop this window as soon as the strip has been round once, rather than
    # running it to the deadline above.
    #
    # The strip repeats until somebody spins, so everything after the first lap
    # is a caption already captured -- more frames, more OCR, and a run listing
    # the same three messages several times over. The lap is spotted from the
    # *picture* rather than from the text (see `cyclic_text.LoopWatch`), which
    # is what lets it be decided inside the capture loop without an OCR pass
    # there: milliseconds against 1.5s a frame.
    #
    # Off falls back to the deadline, which is what a game whose strip does not
    # repeat cleanly should get -- a lap that is never detected costs the
    # window nothing but its full length.
    CYCLIC_MESSAGES_IDLE_STOP_AFTER_LOOP: bool = True

    # Record this window too, not just the win presentation.
    #
    # It was deliberately off while the window ran to a deadline: a clip per
    # spin, most of them 90s of a machine sitting idle, is most of a session on
    # disk and is what recording per win exists to avoid. Stopping after one
    # lap is what changed the arithmetic -- measured on a real losing spin, the
    # whole strip is four frames and about eight seconds -- so a clip of it is
    # short, one per spin, and covers the messages between the stills.
    #
    # Worth knowing before leaving it on: OBS is *slower to answer a screenshot
    # while it is recording*, so this costs the window some of its frame rate.
    # `CyclicStatus.sample_rate` is where that shows up; turn this off if the
    # stills matter more than the clip on a given machine.
    CYCLIC_MESSAGES_RECORD_IDLE_VIDEO: bool = True

    # After a win presentation, fill its messages in from its own clip.
    #
    # **Because the stills cannot keep up with that window and the clip can.**
    # Measured on this machine: a screenshot costs 0.11s with OBS idle, 0.3-0.7s
    # while recording a still screen, and about *six seconds* while recording
    # the win presentation -- the game is animating hard and the encoder has the
    # canvas. A line message stays up around two seconds, so the live stills
    # catch perhaps one in three of them however short the interval is set.
    #
    # The clip has every one of them, because a recording does not skip frames.
    # So once the pass is over and nothing is competing for OBS, its own clip is
    # decoded at CYCLIC_MESSAGES_TEXT_INTERVAL_SECONDS, each frame written out
    # as a screenshot and read like any other. The pass ends up complete; the
    # difference is that the messages the stills missed appear a few seconds
    # after it rather than during it.
    #
    # Off leaves the live stills as the only record of a win, which is the
    # sparse one -- "Read the messages" on the clip is then the complete list.
    CYCLIC_MESSAGES_RECOVER_FROM_CLIP: bool = True

    # --- reading a pass's frames, once it has closed -----------------------
    # Every frame of a pass is screenshotted first and read later: nothing
    # crops or OCRs a frame while the capture loop is still taking them, so
    # reading never competes with capture for OBS or the CPU. The moment a
    # pass closes, its frames are read in one batch, in order, on the run's own
    # task -- see `services/cyclic_messages._read_pending`. This was a
    # concurrent worker draining a queue while capture ran; it was removed
    # because Paddle recognition is not fully outside the GIL, and running it
    # in a background thread while the capture loop's own coroutine needed to
    # run turned a 2s interval into a great deal more than that.
    #
    # Off, a pass still screenshots exactly as before and the frames simply
    # carry no `reading` -- which is what a host without PaddleOCR gets anyway,
    # since a reader that cannot start is reported and not raised.
    CYCLIC_MESSAGES_LIVE_READ: bool = True

    # Paddle's recognition model for reading a closed pass, kept apart from
    # CYCLIC_MESSAGES_TEXT_PADDLE_MODEL because the two answer to different
    # clocks. The clip reader has the whole request to finish in; this one
    # runs after capture has already stopped, so its only cost is how long a
    # tester waits for the pass just captured to show its readings. Same
    # default as the clip reader, so a caption reads the same whichever path
    # read it -- see that setting for why the mobile models are not the
    # default despite being 20x quicker.
    #
    # `CyclicStatus.queue_depth` counting down slowly after a pass closes
    # means this model is what is taking the time; one of the mobile models
    # trades some accuracy for a much shorter wait. There is no backlog to
    # answer here the way a concurrent reader would have had one -- reading
    # starts with an empty queue every time, because nothing was captured
    # while the previous read was still running.
    CYCLIC_MESSAGES_LIVE_PADDLE_MODEL: str = Field(
        default="PP-OCRv6_medium_rec", min_length=1
    )

    # How many of the current pass's frames the live view returns. A cap on the
    # *payload*, not on capture: the frames are all on disk and all in the
    # manifest, and this only bounds what a poll every half-second has to
    # carry. At the 2.0s floor a 150s pass tops out at 75 frames, well under
    # this, but a host where OBS answers faster than that floor -- or a lower
    # CYCLIC_MESSAGES_SAMPLE_INTERVAL_SECONDS -- can still run past it.
    CYCLIC_MESSAGES_LIVE_FRAMES: int = Field(default=400, ge=1, le=5000)

    # How often the run's `run.json` is rewritten while a run is going. The
    # manifest is the whole run every time it is written, so writing it per
    # event is quadratic -- fine at one event every 2s while a pass captures,
    # not fine at one per frame *and* one per reading if that ever ran
    # concurrently with capture, which is exactly what it no longer does. A
    # crash loses at most this much; a stop, and the moment a pass finishes
    # being read, always write unconditionally.
    CYCLIC_MESSAGES_MANIFEST_INTERVAL_SECONDS: float = Field(
        default=1.0, ge=0.0, le=60.0
    )

    # --- reading the messages back off a finished clip --------------------
    # The strip's text is never logged, and a screenshot costs an OBS round
    # trip of 1.5-7s, so the stills catch only a fraction of a pass. The clip
    # is the one record that has every message in it; cutting it into frames
    # and reading each is the only way to recover what the strip actually said.

    # Which regions of the game window the strip's lines sit in, in the order
    # they are drawn, top first. Names rather than rectangles: where a game
    # draws its strip is a fact about that game, so each rectangle lives in
    # `roi.<name>` in the game config beside every other one.
    #
    # **One line during a win presentation.** While the game is paying -- from
    # "GAME PAYS 168" through "LINE 25 PAYS 15" and the rest -- the strip draws
    # a single line and the band beneath it is empty. Measured on a real frame
    # from inside that window, the second band read as "0" at 29 confidence:
    # artwork noise, correctly rejected. So nothing is gained by cropping it
    # there, and this list stays one entry long.
    #
    # The *between-spins* strip is the one with two lines; it has its own
    # setting below, because it is a different window showing different text.
    CYCLIC_MESSAGES_TEXT_REGIONS: str = "cyclic_message"

    @property
    def cyclic_messages_text_regions(self) -> tuple[str, ...]:
        """The win presentation's lines, top first, as a tuple."""
        return _named(self.CYCLIC_MESSAGES_TEXT_REGIONS)

    # **Two lines between spins, and this is the other scenario entirely.**
    # Once a spin is over -- a loss straight away, a win the moment it is
    # taken -- the game sits idle and cycles "GAME OVER", "GAME PAYS n" and
    # "PLAY 880 CREDITS". FortuneOx stacks those across two bands: the top one
    # keeps whatever the strip last said, and a second, smaller line runs
    # beneath it.
    #
    # They have to be cropped apart because a caption is read recognise-only --
    # the crop is asserted to *be* one line -- and handing the recogniser both
    # at once does not read two lines, it reads one wrong one. Measured on a
    # real frame: the stacked crop came back as "ine 2 Pa a s 15" at 0.54
    # confidence with the second line lost entirely, against "Line 25 Pays 15"
    # at 1.00 and "Play 880 Credits" at 0.99 once split.
    #
    # A game whose between-spins strip is one line names one region here.
    CYCLIC_MESSAGES_IDLE_REGIONS: str = "cyclic_message,cyclic_message_2"

    @property
    def cyclic_messages_idle_regions(self) -> tuple[str, ...]:
        """The between-spins strip's lines, top first, as a tuple."""
        return _named(self.CYCLIC_MESSAGES_IDLE_REGIONS)

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
    # "Play", "Credits" and "Over" are here because the strip's *second* line
    # says them, and leaving them out is actively harmful rather than merely
    # incomplete: the repair pulls an unknown word to the nearest known one, so
    # without "Play" in the list a correctly-read "Play 880 Credits" was being
    # rewritten to "Pays 880 Credits" -- one character, and confidently wrong.
    CYCLIC_MESSAGES_TEXT_VOCABULARY: str = "Line,Game,Pays,Play,Credits,Over"

    # Of those, the ones a number follows. A letter in that slot is a misread
    # by definition, which is what makes substituting a digit there safe --
    # see the module docstring for why the same is never done digit-to-digit.
    # "Play" joins these for the second line's "PLAY 880 CREDITS".
    CYCLIC_MESSAGES_TEXT_NUMBER_AFTER: str = "Line,Pays,Play"

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

    # Fewest letters-and-digits a reading must have to be a caption at all.
    #
    # **A character count, not a confidence floor, and the floor cannot do this
    # job.** The strip does not use every one of its lines at every moment --
    # between spins FortuneOx leaves the top band empty for a whole pass -- and
    # a recogniser handed an empty crop does not say "nothing", it says a
    # character it thinks it saw in the artwork. Measured over every reading in
    # every run captured so far:
    #
    #   1-2 characters (noise off an unused band)   104 readings, 29-86 conf
    #   3 characters                                  none
    #   4-14 characters (real captions)             544 readings, 94-100 conf
    #
    # The lengths separate with a whole character of daylight, and the
    # confidences do not: a stray "50" scored 86, *above* the 80 floor, so it
    # would have been shown as a caption the reader was sure of. The same
    # bargain, for the same reason, as `ocr.TILE_MIN_DIGITS` on a prize orb.
    #
    # A refused reading keeps its raw `text` and confidence on the payload and
    # loses only its `repaired` -- so it is visible in the record as something
    # read and rejected, and nothing downstream counts it as a message.
    CYCLIC_MESSAGES_TEXT_MIN_CHARACTERS: int = Field(default=3, ge=0, le=40)

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
