"""Cyclic Messages: follow the game's log for the rotating message strip,
screenshot every message it shows, and record a video of the whole run.

Sibling of :mod:`app.services.event_capture`, and structured the same way --
module-level singleton, a watcher task over a :class:`LogFollower`, one
directory per run holding a ``run.json`` beside its images. Four things differ,
all of them because a cyclic message is not a gameplay event:

* **The rule set is only** :data:`game_log.CYCLIC_MESSAGE_RULES`. Event capture
  resolves the shipped rules plus a game's own; this feature deliberately does
  not, because "only cyclic messages" is the whole point of it.
* **The sequence boundaries are recorded without a frame.** ``capture=False``
  means "no screenshot" here, not "ignore" as it does in event capture -- a run
  reads as sequences, and a marker with no picture is what makes it do so.
* **A run records video per win, not per run.** OBS is asked to record when
  ``cyclic-game-pays`` is read and to stop when the line messages report
  finishing, and each file is moved beside the screenshots so a run is still
  one directory. See below; the boundaries are the point.
* **Part of a run is sampled, not logged.** See below; this is the one thing
  about the feature that cannot be inferred from its siblings.

The strip's *text* is never logged by the game -- not by ``AttractStateMachine``,
not by the results cycle, not by ``LocalizationManager`` -- so these events say
only that the strip changed. What it changed to is in the screenshot, which is
why a run that takes no screenshots is worth failing loudly about and a run that
takes no video is not.

**The win strip is bracketed, not followed, and that is the load-bearing
compromise.** The idle strip logs a line per message, so it gets an event per
message. The win strip does not: ``OnGameStateResults`` says what the game pays
and ``FirstCycleResultsIterationFinishedMsg`` says one full pass through the
line messages is done, and between those two the client log says almost nothing
-- ``WinBangDone`` marks where the count-up ended and then there is silence,
measured at 6.5-7.3s across four one-line FortuneOx wins and at 78.8s across
three 40-line ones, not one line in between either way. **How long that window
is depends on how many lines paid**, at roughly 2s a line, which is what every
deadline here has to be sized against rather than against a typical win. So
the frames inside that window are taken on a timer and marked
:attr:`CyclicEventSource.SAMPLED`. Two rules protect the distinction:

* A sampled event's ``log_line`` quotes the boundary that *opened* the window,
  never a line about the message in its own picture -- there is no such line.
* Only ``cyclic-game-pays`` carries an amount, because
  ``SpinBufferManager.OnGameStateResults`` is the single line in either log that
  states one. Its cents over the denomination is what the strip displays: 16800
  at ``denom[100.000]`` is the "GAME PAYS 168" a player sees.

Do not "improve" a sampled event into a logged one by inventing a rule for it.
A frame taken on a clock that claims a log line behind it is worse than one
that admits it was a guess, because the second can be checked and the first
cannot.

**The window opens on the amount, not on the rack-up.** Capture starts at
``cyclic-game-pays`` and stops at ``cyclic-line-pays-cycle-finished``, which is
the same bracket the clip is recorded across -- so the frames and the video
cover the same stretch of the presentation and can be read against each other.
It used to open one line later, at ``cyclic-win-presented``, which skipped the
banner and the whole count-up; those seconds are as much a part of what the
strip displayed as the line messages after them.

**The strip between spins is a second silent window, and it is not the same
strip.** Once a spin is over the game sits idle and cycles "GAME OVER", "GAME
PAYS n" and "PLAY 880 CREDITS" until somebody spins again. **Both ways a spin
can end arrive there**, which is why the window is bracketed on the game going
idle (``cyclic-idle-strip-started``, the ``IdleStateMachine`` entering
``stateIdleWithCredits``) rather than on either result line: measured across
FortuneOx's own log, a losing spin reaches it within a few hundred milliseconds
of its result and a winning one only once its win has been *taken* -- 78s after
the line messages finished, on one of them. It closes on
``cyclic-idle-strip-ended``, the same machine leaving that state, which is the
first line of the next spin.

So this window is strictly *after* the win presentation, never inside it --
but it is not always *separate* from it, and that is the third case. Take the
win before its line messages have been round once and the game goes idle while
the presentation is still being captured: on screen there is no break at all,
the strip simply carries on from "LINE 1 PAYS 250" into "GAME OVER". So the
window carries on with it (:func:`_carry_on`) -- the same sampler, the same
clip, the same sequence, with the bands widened underneath. It used to close
the win window here and open a fresh one, which lost the end of the line
messages, put a gap in the recording and emptied the live card of the win at
the exact moment the tester pressed the button.

**A carried-on window runs two strips at once, and it ends on the slower
one.** This is the trap, because the two are wildly different lengths: the top
band is still cycling line messages -- 2s a line, so minutes on a 40-line win
-- while the small band beneath it laps its three between-spins captions every
six seconds. So the carry-on keeps the *win's* deadline and goes on expecting
``cyclic-line-pays-cycle-finished``, and the lap watch that ordinarily ends a
between-spins window is not armed until that line lands
(:func:`_watch_for_the_lap`). Arming it at the carry-on instead ended the
window on the small band's first lap: on the run this was found on, six
frames, then 69 uncaptured seconds of line messages, and the closing line
arriving to a window already read and filed.

The two ways of taking a win therefore differ in how many *windows* a spin
produces and not in what ends up on the card: taken early it is one window,
taken after the line messages finish it is two, and either way the live view
shows the whole spin. See :attr:`_ActiveRun.live_cycles`.

The difference between the strips matters because the two draw different
things and are read differently:

* A **win presentation** draws **one** line -- "GAME PAYS 168", then "LINE 25
  PAYS 15" and the rest. The band beneath it is empty, measured at 29
  confidence of artwork noise from inside that window, so
  ``CYCLIC_MESSAGES_TEXT_REGIONS`` names one region and nothing crops the
  second band there.
* The **between-spins strip** draws **two**, stacked: the top band keeps
  whatever the strip last said and a smaller line runs beneath it. Those are
  ``CYCLIC_MESSAGES_IDLE_REGIONS``, and they are cropped apart because a
  caption is read recognise-only -- handing the recogniser both at once reads
  one wrong line rather than two right ones.

Which lines a frame is read for is stamped on it when it is captured
(:attr:`_Pending.regions`), not looked up when the batch is read: by then the
run has moved on, and the window a frame came from is the only thing that knows
what was on screen.

Two things differ from the win window besides, and one thing that used to no
longer does. It has its **own deadline**
(``CYCLIC_MESSAGES_IDLE_MAX_SECONDS``), because the two overrun differently: a
win pass that outruns its closing line is still a win pass, while this window
ends only when somebody spins again -- measured at eight and a half minutes
once -- so the deadline is how it ordinarily ends rather than a fault, and is
sized to catch the whole cycle several times over rather than to cut it short.
And it ends early when the strip has been **round once**
(``CYCLIC_MESSAGES_IDLE_STOP_AFTER_LOOP``), spotted from the picture rather
than the text, because past the first lap every caption is one already caught.

What no longer differs is the **interval**, and the correction is worth having
on the record because the wrong value here fails *silently*. The two strips
were taken to be nowhere near each other -- a line message dwells 1.3-1.8s,
while this one was believed to sit on each of its three captions for the best
part of ten seconds -- so it was sampled at 2.0s. Read back off a real losing
spin's own clip, the strip runs a caption for about a second with a blank
second after it and laps all three in six, so 2.0s sat above the dwell and
dropped captions with nothing on the record saying so. Both strips are now
sampled at 1.0s; see ``CYCLIC_MESSAGES_IDLE_INTERVAL_SECONDS``.

It **does** record a clip, which that same measurement is why. Recording the
gap after every spin was the thing per-win recording existed to avoid, and it
still would be if these clips ran to the deadline -- but a window that stops
after one lap is about eight seconds, one file per spin, and it is the only
complete record of a strip the stills cannot keep up with.

**Capturing every frame on schedule is the one thing this feature cannot get
back if it slips, so nothing else is allowed to compete with it.** Every frame
inside the window is only ever screenshotted -- :func:`_record` writes the file
and appends the event, full stop. Nothing crops it, nothing runs OCR over it,
and no background task is started to do either while the window is open.
Reading is :func:`_read_pending`, and it runs exactly once per pass, called
plainly (awaited, not spawned) the moment the pass closes -- after the last
frame is written and after the clip is filed, so a slow read cannot be mistaken
for a slow capture. Three things follow from doing it this way rather than
concurrently, which is what an earlier version of this feature did:

* **The interval is the only cost of a frame.** OBS still costs what it
  costs -- 1.5-7s per screenshot while a recording is running, longer with the
  wasted ``GetSourceScreenshot`` half of that removed (see
  ``services/obs.take_screenshot``'s ``include_image_data``) -- but that is now
  the *only* thing standing between one frame and the next.
  ``CyclicStatus.sample_rate`` reports what the loop actually achieved.
  A concurrent reader competed for the same CPU the whole time a pass ran: a
  Paddle recognition pass is not fully outside the GIL, so running one in a
  background thread while the capture loop's own coroutine needed to run could
  turn a 2s interval into several times that -- which is the delay this
  redesign exists to remove.
* **Every frame of a pass is read in one place, in order, once.** No queue,
  no worker task, no draining a backlog on a timeout: :func:`_read_pending`
  takes the pass's frames in the order they were captured and reads them one
  at a time, updating :attr:`CyclicEvent.reading` as it goes so a caller
  polling ``/live`` mid-read still sees them arrive progressively. It runs on
  the watcher's own task, so the next log line is not read until it finishes --
  a deliberate trade against capturing the *next* pass sooner, made because
  reading is never the priority here and capturing always is.
* **Losing the reader costs the readings and nothing else.** A host without
  PaddleOCR notes it on the run and keeps every screenshot, which is exactly
  what this feature did before it could read at all. The clip reader in
  :mod:`app.services.cyclic_text` is still the complete answer after the fact;
  the post-pass reading is the same crop, the same repair and the same
  confidence floor, applied to the stills instead of to the video.

**The stills are the sparse record and the clip is the complete one, so every
clip is read back afterwards.** This is the part that makes the whole bargain
above affordable: capture may skip messages, because recovery fills them in.
An OBS screenshot costs 1.5-7s while a recording is running and a caption
stays up for about one second on either strip, so a window caught live is
missing some of what it showed -- measured at two of three captions on one
between-spins pass, and rather worse than that on a win. :func:`_recover`
decodes the window's own clip, writes out each frame where the caption
*changed* and reads it, so the window ends up with the messages the stills
went past.

**It runs on a queue, and that is not tidiness.** A clip is filed at the
moment the window that follows it is often already on screen -- take a win and
the between-spins strip is up within a beat of the presentation's clip being
filed -- and recovering there, inline, took fifteen seconds off a strip that
runs for eight: the run recorded that window opening and closing with not one
frame in between, which is the bug the queue exists to fix. So
:func:`_file_and_record` queues the clip, :func:`_drain_recovery` works
through the queue in the background whenever no window is open, and a window
opening asks it to give way after the single frame it is on. It is the only
work on a run that deliberately runs alongside the watcher, and that is
exactly why it checks :attr:`_ActiveRun.pass_open` before every frame.

**A clip is one window of the strip, and a win presentation's boundaries are
the same two lines the sampler brackets one pass with -- widened by one at the
front.** Recording opens on ``cyclic-game-pays`` (before that event's own
screenshot, so the GAME PAYS banner the frame catches is inside the video) and
closes on ``cyclic-line-pays-cycle-finished`` (after that event's screenshot,
so the last line message is). What that buys is a video of the thing the frames
are evidence of: the amount, then every line message that paid it. The
between-spins strip is bracketed by its own two lines and gets its own clip;
see that window above for why it is recorded at all.

Four other things end a clip, and :attr:`CyclicRunVideo.closed_by` says which:
the next spin cutting the pass short, a *second* win opening before the first
reported finishing (two presentations are two clips, never one long one),
tracking being stopped mid-presentation, and
``CYCLIC_MESSAGES_VIDEO_MAX_SECONDS`` for the closing line that never comes.
That last one is not paranoia -- an unbounded clip is a recording of the rest
of the session, which is exactly what recording per win exists to avoid. It is
also the one that has already been wrong once, at 60s against an 83s
presentation: a clip cut off by a deadline looks exactly like a complete one to
whoever watches it, so it is reported on the run's ``errors`` as well as on the
clip, and the value belongs far above the longest pass a game can legitimately
make rather than just above a typical one.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config.game_config import GameConfig, GameConfigError, load_game_config
from app.config.runtime import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    AppException,
    CyclicMessagesAlreadyRunningError,
    CyclicMessagesLogUnavailableError,
    CyclicMessagesNotRunningError,
    CyclicMessagesRunNotFoundError,
    GameConfigInvalidError,
)
from app.schemas.cyclic_messages import (
    CyclicEvent,
    CyclicEventSource,
    CyclicFrameReading,
    CyclicLiveView,
    CyclicRunDetail,
    CyclicRunState,
    CyclicRunSummary,
    CyclicRunVideo,
    CyclicStatus,
)
from app.schemas.obs import ScreenshotRequest
from app.services import obs as obs_service
from app.utils import game_log, log_search
from app.utils.log_tail import LogFollower
from app.utils.paths import UnsafeNameError, resolve_subdirectory, resolve_within

if TYPE_CHECKING:  # pragma: no cover - the cycle below is a runtime one only
    from collections.abc import Generator

    # `cyclic_text` imports this module to find a run's clip, so importing it
    # back at runtime would be a cycle. These are plain function annotations,
    # never evaluated (`from __future__ import annotations`), so the checker
    # can have the name and the interpreter never needs it.
    from app.services import cyclic_text

logger = get_logger("cyclic_messages")

MANIFEST_NAME = "run.json"

# Directory name per run, and the stamp inside each screenshot's filename.
_RUN_ID_FORMAT = "%Y-%m-%d_%H-%M-%S"
_FILE_TIME_FORMAT = "%H-%M-%S"

# The idle strip's rules, by the part each plays in a loop.
_MESSAGE_EVENT = "cyclic-message-shown"
_CYCLE_STARTED = "cyclic-cycle-started"
_CYCLE_COMPLETED = "cyclic-cycle-completed"

# The win strip's, in the order one presentation writes them. Only the first
# carries an amount; the pass between the second and third is the silent window
# that :func:`_sampler` fills.
_GAME_PAYS = "cyclic-game-pays"
_NO_PAY = "cyclic-no-pay"
_WIN_PRESENTED = "cyclic-win-presented"
_LINE_PAYS_CYCLE_DONE = "cyclic-line-pays-cycle-finished"
_IDLE_STRIP_STARTED = "cyclic-idle-strip-started"
_IDLE_STRIP_ENDED = "cyclic-idle-strip-ended"
_RESULTS_CYCLE_STOPPED = "cyclic-results-cycle-stopped"

# Not rules: the sampled frames, which by definition no log line produces.
# One name per strip, because they are different strips showing different
# things and a reader sorting a run by event should not have to guess which.
_LINE_PAYS_SHOWN = "cyclic-line-pays-shown"
_IDLE_MESSAGE_SHOWN = "cyclic-idle-message-shown"
# Not a sampled frame either: one recovered from the clip afterwards, because
# the live stills could not keep up with the window. See :func:`_recover`.
_RECOVERED = "cyclic-message-recovered"

# Not rules either: the two ways a clip ends without a log line saying so.
_RUN_STOPPED = "run-stopped"
_TIME_LIMIT = "time-limit"
_STRIP_LOOPED = "strip-looped"

# OBS reports the recording's path the moment it stops, which on Windows is
# before the muxer has let go of the handle. Retried rather than failed: the
# file is right there and a moment later it moves.
_MOVE_ATTEMPTS = 5
_MOVE_RETRY_SECONDS = 0.3

# How long a stop waits for the capture loop to finish the frame it is on
# before cancelling it outright. Sized above the 1.5-7s an OBS screenshot costs
# while a recording is running, so the ordinary case always returns through the
# loop's own code and only a wedged OBS reaches the cancel.
_SAMPLER_STOP_SECONDS = 10.0


# What a clip is a recording of. On the clip so its file name, and the run
# view beside it, say which strip it covers -- a run holds both kinds now.
#
# Public because which *bands* a clip's strip draws follows from which strip it
# is of, and that answer lives in `cyclic_text.clip_regions` -- one place, so a
# caption read back off a clip cannot come from a different band than the same
# caption read off the stills beside it.
WIN_VIDEO = "win-video"
IDLE_VIDEO = "idle-video"

# The two of them, one after the other, in one window. Take the win before its
# line messages have been round once and the game goes idle *inside* the
# presentation: the strip runs straight on from the line messages into "GAME
# OVER / GAME PAYS n / PLAY 880 CREDITS" with no break on screen, so the window
# runs straight on with it rather than being torn down and rebuilt. See the
# ``cyclic-idle-strip-started`` branch of :func:`_handle`.
#
# A kind of its own rather than either of the two it spans, for the reason
# every other kind is one: it decides which *bands* the strip draws (both of
# them, since the second half is the between-spins strip) and that answer has
# to be the same for the stills and for the clip read back beside them.
WIN_THEN_IDLE = "win-then-idle"

# Which strip each is, for a summary a reader sees rather than a rule name.
_STRIP_NAMES = {
    WIN_VIDEO: "the win presentation",
    IDLE_VIDEO: "the between-spins strip",
    WIN_THEN_IDLE: "the win presentation and the strip after it",
}


@dataclass(frozen=True)
class _Pending:
    """One written frame waiting to be read once its pass closes.

    A path and a sequence, never the picture itself -- the file is already on
    disk and reading it is deferred, not duplicated. Collected into a plain
    list rather than a queue: nothing drains it while the pass is still being
    captured, so there is no producer/consumer pair here to hand work between,
    only a batch that :func:`_read_pending` works through once, in order, after
    the pass ends.
    """

    sequence: int
    path: Path
    regions: tuple[str, ...]
    """Which lines of the strip this frame should be read for.

    Recorded per frame rather than looked up at reading time, because the two
    windows draw different strips and a frame outlives the window that took
    it: by the time the batch is read the run has moved on, and asking "which
    window are we in now" would read a win presentation's frames for a line
    that only the between-spins strip draws.
    """


@dataclass
class _Clip:
    """The recording in progress, and the win presentation it belongs to."""

    cycle: int
    started_at: datetime
    kind: str = WIN_VIDEO
    """Which strip this is a recording of; see :data:`WIN_VIDEO`."""


@dataclass
class _Recovery:
    """A filed clip whose messages have not been read back out of it yet.

    Queued rather than recovered on the spot, and that is the whole point of
    the type existing. Decoding a clip and OCR'ing the frames where its caption
    changed costs seconds to tens of seconds, and the *next* window is often
    already on screen by the time a clip is filed -- a win taken before its
    line messages have been round once puts the between-spins strip straight
    after it. Recovering inline there cost that strip its first fifteen
    seconds, measured, which on a strip that runs for eight is all of it: the
    run recorded the window opening and closing with not one frame in between.

    So the clip goes on a queue and :func:`_drain_recovery` works through it in
    the background, giving way the instant a window opens.
    """

    video: CyclicRunVideo
    written: int = 0
    """Frames of this clip already written out and read.

    Kept so an interrupted recovery resumes instead of starting over: the drain
    gives way mid-clip, and the frames it did get through are already events on
    the run. Counted in *changed* frames rather than in decoded ones, because
    that is the sequence :func:`_recover` walks and re-walking it from the top
    of the file reproduces it exactly.
    """


@dataclass
class _ActiveRun:
    """Everything the watcher needs, and nothing anyone else does."""

    run_id: str
    game: str
    directory: Path
    output_dir: str
    """Run directory relative to the screenshot root, as OBS wants it."""

    log: LogFollower
    """Cursor over the game's log, positioned at its end when the run started."""

    started_at: datetime
    events: list[CyclicEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    last_seen: dict[str, datetime] = field(default_factory=dict)

    next_sequence: int = 0
    """Sequences handed out so far.

    Not ``len(events) + 1`` any more: the watcher and the sampler both record,
    both await a screenshot before appending, and two producers reading the
    list length would hand the same number to both.
    """

    denomination: float | None = None
    """Cents per credit, from the game's own ``UpdatePayTable`` line.

    Read backwards out of the log at start and refreshed whenever the game
    logs a new one, because it is what turns the only amount either log states
    (16800 cents) into the one the strip displays (168).
    """

    # --- loop bookkeeping -------------------------------------------------
    cycle: int = 0
    """Loops begun so far; also the number every event of the current one carries."""

    position: int = 0
    """Messages shown in the current loop."""

    cycle_count: int = 0
    """Loops that ran to completion."""

    pending_start: game_log.DetectedEvent | None = None
    """A ``cyclic-cycle-started`` held back until a message proves the loop real.

    The game arms and ends attract far more often than it displays anything --
    measured on FortuneOx's log, 115 sequence-ends against 30 starts that showed
    a message. Emitting the marker only once a message follows it is what keeps
    a run's loop count equal to the loops a person actually watched.
    """

    clip: _Clip | None = None
    """The win presentation being recorded, if one is; ``None`` while idle."""

    clip_guard: asyncio.Task[None] | None = None
    """Closes :attr:`clip` if its closing line never arrives.

    Its own task for the same reason the sampler is one: the watcher awaits each
    handler in turn, so a deadline waited out inside one would stop the run
    reading the very line it is waiting for.
    """

    closing: asyncio.Task[None] | None = None
    """The clip being stopped and filed right now, if one is.

    Its own task because the watcher is cancelled *where it stands* when a run
    stops: a close that unwound halfway would leave OBS still recording and the
    file where OBS put it, with nothing on the record saying so. So the filing
    is shielded from its caller's cancellation and :func:`_finish` waits for it.
    """

    videos: list[CyclicRunVideo] = field(default_factory=list)
    """One entry per win presentation, failures included -- a clip OBS refused
    is a fact about that win, not an absence."""

    task: asyncio.Task[None] | None = None

    sampler: asyncio.Task[None] | None = None
    """Fills the silent line-message window with frames; see the module docstring.

    Its own task rather than a loop inside :func:`_handle`, because the watcher
    awaits each handler in turn -- sleeping through a pass there would stop the
    run reading the very line that ends it.
    """

    sampled_count: int = 0

    stop_sampling: bool = False
    """Asks the capture loop to finish. See :func:`_stop_sampler`: the loop is
    ended cooperatively rather than cancelled, because a cancellation lands
    wherever the loop happened to be -- and where it usually is, is half-way
    through a screenshot whose sequence number it has already taken."""

    sample_rate: float = 0.0
    """Frames a second the capture loop actually managed on the current pass.

    Measured rather than assumed, for the reason ``utils/tile_video`` measures
    its own: ``CYCLIC_MESSAGES_SAMPLE_INTERVAL_SECONDS`` is a floor and an OBS
    round trip is what decides the rate. Updated by the sampler as it goes, so
    it freezes at the pass's achieved rate when the sampler is cancelled.
    """

    sample_frames: int = 0
    """Frames the current pass has taken; the numerator of :attr:`sample_rate`."""

    pass_open: bool = False
    """Whether the run is inside a win presentation right now.

    Set on ``cyclic-game-pays`` and cleared when the pass ends, and the one
    thing that decides whether a written frame is collected for
    :func:`_read_pending`. The sampler's own liveness is nearly the same window
    but not exactly: the frames for the opening and closing log lines are taken
    just outside it, and those two captions are worth reading as much as the
    sampled ones.
    """

    live_kind: str = ""
    """Which strip :attr:`live_cycles` is of -- :data:`WIN_VIDEO`,
    :data:`IDLE_VIDEO` or, when the view spans both halves of one spin,
    :data:`WIN_THEN_IDLE`. Empty before the run has shown any of them.

    Not :attr:`pass_kind`, which is cleared the moment a window closes: the
    live view goes on showing a finished window's frames, and a card that
    captioned the between-spins strip as a win presentation was describing the
    wrong thing. Set beside `live_cycles` for that reason -- the two are one
    answer to "which window am I looking at", and :func:`_live_on` is the only
    thing that writes either.
    """

    live_cycles: list[int] = field(default_factory=list)
    """Cyclic sequences the live view shows, oldest first.

    Not ``cycle``, which walks on through the idle attract loops that follow a
    win and would take the live view off the pass a tester is still looking at.

    A *list* rather than one sequence because a spin's presentation can be two
    of them. Let a win's line messages finish and then take it, and the
    between-spins strip opens a window of its own -- a second sequence, on the
    record as a second sequence, and rightly so. But it is the same spin
    continuing, and replacing the live view with it emptied the card of every
    frame of the win that had just been captured. So the new sequence is
    *appended* and the card grows; :func:`_spin_over` is where that happens
    and :attr:`win_cycle` is how it knows the two belong together.

    Reset to a single entry whenever a genuinely new spin's window opens.
    """

    win_cycle: int | None = None
    """The win presentation whose between-spins strip has not run yet.

    Set when a win opens a window and consumed by the ``cyclic-idle-strip-
    started`` that follows it, which is the moment the win was taken. Its only
    job is to tell that line's two cases apart: a strip opening after *this
    run's* win is the same spin continuing and joins it, while one opening
    after a losing spin -- or after the strip has already been round -- is a
    spin of its own and starts the live view again.
    """

    pass_kind: str = ""
    """Which strip the window currently open is of -- :data:`WIN_VIDEO`,
    :data:`IDLE_VIDEO` or :data:`WIN_THEN_IDLE`, empty when none is open.

    There to keep one strip's closing lines off the other's window. The two
    can overlap in the log: take the win before its line messages have been
    round once and ``cyclic-line-pays-cycle-finished`` arrives *after* the game
    has gone idle, where -- read as "close the window" -- it tore down the
    between-spins window a second after it opened, which is why that strip's
    second line never got captured. :data:`WIN_THEN_IDLE` is that same line
    arriving at a window which never closed, and it is refused for the same
    reason: the strip it names has moved on, and the moment is a frame on the
    timeline rather than the end of anything.
    """

    pass_interval: float = 0.0
    """The interval the window currently open asked for.

    Reported beside the rate actually achieved, and taken from the window
    rather than from one setting: the two strips are sampled at different
    speeds, so reading `CYCLIC_MESSAGES_SAMPLE_INTERVAL_SECONDS` here told a
    between-spins window it was 1.0s behind when 2.0s was exactly what it had
    asked for -- and raised a coverage warning about it.
    """

    pass_regions: tuple[str, ...] = ()
    """Lines the strip is drawing in the window currently open. Set when one
    opens, and stamped onto every frame taken inside it."""

    # --- the knobs the capture loop reads, and why they are here ----------
    #
    # Every one of these was a parameter of :func:`_sampler` until a window
    # needed to change mid-flight. Taking the win before the line messages have
    # been round once does exactly that: the strip runs straight on into the
    # between-spins messages, which want a different deadline, a different
    # event name and a loop watch the win pass has none of -- and the one thing
    # that must *not* happen is a gap in the frames while a sampler is stopped
    # and another started. So they live on the run and the loop re-reads them
    # every frame, and :func:`_retune` is the whole of "the window changed".
    pass_opener: game_log.DetectedEvent | None = None
    """The log line each sampled frame quotes as the boundary that opened it."""

    pass_event: str = ""
    """Name stamped on a sampled frame -- one per strip, so a reader sorting a
    run by event never has to guess which strip a frame is of."""

    pass_deadline: float = 0.0
    """When the capture loop gives up, on the monotonic clock. Absolute rather
    than a duration because a retune moves it, and moving a duration part-way
    through a window is ambiguous about what it is measured from."""

    pass_closing_expected: bool = False
    """Whether reaching that deadline is worth complaining about. See
    :func:`_sampler`: the game writes the end of a win pass and writes nothing
    at all for the end of a between-spins one."""

    pass_watch: cyclic_text.LoopWatch | None = None
    """Ends the window once the strip has come back round, or ``None`` to run
    to the deadline. Decided from the picture -- see :func:`_loop_watch`."""

    unfiled: list[int] = field(default_factory=list)
    """Sequences of frames captured with no window open, awaiting a cycle.

    A cycle is a *window* of the strip, and one log-driven frame is written
    before its window's own marker line: ``cyclic-game-over`` is captured 400ms
    after ``GameOverMsg`` while ``cyclic-idle-strip-started`` -- the line that
    increments :attr:`cycle` -- is the next one. Recorded with the cycle
    current at the time, that frame lands in the window that just *ended* and
    :func:`live` (which scopes to one cycle) never shows it, so the between-spins
    strip's first message was captured, read, and still missing from the card.

    Re-filed by :func:`_adopt` the moment a window opens. See it for why the
    frame belongs to the window that follows it rather than the one before.
    """

    pending_reads: list[_Pending] = field(default_factory=list)
    """Frames captured and not yet read, whichever window took them.

    Worked through by :func:`_read_pending` the moment a pass closes -- never
    while one is open, so reading never competes with capture. Appended to
    *including* while no pass is open, because a log-driven frame can be
    written before its window opens and its caption is no less real for it:
    see :func:`_capture`. Such a frame waits here for the next pass to close,
    or for the run to stop, which also drains this.
    """

    reading_now: bool = False
    """Whether :func:`_read_pending` is running right now. There is no reader
    task to ask about instead: reading is a plain awaited call on the watcher's
    own task, made once a pass closes, so this is the only way anything else
    (``status()``, ``live()``) can tell a read from an idle run."""

    read_count: int = 0
    """Frames read so far this run, across every pass -- never reset between
    them, unlike :attr:`pending_reads`."""

    pending_recovery: list[_Recovery] = field(default_factory=list)
    """Filed clips whose messages have not been read back out of them yet.

    Appended to as each clip is filed and worked through by
    :func:`_drain_recovery` whenever no window is open. A queue rather than a
    call at the point of filing because the two compete: see :class:`_Recovery`.
    """

    recovering: asyncio.Task[None] | None = None
    """The drain, if one is running. Its own task -- and the *only* piece of
    work on this run that deliberately runs alongside the watcher -- because
    the watcher handles one log line at a time and a recovery waited out inside
    a handler delays every line behind it, including the one that opens the
    next window."""

    stop_recovery: bool = False
    """Asks the drain to give way. Set when a window opens and when the run is
    sealed; the drain checks it before each frame, so the most a window can
    overlap with a recovery is the one frame already in flight."""

    recovered_count: int = 0
    """Frames recovered from a clip so far this run. Reported beside the
    sampled count because the two are different evidence: a sampled frame is
    what the stills caught live, and this is what they went past."""

    no_pay_count: int = 0
    """Spins this run that paid nothing, and therefore showed no win strip.

    Counted rather than merely recorded because it is the answer to the
    question a silent tracker provokes: a run capturing nothing because the
    game keeps losing and a run capturing nothing because it is broken look
    identical without this. On FortuneOx's own log it is the majority case --
    29 losing spins against 26 winning ones.
    """

    no_pay_since_win: int = 0
    """Of those, how many since the last win -- reset by ``cyclic-game-pays``.
    The live view's number: 'the last four spins paid nothing' is what makes a
    waiting card obviously correct rather than possibly stuck."""

    index: dict[int, int] = field(default_factory=dict)
    """Sequence to position in :attr:`events`.

    The reader finishes with a frame long after the capture loop appended it
    and both producers are still appending, so a reading finds its event by
    this rather than by ``events[sequence - 1]`` -- which the two-producer
    ordering already made untrue.
    """

    last_write: float = 0.0
    """When the manifest was last flushed, on the monotonic clock. See
    :func:`_touch`: the manifest is the whole run every time it is written."""

    finishing: bool = False
    """Set once the run is being sealed, to refuse a *new* sampling pass or clip.

    The final catch-up read replays lines that are already history, and a
    ``WinBangDone`` among them would otherwise open a pass that outlives the
    run -- which under ``filterwarnings = error`` is a test failure, not just
    an oddity.
    """


_run: _ActiveRun | None = None
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Serialise start and stop. Built lazily, like the OBS service's, since a
    lock made at import time would bind to the wrong loop in tests."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


# --- the active game ------------------------------------------------------


def _active_config() -> tuple[str, GameConfig]:
    """Load the selected game's config, or explain why it cannot be used."""
    name = settings.ideck_active_game
    path = settings.ideck_game_config_path_for(name)
    try:
        return name, load_game_config(path)
    except GameConfigError as exc:
        raise GameConfigInvalidError(
            f"Could not load game config {path}: {exc}"
        ) from exc


def _log_for(name: str, config: GameConfig) -> Path:
    """The game's log, checked to actually be there -- refusing up front, since a
    run against a log that never appears looks identical to a quiet game."""
    if config.log_path is None:
        raise CyclicMessagesLogUnavailableError(
            f"The game config for {name!r} does not declare a 'log' path to follow"
        )
    if not config.log_path.is_file():
        raise CyclicMessagesLogUnavailableError(
            f"The log for {name!r} does not exist yet: {config.log_path}. "
            "Start the game and try again."
        )
    return config.log_path


# --- the manifest ---------------------------------------------------------


def _capture_root() -> Path:
    """Directory holding one subdirectory per run."""
    return resolve_subdirectory(
        settings.obs_screenshot_dir, settings.CYCLIC_MESSAGES_DIR_NAME
    )


def _message_count(run: _ActiveRun) -> int:
    """Messages seen, which is not the event count -- the sequence markers are
    events too. Counted by ``captured`` rather than by rule name, so a message
    added to either family counts without editing this."""
    return sum(1 for event in run.events if event.captured)


def _detail(
    run: _ActiveRun, *, status: CyclicRunState, stopped: datetime | None
) -> CyclicRunDetail:
    """Build the record that both the manifest and the API return."""
    return CyclicRunDetail(
        run_id=run.run_id,
        game=run.game,
        status=status,
        started_at=run.started_at,
        stopped_at=stopped,
        message_count=_message_count(run),
        sampled_count=run.sampled_count,
        cycle_count=run.cycle_count,
        log_path=str(run.log.path),
        videos=list(run.videos),
        # Sorted, not appended: the watcher and the sampler each await a
        # screenshot before appending, so the order they finish in is not the
        # order they were triggered in. The sequence is what a reader trusts.
        events=sorted(run.events, key=lambda event: event.sequence),
        errors=list(run.errors),
    )


def _write_manifest(run: _ActiveRun, detail: CyclicRunDetail) -> None:
    """Persist the run record atomically. Never raises -- losing one write is
    better than taking down a live run over it; the next event retries."""
    target = run.directory / MANIFEST_NAME
    temporary = target.with_name(f".{MANIFEST_NAME}.tmp")
    try:
        temporary.write_text(
            detail.model_dump_json(indent=2, by_alias=True) + "\n", encoding="utf-8"
        )
        temporary.replace(target)
    except OSError:
        logger.exception("Could not write the cyclic message manifest at %s", target)
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def _touch(run: _ActiveRun) -> None:
    """Flush the manifest, at most once every
    ``CYCLIC_MESSAGES_MANIFEST_INTERVAL_SECONDS``.

    What every live writer calls instead of :func:`_write_manifest`. The
    manifest is the *whole* run each time it is written, so writing it per
    change is quadratic in the length of the run -- fine at one event per 8s of
    attract, and not fine at ten frames a second with a reading landing on each
    of them. A crash costs at most one interval of the record; a stop calls
    :func:`_write_manifest` directly and is never throttled.
    """
    now = time.monotonic()
    if now - run.last_write < settings.CYCLIC_MESSAGES_MANIFEST_INTERVAL_SECONDS:
        return
    run.last_write = now
    _write_manifest(run, _detail(run, status=CyclicRunState.RUNNING, stopped=None))


def _read_manifest(path: Path) -> CyclicRunDetail | None:
    """Read one ``run.json``, or ``None`` if it is unusable -- drops out of the
    listing rather than breaking it."""
    try:
        document: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Skipping unreadable cyclic message manifest at %s", path)
        return None
    try:
        return CyclicRunDetail.model_validate(document)
    except ValueError:
        logger.warning("Skipping malformed cyclic message manifest at %s", path)
        return None


def _note(run: _ActiveRun, message: str) -> None:
    """Record a problem that did not stop the run, once."""
    if message not in run.errors:
        run.errors.append(message)


# --- video: one clip per win presentation ---------------------------------


def _recording_dir(run_id: str) -> str:
    """Where OBS should write the run's clips, relative to the recording root."""
    return f"{settings.CYCLIC_MESSAGES_DIR_NAME}/{run_id}"


def _clip_name(clip: _Clip, suffix: str) -> str:
    """Filename that says which sequence the clip covers and which strip it is
    of, e.g. ``cycle-003_win-video_16-36-21.mp4`` or
    ``cycle-004_idle-video_16-36-40.mp4`` -- so it sits beside that sequence's
    own frames in a directory listing rather than under an OBS timestamp."""
    stamp = clip.started_at.strftime(_FILE_TIME_FORMAT)
    return f"cycle-{clip.cycle:03d}_{clip.kind}_{stamp}{suffix}"


async def _open_clip(
    run: _ActiveRun, *, kind: str = WIN_VIDEO, replaces: str = _GAME_PAYS
) -> None:
    """Begin recording the window that just opened.

    Both strips are recorded and each gets its own clip -- a win presentation
    and the between-spins strip that follows it are two different things to
    watch, and one file spanning both could not be told apart afterwards. The
    two are switched on separately (``CYCLIC_MESSAGES_RECORD_VIDEO`` and
    ``CYCLIC_MESSAGES_RECORD_IDLE_VIDEO``), because recording costs the window
    some of its screenshot rate and a machine may want that back.

    Never raises: a window whose recording failed still screenshots every
    message, and the reason travels on the run's own list of clips.
    """
    wanted = (
        settings.CYCLIC_MESSAGES_RECORD_IDLE_VIDEO
        if kind == IDLE_VIDEO
        else settings.CYCLIC_MESSAGES_RECORD_VIDEO
    )
    if not wanted or run.finishing:
        return

    # A window opening before the previous one reported finishing gets its own
    # clip; two of them in one file could not be told apart afterwards.
    # ``replaces`` is what that predecessor's record says ended it, which is
    # this event and not a guess -- a win clip cut off by the game going idle
    # was reading as "the next win began".
    await _close_clip(run, closed_by=replaces)

    clip = _Clip(cycle=max(1, run.cycle), started_at=datetime.now(), kind=kind)
    try:
        await obs_service.start_recording(_recording_dir(run.run_id))
    except AppException as exc:
        run.videos.append(
            CyclicRunVideo(
                kind=clip.kind,
                cycle=clip.cycle,
                started_at=clip.started_at,
                error=exc.message,
            )
        )
        _note(run, f"video: {exc.message}")
        logger.warning(
            "Could not record %s for %s: %s", clip.kind, run.run_id, exc.message
        )
        return

    run.clip = clip
    run.clip_guard = asyncio.create_task(
        _guard_clip(run), name=f"cyclic-messages-clip-{run.run_id}"
    )
    logger.info(
        "Cyclic message run %s is recording %s for sequence %d",
        run.run_id,
        clip.kind,
        clip.cycle,
    )


async def _file_clip(
    run: _ActiveRun, clip: _Clip, source: str | None
) -> tuple[str | None, str | None]:
    """Move a finished recording beside the run's screenshots, under a name that
    says which sequence it is. Returns ``(file name, error)``.

    OBS writes under its own recording root, which is the same directory as the
    screenshot root by default but need not be -- so the file is moved rather
    than assumed to have landed in the right place, and a move that keeps
    failing leaves the video where OBS put it rather than losing it.
    """
    if not source:
        return None, "OBS did not report where it wrote the recording"

    origin = Path(source)
    if not origin.is_file():
        return None, f"OBS reported a recording at {source}, but there is no file there"

    target = run.directory / _clip_name(clip, origin.suffix)
    if origin == target:
        return origin.name, None

    attempt = 0
    while True:
        attempt += 1
        try:
            run.directory.mkdir(parents=True, exist_ok=True)
            shutil.move(str(origin), str(target))
        except OSError as exc:
            if attempt >= _MOVE_ATTEMPTS:
                logger.warning(
                    "Could not move %s beside its screenshots: %s", origin, exc
                )
                return None, (
                    f"The video was recorded to {source} but could not be moved "
                    f"beside the screenshots: {exc}"
                )
            await asyncio.sleep(_MOVE_RETRY_SECONDS)
        else:
            return target.name, None


def _is_recording(run: _ActiveRun) -> bool:
    """Whether a clip is open *or* still being filed.

    Both count as recording on purpose: it makes "not recording" mean the clip
    is on the record, which is what a reader waiting for one needs.
    """
    if run.clip is not None:
        return True
    return run.closing is not None and not run.closing.done()


async def _close_clip(run: _ActiveRun, *, closed_by: str) -> None:
    """End the clip in progress and file it with the screenshots. Never raises,
    and does nothing when no clip is open -- every caller may be the one that
    finds the presentation already over."""
    clip = run.clip
    if clip is None:
        return
    # Both of these happen before the first await, and that is load-bearing.
    # Claiming the clip is what stops the watcher and the guard both closing it.
    # Handing the work to a task in the same breath is what stops a caller
    # cancelled *anywhere* below here -- Stop tracking cancels the watcher where
    # it stands -- from leaving OBS recording with nothing on the record. An
    # await between the two leaves exactly that window open.
    run.clip = None
    run.closing = asyncio.create_task(
        _file_and_record(run, clip, closed_by),
        name=f"cyclic-messages-closing-{run.run_id}",
    )
    # Shielded rather than awaited plainly: see _ActiveRun.closing. The caller
    # still sees the cancellation; what it does not do is take the filing with it.
    await asyncio.shield(run.closing)
    run.closing = None


async def _await_closing(run: _ActiveRun) -> None:
    """Wait for a filing that outlived the task which started it."""
    task, run.closing = run.closing, None
    if task is not None and not task.done():
        await asyncio.gather(task, return_exceptions=True)


async def _file_and_record(run: _ActiveRun, clip: _Clip, closed_by: str) -> None:
    """Stop OBS, move the file beside the screenshots, put it on the record.
    Never raises: a clip is a record, never a reading.

    Everything that awaits lives here rather than in :func:`_close_clip`,
    including retiring the deadline -- this task is the one nobody cancels.
    """
    await _stop_guard(run)

    stopped_at = datetime.now()
    try:
        result = await obs_service.stop_recording()
    except AppException as exc:
        video = CyclicRunVideo(
            kind=clip.kind,
            cycle=clip.cycle,
            started_at=clip.started_at,
            stopped_at=stopped_at,
            closed_by=closed_by,
            error=exc.message,
        )
    else:
        file_name, error = await _file_clip(run, clip, result.output_path)
        video = CyclicRunVideo(
            file_name=file_name,
            source_path=result.output_path,
            kind=clip.kind,
            cycle=clip.cycle,
            started_at=clip.started_at,
            stopped_at=stopped_at,
            closed_by=closed_by,
            error=error,
        )

    run.videos.append(video)
    if video.file_name and settings.CYCLIC_MESSAGES_RECOVER_FROM_CLIP:
        # Queued the moment it is filed, and recovered whenever nothing is
        # being captured -- never here, where the caller is very often about to
        # open the window that follows this clip. See :class:`_Recovery`.
        run.pending_recovery.append(_Recovery(video=video))
    if video.error:
        _note(run, f"video: {video.error}")
        logger.warning(
            "Cyclic message run %s could not save sequence %d: %s",
            run.run_id,
            clip.cycle,
            video.error,
        )
    else:
        logger.info(
            "Cyclic message run %s saved sequence %d as %s (%s)",
            run.run_id,
            clip.cycle,
            video.file_name,
            closed_by,
        )
    _write_manifest(run, _detail(run, status=CyclicRunState.RUNNING, stopped=None))


async def _guard_clip(run: _ActiveRun) -> None:
    """Close a clip whose closing line never arrives.

    The ordinary end of a presentation is a log line; this is only for the one
    that never comes. Without it the clip runs until the next win or until
    tracking stops, which is the whole-run recording this feature exists not to
    make.
    """
    await asyncio.sleep(max(0.1, settings.CYCLIC_MESSAGES_VIDEO_MAX_SECONDS))
    # Cleared before closing: _close_clip cancels the guard, and a task
    # cancelling itself would never come back from the cancellation.
    run.clip_guard = None
    # Said out loud on the run, not just on the clip: a truncated video looks
    # exactly like a complete one to whoever watches it, so the one place this
    # can be noticed is here.
    _note(
        run,
        f"video: the clip for sequence {run.clip.cycle if run.clip else 0} was "
        f"cut off after {settings.CYCLIC_MESSAGES_VIDEO_MAX_SECONDS:g}s because "
        "the line messages never reported finishing; raise "
        "CYCLIC_MESSAGES_VIDEO_MAX_SECONDS if the presentation is longer",
    )
    logger.warning(
        "Cyclic message run %s stopped recording after %.1fs without the line "
        "messages reporting that they finished",
        run.run_id,
        settings.CYCLIC_MESSAGES_VIDEO_MAX_SECONDS,
    )
    await _close_clip(run, closed_by=_TIME_LIMIT)


async def _stop_guard(run: _ActiveRun) -> None:
    """End the clip's deadline task. Awaiting the cancellation is not optional
    -- an unawaited one is the pending-task warning that fails the test suite."""
    task, run.clip_guard = run.clip_guard, None
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# --- capturing ------------------------------------------------------------


def _screenshot_name(sequence: int, event: str, at: datetime | None) -> str:
    """Filename that sorts by sequence and still says what it is, e.g.
    ``007_cyclic-message-shown_14-32-14``."""
    stamp = (at or datetime.now()).strftime(_FILE_TIME_FORMAT)
    return f"{sequence:03d}_{event}_{stamp}"


async def _record(
    run: _ActiveRun,
    detected: game_log.DetectedEvent,
    *,
    position: int | None,
    source: CyclicEventSource = CyclicEventSource.LOG,
    at: datetime | None = None,
) -> Path | None:
    """Append one event to the run, screenshotting it unless it is a marker.

    Returns where the frame landed, or ``None`` when none was taken -- the
    capture loop looks at its own frames to notice the strip coming round.

    ``at`` overrides the log line's own stamp, which only a sampled event needs:
    every frame of one pass is opened by the same boundary line, so taking the
    time from it would stamp them all identically.
    """
    # Reserved before the first await: see _ActiveRun.next_sequence.
    run.next_sequence += 1
    sequence = run.next_sequence
    at = at or detected.line.timestamp

    def _append(screenshot: str | None, capture_error: str | None) -> None:
        """Put this event on the record under the sequence already reserved.

        A closure so the cancellation path below can append the very same event
        the ordinary path would, minus its picture -- two constructions of one
        event would eventually disagree about a field.
        """
        run.events.append(
            CyclicEvent(
                sequence=sequence,
                event=detected.event,
                at=at,
                summary=detected.summary,
                cycle=max(1, run.cycle),
                position=position,
                fields=dict(detected.fields),
                captured=detected.capture,
                screenshot=screenshot,
                capture_error=capture_error,
                source=source,
                log_line=detected.line.raw,
            )
        )
        run.index[sequence] = len(run.events) - 1
        if source is CyclicEventSource.SAMPLED:
            run.sampled_count += 1

    screenshot: str | None = None
    capture_error: str | None = None
    saved: Path | None = None

    if not detected.capture:
        # A loop boundary: structure, not a frame. Deliberately still recorded.
        pass
    elif sequence > settings.CYCLIC_MESSAGES_MAX_EVENTS:
        capture_error = (
            f"Event cap of {settings.CYCLIC_MESSAGES_MAX_EVENTS} reached; "
            "the event was recorded without a screenshot"
        )
    else:
        name = _screenshot_name(sequence, detected.event, at)
        try:
            if detected.delay_ms:
                # The transition lands; the strip draws the new text a beat
                # later. Inside the guard, not before it: this sleep is where
                # a stop most often lands (it is up to half a second of doing
                # nothing), and a cancellation here would otherwise unwind past
                # a sequence already reserved.
                await asyncio.sleep(detected.delay_ms / 1000)
            result = await obs_service.take_screenshot(
                ScreenshotRequest(
                    # JPEG by default, and a coverage setting rather than a
                    # disk one -- see CYCLIC_MESSAGES_SCREENSHOT_FORMAT. The
                    # loop has to come round again inside one message's dwell,
                    # and a PNG encode of a 1280-wide frame is round-trip time
                    # spent on fidelity the caption does not need.
                    image_format=settings.CYCLIC_MESSAGES_SCREENSHOT_FORMAT,
                    quality=settings.CYCLIC_MESSAGES_SCREENSHOT_QUALITY,
                    width=settings.CYCLIC_MESSAGES_SCREENSHOT_WIDTH,
                    file_name=name,
                    output_dir=run.output_dir,
                    # Never read back: `run.events` carries a filename, and the
                    # frame is reopened from disk when its caption is read. So
                    # the inline base64 `GetSourceScreenshot` this would
                    # otherwise trigger is a second full re-encode nobody
                    # uses -- skipping it is most of the difference between a
                    # capture and the 2s this loop is asked to keep to.
                    include_image_data=False,
                )
            )
        except AppException as exc:
            # One failed screenshot isn't a reason to end the run -- OBS
            # re-identifies on the next request even if its socket dropped.
            capture_error = exc.message
            _note(run, f"{detected.event}: {exc.message}")
            logger.warning("Screenshot failed for %s: %s", detected.event, exc.message)
        except asyncio.CancelledError:
            # The run stopped while this frame was being settled for or taken.
            # The sequence was reserved before either await, so unwinding
            # without appending would leave a hole in the numbering -- a number
            # issued and no event carrying it, which is precisely what the
            # numbering exists to rule out. Recorded without its picture
            # instead, saying why.
            #
            # The watcher is cancelled where it stands by design (see
            # :func:`_stop_watching`), so this is reached whenever a stop lands
            # inside the capture, and nothing is awaited in here -- the `raise`
            # still unwinds the producer exactly as it was going to.
            _append(None, "The run stopped before this frame was taken")
            raise
        else:
            saved = Path(result.file_path) if result.file_path else None
            screenshot = saved.name if saved else None

    _append(screenshot, capture_error)

    if saved is not None:
        _queue_read(run, sequence=sequence, saved=saved)

    _touch(run)
    return saved


def _queue_read(run: _ActiveRun, *, sequence: int, saved: Path) -> None:
    """Keep one written frame for :func:`_read_pending`.

    Collected, not read: the sampled frames and the log-driven ones alike,
    since "GAME PAYS 168" is a caption like any other.

    **Including the frames captured with no window open**, which is the whole
    reason this is its own function. A log-driven frame can land before its
    window opens -- ``cyclic-game-over`` is captured 400ms after
    ``GameOverMsg`` while ``cyclic-idle-strip-started`` is the *next* line --
    so on a losing spin the one picture the log takes was written while
    ``pass_open`` was still false. Gated on it, that frame's caption was
    dropped on the floor: measured on a real losing spin it reads "Game Over"
    at 97, and nothing else in the run ever looked at it. It waits here for the
    next pass to close, or for the run to stop, both of which drain the queue.

    The bands cannot come from :attr:`_ActiveRun.pass_regions` in that case --
    it still holds whichever window ran *last*, so after a win presentation it
    names the one band that strip draws while the between-spins strip draws
    two, and the second would never be cropped. Outside a presentation the
    strip on screen is the between-spins one, so that is what a frame with no
    window of its own is read as.
    """
    if not run.pass_open:
        # Captured between two windows, so it has no cycle of its own yet --
        # see :attr:`_ActiveRun.unfiled` and :func:`_adopt`.
        run.unfiled.append(sequence)
    run.pending_reads.append(
        _Pending(
            sequence=sequence,
            path=saved,
            regions=(
                run.pass_regions
                if run.pass_open
                else settings.cyclic_messages_idle_regions
            ),
        )
    )


def _adopt(run: _ActiveRun) -> None:
    """Move the frames captured before this window opened into its cycle.

    A frame belongs to the window it is a picture *of*, and the one frame this
    moves is a picture of the strip that is about to be sampled, not of the one
    that just stopped: ``GameOverMsg`` is what puts "GAME OVER" on the
    between-spins strip, and the frame taken 400ms later shows it. Filed under
    the outgoing cycle it was captured, read, and then filtered out of
    :func:`live`, which scopes to one cycle -- so the strip's first message was
    missing from the card while sitting in the run.

    Called by every window opening rather than only the between-spins one: the
    frame precedes whichever window comes next, and a win presentation is
    already inside its own pass by the time its ``cyclic-game-pays`` frame is
    taken, so in practice there is nothing for it to adopt.
    """
    if not run.unfiled:
        return
    adopted = set(run.unfiled)
    run.unfiled = []
    for sequence in adopted:
        at = run.index.get(sequence)
        if at is None:
            continue
        run.events[at] = run.events[at].model_copy(update={"cycle": run.cycle})
    logger.info(
        "Cyclic message run %s: %d frame(s) taken before this window opened "
        "belong to cycle %d",
        run.run_id,
        len(adopted),
        run.cycle,
    )


# --- the amount, and the denomination that scales it ----------------------


def _read_denomination(path: Path) -> float | None:
    """The denomination the game last logged, read *backwards* out of the log.

    The game writes ``UpdatePayTable`` only when a denomination changes, so a
    run that starts on an already-running game would otherwise see none until
    the player changed one -- and every "GAME PAYS" until then would report
    cents. Reading backwards costs one seek and answers immediately.
    """
    found = log_search.last_match(path, game_log.PAYTABLE_LOADED)
    if found is None:
        return None
    return _as_denomination(found.match.groupdict().get("denom"))


def _as_denomination(raw: str | None) -> float | None:
    """Parse a logged denomination, refusing the values that cannot scale."""
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _pays_summary(run: _ActiveRun, detected: game_log.DetectedEvent) -> str:
    """What the strip is about to display, in the units it displays it in.

    The log states cents; the strip states credits. Without a denomination the
    cents are reported *as cents and said to be* -- guessing a rate here would
    put a number on screen that is wrong by a factor of the denomination while
    looking exactly as confident as a right one.
    """
    cents = _as_denomination(detected.fields.get("win_cents"))
    if cents is None:
        return detected.summary
    if run.denomination is None:
        return f"Game pays {cents:g} cents (denomination not logged yet)"
    return f"Game pays {cents / run.denomination:g}"


def _pays_event(
    run: _ActiveRun, detected: game_log.DetectedEvent
) -> game_log.DetectedEvent:
    """``cyclic-game-pays`` with the credits worked out and both units kept."""
    fields = dict(detected.fields)
    cents = _as_denomination(detected.fields.get("win_cents"))
    if run.denomination is not None:
        fields["denom"] = f"{run.denomination:g}"
        if cents is not None:
            fields["win_credits"] = f"{cents / run.denomination:g}"
    return dataclasses.replace(
        detected, summary=_pays_summary(run, detected), fields=fields
    )


# --- reading a pass's frames, once it has closed ---------------------------


def _attach(run: _ActiveRun, sequence: int, readings: list[CyclicFrameReading]) -> None:
    """Hang a frame's readings -- one per line of the strip -- on its event.

    By :attr:`_ActiveRun.index` rather than by position: the watcher and the
    sampler are both still appending while a pass is open, so nothing about
    ``events`` is indexable by sequence.
    """
    at = run.index.get(sequence)
    if at is None:
        logger.warning("No event %d to hang a caption reading on", sequence)
        return
    run.events[at] = run.events[at].model_copy(update={"readings": readings})


def _loop_watch(run: _ActiveRun) -> cyclic_text.LoopWatch | None:
    """A watcher for the between-spins strip coming round, or None if the
    window should simply run to its deadline.

    Never fails the window: a game whose caption regions cannot be resolved
    still gets its frames, it just gets them until the deadline instead of
    until the lap closes.
    """
    if not settings.CYCLIC_MESSAGES_IDLE_STOP_AFTER_LOOP:
        return None
    # Locally, for the reason the TYPE_CHECKING block above gives.
    from app.services import cyclic_text

    try:
        # The lap bands, not every band: see
        # CYCLIC_MESSAGES_IDLE_LOOP_REGIONS. Band 1 is still captured and read
        # -- it just does not decide when the strip has run out of things to
        # show, because its cycle is forty messages long and band 2's is three.
        regions = cyclic_text.strip_rois(
            run.game, settings.cyclic_messages_idle_loop_regions
        )
    except AppException as exc:
        _note(run, f"Not watching for the strip's loop: {exc.message}")
        return None
    return cyclic_text.LoopWatch(regions=regions)


async def _start_recovery(run: _ActiveRun) -> None:
    """Ask for the queued clips to be read back, in the background.

    Fire and forget, and deliberately *not* awaited by the handler that closed
    the window: see :class:`_Recovery`. That handler's next job may be opening
    the window which follows, and a recovery waited out here is time that
    window spends on screen uncaptured.

    Does nothing if a drain is already running -- one queue, one worker, so the
    clips are recovered in the order they were filed and nothing reads two at
    once.
    """
    if not settings.CYCLIC_MESSAGES_RECOVER_FROM_CLIP:
        run.pending_recovery.clear()
        return
    if not run.pending_recovery or run.finishing:
        return
    task = run.recovering
    if task is not None and not task.done():
        return
    run.stop_recovery = False
    run.recovering = asyncio.create_task(
        _drain_recovery(run), name=f"cyclic-messages-recovery-{run.run_id}"
    )


async def _drain_recovery(run: _ActiveRun) -> None:
    """Work through the queued clips while nothing is being captured.

    The one piece of work on a run that deliberately runs alongside the
    watcher, and the guards are what make that safe: a window opening sets
    :attr:`_ActiveRun.stop_recovery` and this returns before its next frame,
    leaving the rest of the clip on the queue for the next quiet moment. So the
    most a recovery can overlap a capture is the single frame already in
    flight -- against the fifteen seconds recovering inline cost the window
    that followed a taken win.

    Never raises: a background task that does is a silent failure, and a clip
    that will not decode costs the messages it held and nothing else.
    """
    try:
        while run.pending_recovery:
            if _recovery_stopped(run) or _finishing(run) or _capturing(run):
                return
            item = run.pending_recovery[0]
            if not await _recover(run, item):
                # A window opened part-way through. Left on the queue with its
                # progress, so the rest of it is picked up rather than re-read.
                return
            run.pending_recovery.pop(0)
    except Exception:
        logger.exception(
            "Cyclic message run %s could not recover a clip's messages", run.run_id
        )


async def _recover(run: _ActiveRun, item: _Recovery) -> bool:
    """Fill one filed clip's messages in from the video itself.

    The live stills cannot keep up with either strip -- a screenshot costs
    about six seconds while OBS records an animating scene, against the two a
    line message stays up, and frames measured 2.6s apart on a between-spins
    strip whose captions change about every second -- so a window caught live
    is missing some of what it showed. The recording is not: it has every
    frame. This decodes the clip, writes out each frame where the caption
    *changed* and reads it, so the window ends up with the messages the stills
    went past.

    **Both strips, not just the win presentation.** The between-spins strip was
    left out of this on the grounds that it holds each caption for the best
    part of ten seconds and its stills therefore catch every one. Measured off
    a real losing spin's own clip it holds each for about a second with a blank
    second between them, and the three stills that pass managed caught two of
    its three captions -- so it needs this exactly as much as a win does.

    Returns whether the clip is finished with. ``False`` means a window opened
    part-way through, and :attr:`_Recovery.written` has been advanced to
    wherever it got to.

    Never raises.
    """
    clip = item.video
    if clip.file_name is None or clip.started_at is None:
        return True

    # Locally, for the reason the TYPE_CHECKING block at the top gives.
    from app.services import cyclic_text

    # The bands *this* clip's strip draws, which is not one fixed region: a win
    # presentation uses one line and the between-spins strip two. Reading an
    # idle clip against the win presentation's single band is reading the one
    # band that strip leaves empty.
    regions = cyclic_text.clip_regions(clip.kind)
    if not regions:
        return True
    try:
        reader = await cyclic_text.live_reader(run.game)
    except AppException as exc:
        _note(run, f"Could not recover this clip's messages: {exc.message}")
        return True

    path = run.directory / clip.file_name
    interval = settings.CYCLIC_MESSAGES_TEXT_INTERVAL_SECONDS
    # Only the frames where the caption changed are read. A clip sampled every
    # second holds each message several times over, and at a second and a half
    # an OCR pass the difference is reading a win presentation in ten seconds
    # rather than eighty -- long enough, at eighty, to still be going when the
    # next spin needs the run.
    changes = cyclic_text.CaptionChanges(regions=reader.regions_in(regions))
    frames = cyclic_text.clip_frames(path, interval)
    # Changed frames walked past this time round; the counterpart of
    # `_Recovery.written`, which says how many of them are already events.
    seen = 0
    recovered = 0
    finished = False
    try:
        while True:
            if _recovery_stopped(run) or _finishing(run) or _capturing(run):
                break
            try:
                # A frame at a time off the decoder rather than a list of them
                # all: a 90s clip's frames are full-size pictures and holding
                # every one at once is hundreds of megabytes, which is why
                # `clip_frames` is a generator in the first place.
                frame = await asyncio.to_thread(_next_clip_frame, frames)
            except Exception as exc:  # noqa: BLE001 - reported, never fatal
                _note(run, f"Could not read this clip back: {exc}")
                logger.warning(
                    "Could not decode %s to recover its messages: %s", path, exc
                )
                finished = True
                break
            if frame is None:
                finished = True
                break
            if not await asyncio.to_thread(changes.changed, frame.image):
                continue
            seen += 1
            if seen <= item.written:
                # Already on the record from an earlier attempt at this clip.
                # Re-walked rather than skipped ahead in the decoder, because
                # `CaptionChanges` compares each frame with the one before it
                # and a jump would compare across the gap.
                continue
            at = clip.started_at + timedelta(seconds=frame.at_seconds)
            run.next_sequence += 1
            sequence = run.next_sequence
            name = _screenshot_name(sequence, _RECOVERED, at)
            target = (
                run.directory / f"{name}.{settings.CYCLIC_MESSAGES_SCREENSHOT_FORMAT}"
            )
            try:
                readings = await asyncio.to_thread(
                    _recover_frame, reader, frame, regions, target
                )
            except Exception as exc:  # noqa: BLE001 - one frame is not the clip
                logger.warning("Could not recover frame %d: %s", frame.index, exc)
                item.written = seen
                continue
            run.events.append(
                CyclicEvent(
                    sequence=sequence,
                    event=_RECOVERED,
                    at=at,
                    summary=(
                        f"Strip {frame.at_seconds:.1f}s into "
                        f"{_STRIP_NAMES.get(clip.kind, clip.kind)}, recovered "
                        "from the clip"
                    ),
                    cycle=clip.cycle or max(1, run.cycle),
                    fields={
                        "at_seconds": f"{frame.at_seconds:.1f}",
                        "strip": clip.kind,
                    },
                    captured=True,
                    screenshot=target.name,
                    source=CyclicEventSource.SAMPLED,
                    log_line=f"recovered from {clip.file_name}",
                    readings=readings,
                )
            )
            run.index[sequence] = len(run.events) - 1
            run.read_count += 1
            run.recovered_count += 1
            item.written = seen
            recovered += 1
            _touch(run)
    finally:
        # Releases the decoder's handle on the file rather than waiting for the
        # generator to be collected -- a clip abandoned part-way through is the
        # ordinary case here, not the exception.
        frames.close()

    if recovered:
        logger.info(
            "Cyclic message run %s recovered %d frame(s) of sequence %s from %s%s",
            run.run_id,
            recovered,
            clip.cycle,
            clip.file_name,
            "" if finished else " so far",
        )
        _write_manifest(run, _detail(run, status=CyclicRunState.RUNNING, stopped=None))
    return finished


def _next_clip_frame(
    frames: Generator[cyclic_text.ClipFrame, None, None],
) -> cyclic_text.ClipFrame | None:
    """One frame off the decoder, or ``None`` at the end of the clip. Blocking."""
    return next(frames, None)


def _recover_frame(
    reader: cyclic_text.LiveReader,
    frame: cyclic_text.ClipFrame,
    regions: tuple[str, ...],
    target: Path,
) -> list[CyclicFrameReading]:
    """Write one decoded clip frame out and read its bands. Blocking."""
    frame.image.save(target)
    return [
        CyclicFrameReading(
            text=line.text,
            repaired=line.repaired,
            confidence=line.confidence,
            reliable=line.reliable,
            engine=cyclic_text.PADDLE,
            region=line.region,
            key=cyclic_text.caption_key(line.repaired),
            crop=line.crop_name,
            read_ms=line.read_ms,
        )
        for line in reader.read_image(frame.image, regions, beside=target)
    ]


# How long a stop waits for the drain to finish the frame it is on before
# cancelling it. Sized above one decode plus one OCR pass (~1.5s for the
# recogniser, more on a cold model), so the ordinary case always returns
# through the drain's own code and only a wedged decode reaches the cancel.
_RECOVERY_STOP_SECONDS = 30.0


def _recovery_stopped(run: _ActiveRun) -> bool:
    """Read :attr:`_ActiveRun.stop_recovery` through a call, for the reason
    :func:`_sampling_stopped` gives."""
    return run.stop_recovery


def _capturing(run: _ActiveRun) -> bool:
    """Read :attr:`_ActiveRun.pass_open` through a call, for the same reason --
    the drain checks it either side of an ``await``, and the watcher sets it
    from outside that coroutine while it is suspended there, which is the whole
    point of checking twice."""
    return run.pass_open


def _ask_recovery_to_stop(run: _ActiveRun) -> None:
    """Ask the drain to give way, and do not wait for it.

    Not awaited, which is the whole difference from :func:`_stop_recovery` and
    the reason both exist. This is called from the one place that must not
    block -- a window opening -- and awaiting the drain there would put the
    remainder of the frame it is on, a decode and an OCR pass, in front of that
    window's first screenshot. Far better than the fifteen seconds recovering
    inline cost, and still the wrong thing to spend on the one path where
    capturing on schedule is the only priority.

    Nothing is lost by not waiting. The drain returns through its own guard at
    the next frame, its task stays on :attr:`_ActiveRun.recovering` until it
    does (so :func:`_start_recovery` will not start a second one), and its
    queue is untouched -- the clips it had not reached are recovered at the
    next quiet moment.
    """
    run.stop_recovery = True


async def _stop_recovery(run: _ActiveRun) -> None:
    """Ask the drain to give way and wait for it, with a cancel behind it.

    The waiting half, for sealing a run and for tests: an unawaited task that
    outlives its run is the pending-task warning that fails the suite under
    ``filterwarnings = error``. See :func:`_ask_recovery_to_stop` for the path
    that deliberately does not wait.

    Asked rather than cancelled, for the reason :func:`_stop_sampler` is: the
    drain is nearly always inside a frame whose sequence number it has already
    taken, and cancelled there that number is spent with no event carrying it.
    The cancel behind it is for the decode that never comes back, which must
    not hold a Stop press open indefinitely.
    """
    task, run.recovering = run.recovering, None
    if task is None or task.done():
        return
    run.stop_recovery = True
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(
            asyncio.gather(task, return_exceptions=True),
            timeout=_RECOVERY_STOP_SECONDS,
        )


async def _read_pending(run: _ActiveRun) -> None:
    """Read every frame collected during the pass that just closed, in order,
    one at a time -- and only after it has closed.

    Called plainly from :func:`_handle` once :attr:`_ActiveRun.pass_open` goes
    false, never spawned as a task: capturing the next pass on schedule is the
    one thing this feature cannot get back if it slips, so nothing is left
    running that could compete with it. By the time this is called there is
    nothing to compete with -- the sampler has already stopped -- so reading
    here costs this pass's own time and nothing else's.

    One at a time is not a tuning choice -- ``utils/paddle_ocr`` keeps one
    recogniser per process and Paddle's are not documented as thread-safe -- but
    it no longer costs a queue or a backlog to say so: there is exactly one
    batch, taken in the order it was captured, and this returns once it is
    read. A backlog that matters is answered by a quicker model
    (``CYCLIC_MESSAGES_LIVE_PADDLE_MODEL``), never by parallelising this.

    The engine is built here, before the first frame, for the reason
    :func:`cyclic_text._resolve_engine` builds one before decoding a clip: once
    the loop is running an engine failure is caught per frame, and a pass of
    "could not read" on every frame is indistinguishable from a strip that
    showed nothing. Failing to build it drops this pass's readings rather than
    the run -- a run whose captions cannot be read still screenshots every one
    of them, which is what this feature did before it could read at all. The
    model itself is cached at the process level (``utils/paddle_ocr``), so
    calling this again for a run's second win does not rebuild it.
    """
    if not run.pending_reads:
        return
    if not settings.CYCLIC_MESSAGES_LIVE_READ:
        run.pending_reads.clear()
        return

    # Imported here, not at module scope: `cyclic_text` imports this module to
    # find a run's clip, so the pair is a cycle at import time and not at call
    # time. The live reader lives over there because the region, the repair and
    # the confidence floor are that module's, and two answers about what the
    # strip is allowed to say is the one thing worth avoiding here.
    from app.services import cyclic_text

    try:
        reader = await cyclic_text.live_reader(run.game)
    except AppException as exc:
        _note(run, f"Captions are not being read: {exc.message}")
        logger.warning(
            "Cyclic message run %s will not read captions: %s", run.run_id, exc.message
        )
        return

    run.reading_now = True
    try:
        # Taken off the front one at a time rather than snapshotted, so a read
        # cut short -- the run stopping mid-batch -- leaves the frames it never
        # reached on the list for the next caller to finish. Capture has
        # stopped by the time this runs, so nothing is being added behind it.
        while run.pending_reads:
            item = run.pending_reads.pop(0)
            try:
                read = await asyncio.to_thread(reader.read, item.path, item.regions)
            except Exception as exc:  # noqa: BLE001 - recorded on the frame below
                message = f"{type(exc).__name__}: {exc}"
                _attach(
                    run,
                    item.sequence,
                    # Recorded on the frame rather than dropped: a caption that
                    # could not be read is a different fact from a strip that
                    # said nothing, and only one of them is worth investigating.
                    # One entry per line, so a frame that failed still has the
                    # shape a frame that succeeded has.
                    [
                        CyclicFrameReading(
                            text="",
                            confidence=0.0,
                            reliable=False,
                            engine=cyclic_text.PADDLE,
                            region=name,
                            error=message,
                        )
                        for name in item.regions
                    ],
                )
                _note(run, f"caption reading: {message}")
                logger.warning("Could not read the caption on %s: %s", item.path, exc)
            else:
                _attach(
                    run,
                    item.sequence,
                    [
                        CyclicFrameReading(
                            text=line.text,
                            repaired=line.repaired,
                            confidence=line.confidence,
                            reliable=line.reliable,
                            engine=cyclic_text.PADDLE,
                            region=line.region,
                            # The clip reader's own rule, not a second one: the
                            # stills and the video are two readings of one pass
                            # and must not disagree about what the strip said.
                            key=cyclic_text.caption_key(line.repaired),
                            crop=line.crop_name,
                            read_ms=line.read_ms,
                        )
                        for line in read
                    ],
                )
            run.read_count += 1
            _touch(run)
    finally:
        run.reading_now = False
        # Unconditional rather than throttled: a caller polling right after a
        # pass closes should see every one of its readings, not wait out
        # whatever was left of _touch's own interval.
        _write_manifest(run, _detail(run, status=CyclicRunState.RUNNING, stopped=None))


# --- sampling the silent window -------------------------------------------


def _finishing(run: _ActiveRun) -> bool:
    """Read :attr:`_ActiveRun.finishing` through a call, for the reason
    :func:`_sampling_stopped` gives: checked once and then again after an
    ``await``, the second read is narrowed away as unreachable even though the
    run is sealed from outside the coroutine in between."""
    return run.finishing


def _sampling_stopped(run: _ActiveRun) -> bool:
    """Read :attr:`_ActiveRun.stop_sampling` through a call rather than inline.

    Purely to keep mypy honest: read as ``run.stop_sampling`` directly at two
    points either side of an ``await``, it narrows the attribute to ``False``
    after the first check (nothing *in this function* reassigns it) and calls
    the second one unreachable -- even though :func:`_stop_sampler` sets it
    from outside this coroutine while it is suspended at that ``await``, which
    is the whole point of checking twice. A function call is a fresh
    expression each time, so nothing carries over.
    """
    return run.stop_sampling


async def _sampler(run: _ActiveRun) -> None:
    """Screenshot whatever the strip is showing, on a timer, until it stops.

    Two windows use this and they are bracketed by different lines. A win
    presentation runs ``cyclic-game-pays`` to
    ``cyclic-line-pays-cycle-finished``; a losing spin runs ``cyclic-no-pay``
    to ``cyclic-idle-strip-ended``. Either way :func:`_stop_sampler` ends it
    the moment the log says so, and the interval decides how *often* a frame is
    taken and never how many.

    **Every knob it reads is on the run, re-read once a frame, and that is the
    point rather than an accident.** A third window exists -- the two above,
    run together, when the win is taken before its line messages have been
    round once -- and it is not a third *pass*: the strip on screen runs
    straight on from the line messages into the between-spins ones with no
    break, so the loop must too. Stopping one sampler and starting another
    there is a hole in the frames at precisely the moment worth catching, so
    :func:`_retune` changes the knobs under the running loop instead. Read as
    parameters they were fixed for the window's lifetime, which is what made
    that impossible.

    :attr:`_ActiveRun.pass_deadline` is per window rather than one setting,
    because the two fail differently when their closing line never comes: a win
    pass that overruns is still a win pass and worth following for a long time,
    while an idle strip that overruns is a machine nobody is playing and
    screenshotting it for the same two and a half minutes would fill a run
    directory with the same three captions.

    :attr:`_ActiveRun.pass_watch` ends the window early once the strip has
    shown everything it has and come back round, which is the ordinary way the
    between-spins window should stop: everything past the first lap is a
    caption already captured. It is decided from the picture, so it costs
    milliseconds a frame rather than the 1.5s an OCR pass would -- see
    :class:`cyclic_text.LoopWatch`. A win presentation has none: its messages
    are a list to get to the end of, not a loop, and the log says when it is
    done.

    :attr:`_ActiveRun.pass_closing_expected` says whether reaching the deadline
    is worth complaining about, and the two windows genuinely differ. The game
    writes ``cyclic-line-pays-cycle-finished`` itself, so a win pass that never
    gets there has something wrong with it and the run should say so. Nothing
    writes the end of an idle strip except the player spinning again --
    measured on FortuneOx, eleven seconds of *completely silent* log between a
    losing spin and the next one -- so that window ending on its deadline is
    the ordinary case, and it repeats the same few captions until it does.
    Reporting it would put an error on almost every losing spin in a run.

    Frames are only written here, never read -- :func:`_read_pending` does that
    once the window closes. That is the whole reason the elapsed figure below
    is wall clock and not ``index * interval``: the loop runs at whatever
    ``asyncio.sleep`` plus an OBS round trip costs, and a frame stamped with
    the time it was *due* would drift from the time it actually landed. It is
    measured from when the window opened and a retune does not reset it: a
    merged window is one stretch of strip, and its frames are timed from the
    moment that stretch began.
    """
    begun = time.monotonic()
    run.sample_frames = 0
    run.sample_rate = 0.0
    index = 0
    # The deadline is the *only* way out of this loop that falls through to the
    # warning below: a cooperative stop returns from inside it. Putting
    # `stop_sampling` in the while condition instead would report every ordinary
    # end of a pass as a pass that never reported finishing.
    while time.monotonic() < run.pass_deadline:
        if _sampling_stopped(run):
            return
        await asyncio.sleep(max(0.0, run.pass_interval))
        if _sampling_stopped(run):
            # Checked again after the sleep, which is where the loop spends
            # nearly all of its time and where a stop nearly always arrives.
            return
        opener = run.pass_opener
        if opener is None:  # pragma: no cover - every window sets one
            return
        index += 1
        elapsed = time.monotonic() - begun
        run.sample_frames = index
        run.sample_rate = index / elapsed if elapsed > 0 else 0.0
        run.position += 1
        saved = await _record(
            run,
            dataclasses.replace(
                opener,
                event=run.pass_event,
                summary=f"Strip on screen {elapsed:.1f}s into the presentation",
                fields={
                    "sample_index": str(index),
                    "elapsed_seconds": f"{elapsed:.1f}",
                },
                # The pass is already running; a frame is wanted now, not later.
                delay_ms=0,
                # **Forced on, not inherited.** A sampled frame exists to be a
                # picture -- that is the entire reason this loop runs -- but
                # `replace` copies the opener's own flag, and the line that
                # opens a window is often a marker that takes no picture
                # (`cyclic-idle-strip-started`, `cyclic-no-pay`). Inheriting it
                # made every frame of the between-spins strip a marker with no
                # screenshot, so a losing spin recorded a dozen events and the
                # only image on the run was the one `cyclic-game-over` took.
                capture=True,
            ),
            position=run.position,
            source=CyclicEventSource.SAMPLED,
            at=datetime.now(),
        )
        # Off the loop's own thread: opening a JPEG is blocking work, and small
        # though it is there is no reason to do it on the event loop that the
        # OBS round trip is also waiting on.
        #
        # Read through the run, like every other knob: a window retuned
        # mid-flight gains a watch it did not open with, and a local bound
        # before the loop would never see it.
        watch = run.pass_watch
        if (
            watch is not None
            and saved is not None
            and await asyncio.to_thread(watch.saw, saved)
        ):
            logger.info(
                "Cyclic message run %s captured %d caption(s) of the strip "
                "and stopped: it has come round",
                run.run_id,
                len(watch.seen),
            )
            return
    if not run.pass_closing_expected:
        # The ordinary way this window ends. The strip loops the same few
        # captions until somebody spins, so no line was ever coming and there
        # is nothing further to see. Logged, not noted -- an entry on
        # `run.errors` here would flag almost every losing spin in a run.
        logger.info(
            "Cyclic message run %s captured the between-spins strip until its "
            "deadline and stopped; it repeats until the next spin",
            run.run_id,
        )
        return

    limit = settings.CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS
    _note(
        run,
        f"capture stopped after {limit:g}s without the line-message pass "
        "reporting that it finished; raise CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS "
        "if a win presentation legitimately runs longer",
    )
    logger.warning(
        "Cyclic message run %s stopped capturing after %.1fs without the "
        "line-message pass reporting that it finished",
        run.run_id,
        limit,
    )


def _tune(
    run: _ActiveRun,
    opener: game_log.DetectedEvent,
    *,
    interval: float,
    max_seconds: float,
    event: str,
    closing_line_expected: bool,
    loop_watch: cyclic_text.LoopWatch | None,
) -> None:
    """Point the capture loop's knobs at one window. See :func:`_sampler` for
    why they are on the run rather than parameters of it."""
    run.pass_opener = opener
    run.pass_interval = max(0.0, interval)
    run.pass_event = event
    run.pass_deadline = time.monotonic() + max(run.pass_interval, max_seconds)
    run.pass_closing_expected = closing_line_expected
    run.pass_watch = loop_watch


def _retune(
    run: _ActiveRun,
    opener: game_log.DetectedEvent,
    *,
    interval: float,
    max_seconds: float,
    event: str,
    closing_line_expected: bool,
    loop_watch: cyclic_text.LoopWatch | None,
) -> None:
    """Change what the open window is capturing, without stopping it.

    The one caller is the win taken early: the game goes idle while the line
    messages are still cycling, and the strip runs straight on from them into
    the between-spins messages. That is one stretch of strip and it wants one
    unbroken run of frames over it -- so nothing here touches
    :attr:`_ActiveRun.sampler`, and the loop picks the new values up on its
    next frame.

    Everything a window decides is reset, the deadline included: the
    between-spins half has its own, and leaving the win's in place would cut
    it short by however long the win had already been running. What is
    deliberately *not* reset is the achieved rate and the elapsed clock the
    frames are stamped from -- one window, one set of figures.
    """
    _tune(
        run,
        opener,
        interval=interval,
        max_seconds=max_seconds,
        event=event,
        closing_line_expected=closing_line_expected,
        loop_watch=loop_watch,
    )
    logger.info(
        "Cyclic message run %s is carrying its open window on into %s with no "
        "break in the frames",
        run.run_id,
        _STRIP_NAMES.get(run.pass_kind, run.pass_kind),
    )


async def _start_sampler(
    run: _ActiveRun,
    opener: game_log.DetectedEvent,
    *,
    interval: float,
    max_seconds: float,
    event: str,
    closing_line_expected: bool,
    loop_watch: cyclic_text.LoopWatch | None = None,
) -> None:
    """Begin capturing whatever the strip is about to show, replacing any window
    still running -- two spins close together should not leave two samplers on
    one run."""
    if not settings.CYCLIC_MESSAGES_SAMPLE_LINE_PAYS or run.finishing:
        return
    # Asked, and deliberately not waited for. A recovery reading a clip back
    # is the one thing on a run that can be running when a window opens, and
    # capturing every frame on schedule is the only priority while one is -- so
    # it gives way after the frame it is on, and this does not stand around for
    # even that. Its queue survives to be finished at the next quiet moment.
    _ask_recovery_to_stop(run)
    await _stop_sampler(run)
    run.stop_sampling = False
    _tune(
        run,
        opener,
        interval=interval,
        max_seconds=max_seconds,
        event=event,
        closing_line_expected=closing_line_expected,
        loop_watch=loop_watch,
    )
    run.sampler = asyncio.create_task(
        _window(run), name=f"cyclic-messages-sampler-{run.run_id}"
    )


async def _window(run: _ActiveRun) -> None:
    """Capture a window, and close it if it ends on its own terms.

    The capture loop can finish two ways and they need opposite handling. A log
    line ending it -- the line messages finishing, the next spin clearing the
    strip -- is :func:`_stop_sampler` asking, and the handler that asked is the
    one that closes the window and reads its frames. Everything else is the
    loop deciding for itself: the between-spins strip coming round, or the
    deadline. Nothing is waiting on those, so if this did not close the window
    here it would stay open with its frames unread until the *next* spin
    happened to close it -- which is exactly what a strip that had already
    shown everything it has looked like: stuck on "capturing", nothing read.
    """
    await _sampler(run)
    if run.stop_sampling:
        # Asked to stop: the caller owns the close, and is most likely awaiting
        # this very task inside `_stop_sampler` right now.
        return
    run.pass_open = False
    run.pass_kind = ""
    # Stopped before the reading, which can take a minute: leaving OBS
    # recording through it would put that minute in the file.
    await _close_clip(run, closed_by=_STRIP_LOOPED)
    await _read_pending(run)
    # The ordinary end of a between-spins window, and the quietest moment a run
    # has: the strip has shown everything it has and the next spin is at human
    # speed. Which is exactly when the clip it just filed should be read back.
    await _start_recovery(run)


async def _stop_sampler(run: _ActiveRun) -> None:
    """End the capture loop, cooperatively, with a cancel behind it.

    Asked rather than cancelled, the way ``services/analyze_spin`` ends its
    own waits and for a reason particular to this one: the loop is nearly
    always inside :func:`_record`, which takes a sequence number before it
    awaits its screenshot. Cancelled there, the number is spent and no event
    ever carries it -- a hole in a numbering whose whole job is to have none.
    Asked, the loop finishes the frame it is on and returns through its own
    code.

    The cancel behind it is for the screenshot OBS never answers, which must
    not hold a Stop press open indefinitely. ``wait_for`` cancels *and awaits*
    the gather itself on timeout, so nothing is left pending either way --
    which under ``filterwarnings = error`` is the difference between a slow
    stop and a failing suite. Awaiting is not optional in either branch.
    """
    task, run.sampler = run.sampler, None
    if task is None or task.done():
        return
    run.stop_sampling = True
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(
            asyncio.gather(task, return_exceptions=True),
            timeout=_SAMPLER_STOP_SECONDS,
        )


def _live_on(run: _ActiveRun, cycle: int, *, kind: str, continues: int | None) -> None:
    """Point the live view at a window, or add one to what it already shows.

    ``continues`` is the sequence this window is the rest of, when it is the
    rest of one -- the win whose between-spins strip this is. Given one, the
    card goes on showing that win's frames and files this window's in beside
    them; given ``None``, the card starts again.

    Both are needed and picking either for both cases is wrong. A run captures
    every spin, so a view that never reset would grow without bound and show a
    tester the spin before last. But a *spin* is up to two windows -- the win
    paying out, then the strip after it has been taken -- and those two are one
    thing a tester is looking at: resetting between them emptied the card of
    the win that had just been captured, at the exact moment the tester pressed
    the button that ended it.
    """
    if continues is not None and continues in run.live_cycles:
        run.live_cycles.append(cycle)
        # Named for what the card is now showing, which is both of them. The
        # frames of either half are still stamped with their own strip's bands
        # -- see :attr:`_Pending.regions` -- so this is a caption for the view
        # and never an input to a reading.
        run.live_kind = WIN_THEN_IDLE
        return
    run.live_cycles = [cycle]
    run.live_kind = kind


async def _carry_on(run: _ActiveRun, detected: game_log.DetectedEvent) -> None:
    """Run the open win window straight on into the between-spins strip.

    The win taken before its line messages have been round once. On screen
    there is no break: the strip carries on from the line messages into "GAME
    OVER / GAME PAYS n / PLAY 880 CREDITS", and the only thing marking the
    boundary is this log line. So the window carries on with it -- the same
    sampler, the same clip, the same sequence -- and everything that differs
    between the two strips is changed underneath it.

    **What this replaces, and why.** This used to stop the sampler, close the
    window, file its clip and open a fresh window of everything: it cost the
    end of the line messages (the frames between the last one captured and the
    stop, which is exactly where the messages a short win has not shown yet
    are), it cost an OBS round trip's worth of strip while the new recording
    started, and it moved the live view onto the new sequence, which emptied
    the card of every frame of the win. Three separate symptoms of one thing:
    the strip did not stop, so the window should not have.

    Three things change and nothing else does:

    * **The bands.** The win presentation draws one line and the between-spins
      strip draws two (CYCLIC_MESSAGES_IDLE_REGIONS), so every frame from here
      on is stamped for both -- which is what makes the second line readable
      at all. Frames already taken keep the single band they were taken for;
      the second one was empty then.
    * **The clip.** It goes on recording, and becomes a clip of both strips
      (:data:`WIN_THEN_IDLE`) so that reading it back crops both bands. One
      file rather than two here *only*: the ordinary case -- a win left to
      finish, taken afterwards -- still records a clip each, because there the
      two really are separate windows with a gap between them.
    * **The window's own terms.** Interval and sampled-event name become the
      between-spins strip's, because that is what is on screen now. Not the
      elapsed clock and not the achieved rate: it is one window and they are
      its figures. And **not the deadline and not the lap watch** -- see
      below, which is the thing here most easily got wrong.

    :attr:`_ActiveRun.pass_kind` becoming :data:`WIN_THEN_IDLE` is also what
    keeps ``cyclic-line-pays-cycle-finished`` -- which lands *after* this line
    when a win is taken early -- from closing the window it names. It is
    recorded, with a frame, as the moment on the strip that it is.

    **Both strips are now running at once, so this window still ends on the
    win's terms and not on the between-spins strip's.** The top band goes on
    cycling "LINE 4 PAYS 15", "LINE 5 PAYS 15" for however many lines paid --
    minutes, on a 40-line win -- while the smaller band beneath it laps its
    three captions every six seconds. Retuning onto the between-spins
    deadline and arming its lap watch here therefore ended the window on that
    six-second lap, with the line messages still going and
    ``cyclic-line-pays-cycle-finished`` still more than a minute away: on the
    run this was found on, the strip ran for another 69 seconds after the last
    frame, uncaptured and unrecorded, and the closing line arrived to a window
    that had already been read and filed. So the deadline stays the win's own
    (what is left of it -- this is the same window, not a new one), the
    closing line is still expected, and the lap watch is **not** armed until
    that line lands and the top band really has finished. Arming it there is
    :func:`_watch_for_the_lap`.
    """
    run.pass_kind = WIN_THEN_IDLE
    run.pass_regions = settings.cyclic_messages_idle_regions
    # The win's own sequence carries on, so nothing moves on the card and the
    # frames from here simply keep arriving after the ones already on it.
    run.win_cycle = None
    run.live_kind = WIN_THEN_IDLE
    if run.clip is not None:
        # Mutated rather than reopened: stopping and starting OBS here is the
        # gap in the recording this function exists to avoid, and the kind is
        # only ever read when the clip is filed and read back.
        run.clip.kind = WIN_THEN_IDLE
    _retune(
        run,
        detected,
        interval=settings.CYCLIC_MESSAGES_IDLE_INTERVAL_SECONDS,
        # What is left of the win's deadline, not a fresh one of either kind.
        # The window is unchanged in the only respect a deadline is about --
        # it is still waiting for `cyclic-line-pays-cycle-finished`, from the
        # same `cyclic-game-pays` it always was.
        max_seconds=max(0.0, run.pass_deadline - time.monotonic()),
        event=_IDLE_MESSAGE_SHOWN,
        # Both of these are the win's still, and for the one reason: the line
        # messages have not finished. Their closing line is still coming, and
        # the between-spins strip lapping underneath them is not evidence
        # there is nothing left to capture -- it laps every six seconds while
        # they run on for as long as the win takes to pay.
        loop_watch=None,
        closing_line_expected=True,
    )
    await _record(run, detected, position=None)


def _watch_for_the_lap(run: _ActiveRun, detected: game_log.DetectedEvent) -> None:
    """Put a carried-on window onto the between-spins strip's own terms, now
    that the line messages running over it have reported finishing.

    The second half of :func:`_carry_on`, and the reason that one leaves the
    deadline and the lap watch alone. Until this line lands, two strips are on
    screen at once and only the top one knows when it is done; from here the
    between-spins strip is all that is left, so it gets the deadline and the
    lap watch that ordinarily end its window -- the lap counted from here,
    since the captions it showed while the line messages were still cycling
    have been captured already.

    Still one window and still one clip: nothing about the sampler, the
    sequence or the recording changes, exactly as at the carry-on.
    """
    _retune(
        run,
        detected,
        interval=settings.CYCLIC_MESSAGES_IDLE_INTERVAL_SECONDS,
        max_seconds=settings.CYCLIC_MESSAGES_IDLE_MAX_SECONDS,
        event=_IDLE_MESSAGE_SHOWN,
        loop_watch=_loop_watch(run),
        # Nothing writes the end of a between-spins strip; it stops when the
        # player spins again. The deadline is how it ordinarily ends.
        closing_line_expected=False,
    )


def _is_repeat(run: _ActiveRun, detected: game_log.DetectedEvent) -> bool:
    """Whether this event is the same one already recorded moments ago, by the
    game's own timestamps -- the publish line and the transition it caused are
    one event logged twice. A line with no usable timestamp is always let through."""
    at = detected.line.timestamp
    if at is None:
        return False
    previous = run.last_seen.get(detected.event)
    run.last_seen[detected.event] = at
    if previous is None:
        return False
    gap = (at - previous).total_seconds()
    return 0 <= gap < settings.CYCLIC_MESSAGES_DEBOUNCE_SECONDS


async def _handle(run: _ActiveRun, raw: str) -> None:
    """Capture one appended line, if a cyclic rule claims it.

    This is where a sequence is assembled, and the two families are assembled
    differently. For the idle strip a start is held until a message proves the
    loop real, messages carry their position, and a completion counts only if
    the loop showed something. For the win strip the amount opens a new
    sequence, the rack-up ending starts the sampler, and the pass finishing
    stops it -- so the sampler's lifetime is exactly the window the log brackets.
    """
    try:
        line = game_log.parse_line(raw)
        if line is None:
            return

        # Not an event: the game states its denomination on its own line, and
        # this is the only thing that turns logged cents into shown credits.
        paytable = game_log.PAYTABLE_LOADED.search(line.message)
        if paytable is not None:
            run.denomination = (
                _as_denomination(paytable.groupdict().get("denom")) or run.denomination
            )

        detected = game_log.match(line, game_log.CYCLIC_MESSAGE_RULES)
        if detected is None or _is_repeat(run, detected):
            return

        if detected.event == _CYCLE_STARTED:
            # Held, not recorded: most armed sequences never display anything.
            run.pending_start = detected
            run.position = 0
            return

        if detected.event == _MESSAGE_EVENT:
            pending, run.pending_start = run.pending_start, None
            if pending is not None:
                run.cycle += 1
                await _record(run, pending, position=None)
            elif run.cycle == 0:
                # Tracking began mid-loop; the run's first loop is still a loop.
                run.cycle = 1
            run.position += 1
            await _record(run, detected, position=run.position)
            return

        if detected.event == _CYCLE_COMPLETED:
            # A sequence that ended without showing anything is not a loop.
            if run.position > 0:
                run.cycle_count += 1
                await _record(run, detected, position=None)
            run.pending_start = None
            run.position = 0
            return

        if detected.event == _NO_PAY:
            # This spin paid nothing, so there is no banner and no line
            # messages. Counted and recorded as a marker, and nothing more --
            # the strip it leaves behind is the *between-spins* one, which
            # opens a couple of hundred milliseconds later on
            # `cyclic-idle-strip-started` below. That line is where the window
            # belongs because a won spin reaches it too, once its win has been
            # taken, and the two show the same strip.
            run.no_pay_count += 1
            run.no_pay_since_win += 1
            # This spin has no win to continue, so the strip it is about to
            # show is a window of its own rather than the second half of one.
            run.win_cycle = None
            if run.cycle == 0:
                run.cycle = 1
            await _record(run, detected, position=None)
            return

        if detected.event == _IDLE_STRIP_STARTED:
            # The spin is over and the game is sitting idle, which is when the
            # between-spins strip runs: "GAME OVER", "GAME PAYS n", "PLAY 880
            # CREDITS", round and round until somebody spins again. **Both of
            # the ways a spin can end arrive here** -- a loss within a few
            # hundred milliseconds of its result, a win only once it has been
            # taken -- so this one line covers both.
            #
            # Nothing logs the strip's messages, exactly as nothing logs the
            # line messages, so they are sampled. Two lines, not one: see
            # CYCLIC_MESSAGES_IDLE_REGIONS.
            #
            # Which of *three* things this is depends on what came before it,
            # and the three are handled below in the order they read:
            #
            # 1. A win still being captured -- the win was taken before its
            #    line messages had been round once. The strip does not stop
            #    and neither does the window: :func:`_carry_on`.
            # 2. A win already finished -- the ordinary case, the win taken
            #    after its line messages completed. A window of its own, but
            #    the same spin, so the live view keeps the win's frames and
            #    this one's are appended to them.
            # 3. Anything else -- a losing spin, or a strip that has already
            #    been round. A spin of its own, and the live view starts again.
            run.pending_start = None
            if run.pass_open and run.pass_kind == WIN_VIDEO:
                await _carry_on(run, detected)
                return

            # The win this strip belongs to, if the run has one still waiting
            # for it. Read before the cycle moves, and cleared either way: a
            # win's between-spins strip runs once.
            continues, run.win_cycle = run.win_cycle, None
            run.cycle += 1
            run.position = 0
            # Appended rather than replacing when this is the second half of a
            # spin whose first half is already on the card. That card is what a
            # tester is watching while they press Take Win, and swapping it for
            # an empty one at that exact moment is what made the win's frames
            # look like they had been thrown away -- they had not; they were
            # filed under a sequence the view had just stopped showing.
            _live_on(run, run.cycle, kind=IDLE_VIDEO, continues=continues)
            run.pass_regions = settings.cyclic_messages_idle_regions
            run.pass_kind = IDLE_VIDEO
            run.pass_open = True
            # After the cycle is settled and before the first frame: the
            # between-spins strip's first message was captured by the log line
            # before this one. See :func:`_adopt`.
            _adopt(run)
            # Before the first frame, so the clip covers the strip from its
            # first message rather than joining it part-way.
            await _open_clip(run, kind=IDLE_VIDEO, replaces=_IDLE_STRIP_STARTED)
            await _record(run, detected, position=None)
            await _start_sampler(
                run,
                detected,
                interval=settings.CYCLIC_MESSAGES_IDLE_INTERVAL_SECONDS,
                max_seconds=settings.CYCLIC_MESSAGES_IDLE_MAX_SECONDS,
                event=_IDLE_MESSAGE_SHOWN,
                loop_watch=_loop_watch(run),
                # Nothing will write the end of this one: it stops when the
                # player spins again, which may be minutes away or never. The
                # deadline is how it ordinarily ends, not a fault.
                closing_line_expected=False,
            )
            return

        if detected.event == _IDLE_STRIP_ENDED:
            # The player has bet or spun again, so the between-spins strip is
            # gone. Only closes that strip's own window: the line fires
            # whenever the game leaves idle, including for reasons that have
            # nothing to do with a window being open.
            if not run.pass_open or run.pass_kind not in (
                IDLE_VIDEO,
                WIN_THEN_IDLE,
            ):
                return
            await _stop_sampler(run)
            run.pass_open = False
            run.pass_kind = ""
            run.win_cycle = None
            run.cycle_count += 1
            await _record(run, detected, position=None)
            await _close_clip(run, closed_by=_IDLE_STRIP_ENDED)
            await _read_pending(run)
            # Every clip filed since the last quiet moment -- this window's own
            # and, after a taken win, the presentation before it -- read back in
            # the background. Both need it: the stills of either strip miss
            # captions, for the same reason and to a similar degree.
            await _start_recovery(run)
            return

        if detected.event == _GAME_PAYS:
            # A win presentation is its own cyclic sequence: it opens here, on
            # the one line that says what the strip is about to display.
            run.pending_start = None
            run.no_pay_since_win = 0
            run.cycle += 1
            run.position = 1
            # The live view follows this sequence until the next win opens one,
            # rather than following `cycle` on into the attract loops after it.
            _live_on(run, run.cycle, kind=WIN_VIDEO, continues=None)
            # And the between-spins strip that follows this win, whenever the
            # player takes it, is the rest of this same spin. See
            # :attr:`_ActiveRun.win_cycle`.
            run.win_cycle = run.cycle
            run.pass_regions = settings.cyclic_messages_text_regions
            run.pass_kind = WIN_VIDEO
            run.pass_open = True
            _adopt(run)
            # Recording starts before the frame is taken, so the GAME PAYS
            # banner this event screenshots is inside the video as well.
            await _open_clip(run)
            # This frame first, then the loop -- not the other way round, even
            # though capture "starting on cyclic-game-pays" would read as the
            # loop going first. Two reasons. The banner frame is the pass's
            # first frame and the only one with a log line behind it, and a
            # loop started ahead of it would have the two competing for the
            # same OBS socket with the *unlogged* one likely to win. And the
            # watcher is cancelled where it stands when a run stops, so an
            # event still waiting on its screenshot is an event lost -- worth
            # nothing for a sampled frame and worth the whole amount here.
            #
            # It costs the rule's own 500ms settle plus one round trip before
            # the loop begins, which is inside the 1.5-7s a screenshot takes on
            # this machine anyway.
            await _record(run, _pays_event(run, detected), position=run.position)
            # Capture opens *here*, on the amount, rather than on the rack-up
            # ending below. The strip is already showing something the moment
            # the game says what it pays -- the banner, then the count-up, then
            # the line messages -- and the frames of that first stretch are
            # exactly as much a record of the presentation as the later ones.
            # Waiting for `cyclic-win-presented` skipped them.
            await _start_sampler(
                run,
                detected,
                interval=settings.CYCLIC_MESSAGES_SAMPLE_INTERVAL_SECONDS,
                max_seconds=settings.CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS,
                event=_LINE_PAYS_SHOWN,
                closing_line_expected=True,
            )
            return

        if detected.event == _WIN_PRESENTED:
            # The meter has finished counting, so the line messages start now --
            # and nothing further is logged until the pass ends. Capture is
            # already running (it opened on the amount); this only marks the
            # boundary, and restarting the sampler here would reset the pass's
            # own elapsed clock part-way through it.
            if run.cycle == 0:
                run.cycle = 1
            if not run.pass_open:
                # No window open, so `cyclic-game-pays` never arrived -- either
                # tracking began mid-presentation or the amount went unlogged.
                # This is the first boundary there is, so the window opens on
                # it instead. Keyed on the window rather than on "have we seen
                # a win yet": the second win of a run whose amount was missed
                # needs capturing exactly as much as the first.
                _live_on(run, run.cycle, kind=WIN_VIDEO, continues=None)
                run.win_cycle = run.cycle
                run.pass_regions = settings.cyclic_messages_text_regions
                run.pass_kind = WIN_VIDEO
                run.pass_open = True
                _adopt(run)
                await _start_sampler(
                    run,
                    detected,
                    interval=settings.CYCLIC_MESSAGES_SAMPLE_INTERVAL_SECONDS,
                    max_seconds=settings.CYCLIC_MESSAGES_SAMPLE_MAX_SECONDS,
                    event=_LINE_PAYS_SHOWN,
                    closing_line_expected=True,
                )
            run.position += 1
            await _record(run, detected, position=run.position)
            return

        if detected.event == _LINE_PAYS_CYCLE_DONE:
            # Only closes a window that is still purely the *win* one.
            # Taking the win before the line messages have been round once
            # makes the game go idle first, and this line then lands with the
            # window already carried on into the between-spins strip
            # (:data:`WIN_THEN_IDLE`) or with a separate between-spins window
            # already open -- where closing "the current window" ended that
            # strip a second after it started and lost everything it had to
            # say. Recorded either way: the line is real, it is a moment on
            # the strip, and it belongs on the timeline with a frame of its own.
            if run.pass_open and run.pass_kind == WIN_THEN_IDLE:
                # A carried-on window, and this is the half of it that knew
                # when it was finished. The line messages are done; the strip
                # beneath them is not, and neither is the window. So the pass
                # is counted and framed like the win pass it half was, and
                # then handed over to the between-spins strip's own deadline
                # and lap watch -- which :func:`_carry_on` deliberately did
                # not arm, because until this line arrived they would have
                # ended the window on the small band's six-second lap with
                # the line messages still running.
                run.cycle_count += 1
                run.position += 1
                await _record(run, detected, position=run.position)
                _watch_for_the_lap(run, detected)
                return
            if run.pass_kind != WIN_VIDEO:
                await _record(run, detected, position=None)
                return
            # Stop first: the pass is over, and a frame taken after this line
            # would be of the next pass rather than of the one being recorded.
            await _stop_sampler(run)
            run.cycle_count += 1
            run.position += 1
            # `pass_open` outlives the sampler by one frame on purpose: this
            # event's own screenshot is the last line message, and it is read
            # like every frame before it.
            await _record(run, detected, position=run.position)
            run.pass_open = False
            run.pass_kind = ""
            # Closed after that frame rather than before it: this line is the
            # end of the pass, so the last line message belongs in the video.
            await _close_clip(run, closed_by=_LINE_PAYS_CYCLE_DONE)
            # Reading is last: capturing this pass and filing its clip are both
            # quick and both matter to the next event that might already be
            # waiting in the log; reading is neither, and only starts once
            # there is truly nothing left to capture.
            await _read_pending(run)
            await _start_recovery(run)
            return

        if detected.event == _RESULTS_CYCLE_STOPPED:
            # The strip has stopped cycling results, so any pass still being
            # captured or recorded has been cut short by whatever stopped it.
            await _stop_sampler(run)
            run.pass_open = False
            run.pass_kind = ""
            await _close_clip(run, closed_by=_RESULTS_CYCLE_STOPPED)
            # Whatever stopped the strip ended this spin's presentation; the
            # next `cyclic-idle-strip-started` is not the rest of it.
            run.win_cycle = None
            await _record(run, detected, position=None)
            run.position = 0
            await _read_pending(run)
            await _start_recovery(run)
            return

        # Anything else the rule set recognises -- 'cyclic-game-over' and the
        # rack-up marker -- is a message in the sequence that is current.
        if run.cycle == 0:
            run.cycle = 1
        position: int | None = None
        if detected.capture:
            run.position += 1
            position = run.position
        await _record(run, detected, position=position)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if message not in run.errors:
            run.errors.append(message)
        logger.exception("Cyclic message run %s hit an error", run.run_id)


async def _watch(run: _ActiveRun) -> None:
    """Follow the log until cancelled."""
    logger.info("Cyclic message run %s following %s", run.run_id, run.log.path)
    await run.log.follow(lambda raw: _handle(run, raw))


async def _stop_watching(run: _ActiveRun) -> None:
    """End the watcher task. Awaiting the cancellation is not optional -- an
    unawaited one is the pending-task warning that fails the test suite."""
    task = run.task
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _finish(run: _ActiveRun, *, status: CyclicRunState) -> CyclicRunDetail:
    """Stop the watcher, take one last look, end any clip, seal the manifest."""
    await _stop_watching(run)

    # The watcher may have been cancelled part-way through filing a clip, which
    # goes on without it. Waited for here so the record this returns has it.
    await _await_closing(run)

    # Both before and after the catch-up read: before, because a pass still
    # being sampled ends with the run; after, because the flag is what stops
    # the replayed lines opening a new one.
    run.finishing = True
    await _stop_sampler(run)
    # Before the catch-up read below rather than after: the drain is the one
    # thing that can still be running here, and the final read should have the
    # machine to itself for the same reason every other read does.
    await _stop_recovery(run)

    # Events logged between the last poll and the stop request still count.
    for raw in run.log.new_lines():
        await _handle(run, raw)

    await _stop_sampler(run)
    run.pass_open = False
    # A presentation still being recorded ends with the run; the clip says so
    # rather than being dropped, because a short video is still the win.
    await _close_clip(run, closed_by=_RUN_STOPPED)

    # Whatever the last pass left uncaptured is read now, exactly as it would
    # be if the pass had closed on its own -- capturing has stopped either way,
    # so there is nothing left for this to compete with.
    await _read_pending(run)

    # Deliberately *not* noted on `errors`, and deliberately not drained here
    # either. A queue of long clips is minutes of OCR and Stop should stop, so
    # a sealed run leaving clips unread is the ordinary way a run ends rather
    # than a fault -- and `errors` is rendered as the run's problems. Saying
    # it there put a red line on almost every run to tell a reader something
    # the "Read the messages" button beside each clip already offers. The
    # queue itself is still reported: see `CyclicStatus.recovery_pending`.
    if run.pending_recovery:
        logger.info(
            "Cyclic message run %s stopped with %d clip(s) still unread; "
            "'Read the messages' on each reads it back",
            run.run_id,
            len(run.pending_recovery),
        )

    detail = _detail(run, status=status, stopped=datetime.now())
    _write_manifest(run, detail)
    logger.info(
        "Cyclic message run %s %s with %d messages over %d loops, %d clips and "
        "%d captions read",
        run.run_id,
        status.value,
        detail.message_count,
        detail.cycle_count,
        len(detail.videos),
        run.read_count,
    )
    return detail


# --- public API -----------------------------------------------------------


async def start() -> CyclicStatus:
    """Begin a run against the active game's log. OBS is connected first and
    allowed to fail the request -- a run that can't screenshot is just text."""
    global _run
    async with _get_lock():
        if _run is not None:
            raise CyclicMessagesAlreadyRunningError(
                f"Cyclic message run {_run.run_id} is already in progress; "
                "stop it first"
            )

        name, config = _active_config()
        log_path = _log_for(name, config)

        await obs_service.connect()
        # Worth trying, not worth failing for -- the scene may already be correct.
        with contextlib.suppress(AppException):
            await obs_service.select_current_game_window()

        started = datetime.now()
        run_id = started.strftime(_RUN_ID_FORMAT)
        output_dir = f"{settings.CYCLIC_MESSAGES_DIR_NAME}/{run_id}"
        directory = resolve_subdirectory(settings.obs_screenshot_dir, output_dir)
        directory.mkdir(parents=True, exist_ok=True)

        run = _ActiveRun(
            run_id=run_id,
            game=name,
            directory=directory,
            output_dir=output_dir,
            # Opened here so the run starts from the log's end, not its history.
            log=LogFollower(
                log_path, poll_seconds=settings.CYCLIC_MESSAGES_POLL_SECONDS
            ),
            started_at=started,
            # Read backwards now rather than waited for: the game states its
            # denomination only when one changes, so a run against an
            # already-running game would report cents until the player did.
            denomination=_read_denomination(log_path),
        )

        # Deliberately not recording yet: a clip is a win presentation, and
        # this run has not seen one. See the module docstring.
        _write_manifest(run, _detail(run, status=CyclicRunState.RUNNING, stopped=None))
        run.last_write = time.monotonic()
        run.task = asyncio.create_task(_watch(run), name=f"cyclic-messages-{run_id}")
        # Nothing else to start: reading is not a standing worker any more, and
        # PaddleOCR's model is built the first time a pass actually closes --
        # see :func:`_read_pending`. That means the first pass of a run pays
        # the model's build time before its readings appear; every pass after
        # it (this run's or the next one's) reuses the process-level cache.
        _run = run

    return status()


async def stop() -> CyclicRunDetail:
    """End the run and return its finished record."""
    global _run
    async with _get_lock():
        run = _run
        if run is None:
            raise CyclicMessagesNotRunningError(
                "No cyclic message run is in progress, so there is nothing to stop"
            )
        _run = None

    return await _finish(run, status=CyclicRunState.COMPLETED)


def status() -> CyclicStatus:
    """Report the run in progress, if any. Never fails."""
    run = _run
    if run is None:
        return CyclicStatus(active=False)

    elapsed = datetime.now() - run.started_at
    recent = settings.CYCLIC_MESSAGES_RECENT_EVENTS
    return CyclicStatus(
        active=True,
        run_id=run.run_id,
        game=run.game,
        started_at=run.started_at,
        duration_ms=max(0, int(elapsed.total_seconds() * 1000)),
        message_count=_message_count(run),
        sampled_count=run.sampled_count,
        cycle_count=run.cycle_count,
        recording=_is_recording(run),
        video_count=len(run.videos),
        sampling=run.sampler is not None and not run.sampler.done(),
        reading=run.reading_now,
        recovering=run.recovering is not None and not run.recovering.done(),
        recovery_pending=len(run.pending_recovery),
        recovered_count=run.recovered_count,
        no_pay_count=run.no_pay_count,
        queue_depth=len(run.pending_reads),
        read_count=run.read_count,
        sample_rate=run.sample_rate,
        recent_events=sorted(run.events, key=lambda event: event.sequence)[-recent:],
        errors=list(run.errors),
    )


def live() -> CyclicLiveView:
    """The win presentation being captured right now, frame by frame.

    Read off the live run rather than off its manifest, which is the point of
    it: :func:`_touch` flushes at most once a second, and a frame should be on
    screen as soon as OBS has written it. Never fails -- like :func:`status`,
    an idle backend is an answer and not an error.

    Scoped to :attr:`_ActiveRun.live_cycles`, so the frames stay put once a
    pass ends instead of being replaced by the attract loop that follows it --
    and so a spin captured as two windows is still shown as one spin. See
    :func:`_live_on`: taking a win after its line messages have finished opens
    a sequence of its own, and the card grows by it rather than being emptied
    and started again.

    ``cycle`` reports the first of them, which is the one the view is named
    for: the win, when there is a win.
    """
    run = _run
    if run is None:
        return CyclicLiveView(active=False)

    cycles = set(run.live_cycles)
    frames = sorted(
        (event for event in run.events if event.captured and event.cycle in cycles),
        key=lambda event: event.sequence,
    )
    return CyclicLiveView(
        active=True,
        run_id=run.run_id,
        game=run.game,
        cycle=run.live_cycles[0] if run.live_cycles else None,
        strip=run.live_kind or None,
        capturing=run.pass_open,
        reading=run.reading_now,
        recovering=run.recovering is not None and not run.recovering.done(),
        recovery_pending=len(run.pending_recovery),
        spins_without_pay=run.no_pay_since_win,
        queue_depth=len(run.pending_reads),
        read_count=run.read_count,
        sample_rate=run.sample_rate,
        sample_interval_seconds=run.pass_interval,
        frame_count=len(frames),
        # The newest, not the oldest: a pass longer than the cap is one whose
        # last frames are the ones still being read.
        frames=frames[-settings.CYCLIC_MESSAGES_LIVE_FRAMES :],
        errors=list(run.errors),
    )


def list_runs() -> list[CyclicRunSummary]:
    """Every run on disk, newest first -- run ids are timestamps, so sorting by
    name is sorting by time."""
    root = _capture_root()
    if not root.is_dir():
        return []

    summaries: list[CyclicRunSummary] = []
    for directory in sorted(root.iterdir(), key=lambda item: item.name, reverse=True):
        if not directory.is_dir():
            continue
        detail = _read_manifest(directory / MANIFEST_NAME)
        if detail is not None:
            summaries.append(CyclicRunSummary.model_validate(detail.model_dump()))
    return summaries


def _run_directory(run_id: str) -> Path:
    """Resolve one run's directory from an id that came off the wire."""
    try:
        directory = resolve_within(_capture_root(), run_id)
    except UnsafeNameError as exc:
        raise CyclicMessagesRunNotFoundError(
            f"Invalid cyclic message run id: {exc.reason}"
        ) from exc
    if not directory.is_dir():
        raise CyclicMessagesRunNotFoundError(f"No cyclic message run named {run_id!r}")
    return directory


def get_run(run_id: str) -> CyclicRunDetail:
    """One run, with all of its events."""
    detail = _read_manifest(_run_directory(run_id) / MANIFEST_NAME)
    if detail is None:
        raise CyclicMessagesRunNotFoundError(
            f"The record for cyclic message run {run_id!r} is missing or unreadable"
        )
    return detail


def file_path(run_id: str, file_name: str) -> Path:
    """Resolve one screenshot or video for serving. Both halves come from the
    URL, so both go through the path guards."""
    directory = _run_directory(run_id)
    try:
        target = resolve_within(directory, file_name)
    except UnsafeNameError as exc:
        raise CyclicMessagesRunNotFoundError(
            f"Invalid file name: {exc.reason}"
        ) from exc
    if not target.is_file():
        raise CyclicMessagesRunNotFoundError(
            f"No file named {file_name!r} in cyclic message run {run_id!r}"
        )
    return target


async def abort() -> None:
    """End any run because the process is shutting down. Marks the record
    ``interrupted``, not ``completed`` -- nobody pressed stop. Runs before OBS
    disconnects in the lifespan, so the recording is stopped rather than left on."""
    global _run
    run = _run
    if run is None:
        return
    _run = None
    await _finish(run, status=CyclicRunState.INTERRUPTED)


async def reset() -> None:
    """Drop all state, including the lock bound to this loop. Tests only."""
    global _run, _lock
    run, _run = _run, None
    if run is not None:
        run.finishing = True
        run.pass_open = False
        await _stop_watching(run)
        await _stop_sampler(run)
        await _stop_recovery(run)
        await _stop_guard(run)
        await _await_closing(run)
    _lock = None
