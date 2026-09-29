"""Cyclic message capture payloads; also the shape of the ``run.json`` manifest
written to disk, deliberately shared so the UI and the folder never disagree.

Mirrors :mod:`app.schemas.event_capture` with two additions: an event's
``captured`` flag (loop boundaries take no frame) and the run's ``videos``,
one clip per win presentation rather than one recording of the whole run.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class CyclicRunState(StrEnum):
    """How a run ended, or that it has not."""

    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"  # backend restarted mid-run


class CyclicEventSource(StrEnum):
    """Whether a log line triggered this event, or a clock did.

    Every ``LOG`` event stands on a line the game wrote, quoted in
    :attr:`CyclicEvent.log_line`. A ``SAMPLED`` event does not: those frames
    are taken on a timer between two logged boundaries, and ``log_line``
    quotes the boundary that opened the window rather than the message on
    the frame.
    """

    LOG = "log"
    SAMPLED = "sampled"


class CyclicFrameReading(BaseModel):
    """What one *line* of the strip read as, on one captured frame.

    One per line the strip draws, not one per frame -- FortuneOx stacks two,
    read separately since handing a recogniser both at once misreads one
    line rather than two right ones. Filled in by the reader worker after
    the frame is taken: null means unread, ``error`` means it was read and
    could not be.
    """

    text: str = Field(description="What Paddle read, verbatim and uncorrected.")
    repaired: str = Field(
        default="",
        description=(
            "Reading repaired against the strip's known wording -- the same "
            "repair the clip reader applies, kept beside the raw text because "
            "a repair can be confidently wrong about which digit."
        ),
    )
    confidence: float = Field(
        ge=0,
        le=100,
        description=(
            "Lowest per-word confidence, 0-100 -- Paddle's own 0-1 score "
            "rescaled, so the floor and the payload mean one thing."
        ),
    )
    reliable: bool = Field(
        description=(
            "Whether confidence cleared CYCLIC_MESSAGES_TEXT_MIN_CONFIDENCE. "
            "That floor was originally measured against a Tesseract reading, "
            "not independently re-measured for Paddle, so an unreliable "
            "reading still keeps its text."
        )
    )
    engine: str = Field(
        default="paddle",
        description="Which engine read it -- always 'paddle', the only OCR engine this project has.",
    )
    region: str = Field(
        default="",
        description=(
            "Named game-config region this line was cropped from, one of "
            "CYCLIC_MESSAGES_TEXT_REGIONS -- says which line of the strip the "
            "text came off."
        ),
    )
    key: str = Field(
        default="",
        description=(
            "What two frames must share to count as the same caption -- the "
            "repaired text with case, spacing and punctuation removed, "
            "collapsing the several frames one caption is usually caught by "
            "into one entry. Deliberately intolerant of a single wrong "
            "character: two real captions can differ by exactly one ('Line 1 "
            "Pays 250' vs 'Line 4 Pays 250'). Empty for a frame that read "
            "nothing, so blanks never group with each other or bridge two "
            "showings of one line."
        ),
    )
    crop: str | None = Field(
        default=None,
        description=(
            "Filename of the caption crop, written beside the screenshot and "
            "served by the same route -- lets a wrong reading be told apart "
            "from a badly aimed region without re-cropping anything."
        ),
    )
    read_ms: int = Field(
        default=0, ge=0, description="How long cropping and recognising took."
    )
    error: str | None = Field(
        default=None,
        description=(
            "Why this frame could not be read. Set instead of dropping the "
            "reading, so a frame the reader choked on is still visible as one."
        ),
    )


class CyclicEvent(BaseModel):
    """One recognised strip change and the screenshot taken for it."""

    sequence: int = Field(ge=1, description="1-based position within the run.")
    event: str = Field(description="Rule name, e.g. 'cyclic-message-shown'.")
    at: datetime | None = Field(
        default=None, description="Game's own timestamp for the line."
    )
    summary: str = Field(description="One-line description of what happened.")
    cycle: int = Field(
        ge=1,
        description=(
            "1-based cyclic sequence this event belongs to. The strip rotates "
            "two unrelated families of message -- an idle attract loop and a "
            "win presentation -- and both count here, so a run groups into "
            "sequences without re-reading."
        ),
    )
    position: int | None = Field(
        default=None,
        description=(
            "1-based position of this message within its sequence; null for "
            "the boundary markers, which are not messages."
        ),
    )
    fields: dict[str, str] = Field(
        default_factory=dict,
        description="Values the rule extracted, e.g. {'from_state': '...'}.",
    )
    captured: bool = Field(
        default=True,
        description=(
            "Whether this event takes a screenshot at all. False for the loop "
            "boundary markers, which are timeline structure rather than a "
            "frame -- distinct from a capture that was attempted and failed."
        ),
    )
    screenshot: str | None = Field(
        default=None,
        description="Image filename inside the run directory; null if it failed.",
    )
    capture_error: str | None = Field(
        default=None, description="Why the screenshot failed, when it did."
    )
    source: CyclicEventSource = Field(
        default=CyclicEventSource.LOG,
        description=(
            "Whether a log line or a timer triggered this event. Defaults to "
            "'log' so a manifest written before sampling existed still reads "
            "correctly."
        ),
    )
    log_line: str = Field(
        description=(
            "The log line the rule matched, verbatim. For a 'sampled' event "
            "this is the boundary line that opened the sampling window, not a "
            "line describing the message in the picture -- there is none."
        )
    )

    readings: list[CyclicFrameReading] = Field(
        default_factory=list,
        description=(
            "What each line of the strip read as on this frame, top line "
            "first -- one entry per CYCLIC_MESSAGES_TEXT_REGIONS. Empty while "
            "unread, live reading is off, or the event took no screenshot; a "
            "list because the stacked lines cycle independently."
        ),
    )


class CyclicRunVideo(BaseModel):
    """One recording: a single window of the strip, not the whole run.

    Recording opens on ``cyclic-game-pays`` and closes on
    ``cyclic-line-pays-cycle-finished`` -- one clip per win the run saw,
    rather than one video of everything between Start and Stop.
    """

    file_name: str | None = Field(
        default=None,
        description=(
            "Video filename inside the run directory, served by the same "
            "route as the screenshots. Null when recording never started or "
            "the file could not be moved beside them."
        ),
    )
    source_path: str | None = Field(
        default=None,
        description="Where OBS actually wrote it, before it was moved.",
    )
    error: str | None = Field(
        default=None,
        description=(
            "Why there is no video -- reported rather than raised, since a "
            "win whose recording failed still keeps every screenshot."
        ),
    )
    kind: str = Field(
        default="win-video",
        description=(
            "Which strip this recorded: 'win-video' for a payout, 'idle-video' "
            "for the between-spins strip after a spin. A run holds both, so "
            "this says which."
        ),
    )
    cycle: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Cyclic sequence this clip covers, matching the events' own "
            "``cycle`` -- so a clip can be shown beside the frames taken in it."
        ),
    )
    started_at: datetime | None = Field(
        default=None,
        description=(
            "When recording began -- when 'cyclic-game-pays' was read, not "
            "when the game logged it."
        ),
    )
    stopped_at: datetime | None = Field(
        default=None, description="When recording ended."
    )
    closed_by: str | None = Field(
        default=None,
        description=(
            "What ended the clip: the ordinary full pass, a spin that cut it "
            "short, a new win that began before the previous one finished, a "
            "run stopped mid-clip, or the closing line never arriving. "
            "Reported because an early end has a reason, not a bug."
        ),
    )


class CyclicRunSummary(BaseModel):
    """A run as it appears in the list, without its events."""

    run_id: str = Field(description="Directory name, 'YYYY-MM-DD_HH-MM-SS'.")
    game: str = Field(description="Game that was being played.")
    status: CyclicRunState = Field(description="Whether and how the run ended.")
    started_at: datetime = Field(description="When tracking started.")
    stopped_at: datetime | None = Field(
        default=None, description="When tracking stopped; null while running."
    )
    message_count: int = Field(
        ge=0,
        description=(
            "Messages seen, i.e. events that take a frame -- boundary markers "
            "excluded as timeline structure."
        ),
    )
    sampled_count: int = Field(
        default=0,
        ge=0,
        description=(
            "How many of those messages were sampled on a timer rather than "
            "triggered by a log line -- shows at a glance how much of a run "
            "rests on evidence."
        ),
    )
    cycle_count: int = Field(
        ge=0,
        description=(
            "Cyclic sequences completed during the run -- idle attract loops "
            "and win presentations together."
        ),
    )
    log_path: str = Field(description="Game log that was followed.")
    videos: list[CyclicRunVideo] = Field(
        default_factory=list,
        description=(
            "One clip per win presentation seen, in order. Empty for a run "
            "where nothing paid -- deliberately no whole-run fallback recording."
        ),
    )


class CyclicRunDetail(CyclicRunSummary):
    """A whole run, including every event. This is the ``run.json`` shape."""

    events: list[CyclicEvent] = Field(
        default_factory=list, description="Events in the order they happened."
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Problems hit during the run that did not stop it.",
    )


class CyclicTextFrame(BaseModel):
    """One frame of a clip, and what the caption on it read as."""

    at_seconds: float = Field(
        ge=0,
        description=(
            "Offset into the clip this frame really sits at, from its index "
            "and the file's own rate -- not the interval that was asked for."
        ),
    )
    frame_index: int = Field(ge=0, description="0-based position in the clip.")
    text: str = Field(description="What the engine read, verbatim and uncorrected.")
    repaired: str = Field(
        default="",
        description=(
            "Reading repaired against the strip's known wording -- what the "
            "messages were grouped by. Kept beside the raw text since a repair "
            "can be confidently wrong about which digit."
        ),
    )
    confidence: float = Field(
        ge=0,
        le=100,
        description=(
            "Lowest per-word confidence on the frame, 0-100 -- the lowest not "
            "the mean, since one mangled word is what makes a caption wrong. "
            "Paddle's own 0-1 score is scaled onto this range."
        ),
    )
    reliable: bool = Field(
        description=(
            "Whether confidence cleared CYCLIC_MESSAGES_TEXT_MIN_CONFIDENCE. "
            "An unreliable frame keeps its text anyway -- it is evidence the "
            "recording, not the reader, failed."
        )
    )


class CyclicTextMessage(BaseModel):
    """One message the strip displayed, from the run of frames showing it.

    Consecutive frames reading the same caption are one message, so how many
    frames it spans is how long it was up, not how many times it appeared.
    "The same caption" tolerates spacing and punctuation but never a
    differing character.
    """

    text: str = Field(
        description=(
            "Best-scoring of the readings that formed this message -- not "
            "necessarily the first: grouping tolerates spacing and "
            "punctuation, so the surest reading is the better bet."
        )
    )
    first_seen: float = Field(
        ge=0, description="Offset into the clip of the first frame reading this."
    )
    last_seen: float = Field(
        ge=0, description="Offset into the clip of the last frame reading this."
    )
    frames: int = Field(ge=1, description="How many sampled frames read it.")
    confidence: float = Field(
        ge=0, le=100, description="Best confidence any of those frames managed."
    )
    reliable: bool = Field(
        description="Whether any frame of this message cleared the floor."
    )

    @property
    def duration_seconds(self) -> float:
        """How long the message was on screen, to the sampling resolution."""
        return self.last_seen - self.first_seen


class CyclicTextCaption(BaseModel):
    """One caption the strip showed, however many times it showed it.

    The strip loops, so the chronological list has the same caption in it
    several times over. ``showings`` counts the separate times it came up;
    the full timeline it was counted from is still on ``messages``.
    """

    text: str = Field(description="The best-scoring reading of this caption.")
    showings: int = Field(ge=1, description="Separate times the strip displayed it.")
    frames: int = Field(
        ge=1, description="Sampled frames it was on screen for, across them all."
    )
    first_seen: float = Field(ge=0, description="Offset it first appeared at.")
    last_seen: float = Field(ge=0, description="Offset it was last on screen at.")
    confidence: float = Field(
        ge=0, le=100, description="Best confidence any frame of it managed."
    )
    reliable: bool = Field(description="Whether any frame of it cleared the floor.")


class CyclicTextReading(BaseModel):
    """Every message recovered from one run's clip."""

    run_id: str = Field(description="Run the clip belongs to.")
    game: str = Field(description="Game that was playing.")
    file_name: str = Field(description="Clip that was read.")
    cycle: int | None = Field(
        default=None, description="Cyclic sequence the clip covers."
    )
    region: str = Field(
        description="Named game-config region the caption was cropped from."
    )
    engine: str = Field(
        description=(
            "Which OCR engine read the frames -- always 'paddle', the only "
            "OCR engine this project has. Carried on the reading anyway, so "
            "two readings of one clip are comparable without assuming that."
        )
    )
    interval_seconds: float = Field(
        gt=0, description="Gap between sampled frames, as asked for."
    )
    duration_seconds: float = Field(
        ge=0, description="Length of the clip, from its own header."
    )
    frames_sampled: int = Field(
        ge=0,
        description=(
            "Frames decoded out of the clip at `interval_seconds`. Every one "
            "of them appears on `frames` with its own offset."
        ),
    )
    frames_read: int = Field(
        default=0,
        ge=0,
        description=(
            "Of those, how many actually went to the recogniser -- usually "
            "several times fewer than `frames_sampled`, since a caption stays "
            "up for several sampled frames in a row and a blank frame is "
            "never read at all. What keeps a 90s clip's ~180 recognitions "
            "well under a 290s budget."
        ),
    )
    frames_unreadable: int = Field(
        ge=0,
        description=(
            "Of those, how many fell below the confidence floor. High here "
            "means the recording smeared the caption -- see "
            "CYCLIC_MESSAGES_TEXT_MIN_CONFIDENCE."
        ),
    )
    captions: list[CyclicTextCaption] = Field(
        default_factory=list,
        description=(
            "Each caption once, in first-shown order, with repeats counted "
            "rather than listed -- answers 'what did the strip say'; "
            "`messages` answers 'when'."
        ),
    )
    messages: list[CyclicTextMessage] = Field(
        default_factory=list,
        description=(
            "Every showing in order, repeats included, since the strip loops. "
            "Kept beside `captions` because 'when' is a different question "
            "from 'whether'."
        ),
    )
    frames: list[CyclicTextFrame] = Field(
        default_factory=list,
        description=(
            "Every frame's own reading, in order -- what the collapsed "
            "`messages` were built from."
        ),
    )


class CyclicStatus(BaseModel):
    """What the panel polls; always returned, running or not."""

    active: bool = Field(description="Whether a run is in progress.")
    run_id: str | None = Field(default=None, description="Active run, if any.")
    game: str | None = Field(
        default=None,
        description=(
            "Game the active run is following. A run keeps its game even if "
            "the active selection changes, so this can differ from it."
        ),
    )
    started_at: datetime | None = Field(default=None, description="When it started.")
    duration_ms: int = Field(default=0, ge=0, description="Elapsed run time.")
    message_count: int = Field(default=0, ge=0, description="Messages seen so far.")
    sampled_count: int = Field(
        default=0, ge=0, description="How many of those were sampled, not logged."
    )
    cycle_count: int = Field(
        default=0, ge=0, description="Cyclic sequences completed so far."
    )
    sampling: bool = Field(
        default=False,
        description=(
            "Whether a line-message pass is being sampled right now -- live "
            "state, so the panel can show a win being followed."
        ),
    )
    recording: bool = Field(
        default=False,
        description=(
            "Whether a clip is being recorded *right now* -- true only "
            "between 'cyclic-game-pays' and the line messages finishing."
        ),
    )
    video_count: int = Field(
        default=0, ge=0, description="Clips saved so far, one per win presentation."
    )
    reading: bool = Field(
        default=False,
        description=(
            "Whether captions are being read *right now*. Never runs "
            "alongside capture -- goes true only once a pass has closed, for "
            "as long as reading that pass's frames takes."
        ),
    )
    recovering: bool = Field(
        default=False,
        description=(
            "Whether a filed clip is being read back *right now*. Live "
            "stills of either strip miss captions -- an OBS screenshot costs "
            "seconds and a caption stays up for about one -- so each clip is "
            "decoded afterwards. The only work that can overlap capture, and "
            "only by the one frame already in flight."
        ),
    )
    recovery_pending: int = Field(
        default=0,
        ge=0,
        description=(
            "Clips filed and not yet read back. Non-zero while a window is "
            "open, since recovery yields to capture -- and non-zero on a "
            "sealed run means those clips were never read back."
        ),
    )
    recovered_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Frames recovered from clips so far. Kept beside `sampled_count` "
            "rather than folded into it: sampled is what the stills caught "
            "live, this is what they went past."
        ),
    )
    no_pay_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Spins this run that paid nothing. Recorded as a marker with no "
            "screenshot and no sequence, but counted -- a run capturing "
            "nothing because the game keeps losing should not look like a "
            "broken run. Majority case on FortuneOx: 29 losses to 26 wins in "
            "one log."
        ),
    )
    queue_depth: int = Field(
        default=0,
        ge=0,
        description=(
            "Frames captured in the pass still going, or read so far out of "
            "the pass just closed. Always 0 once a pass's reading has "
            "finished -- nothing competes with capture until it is done."
        ),
    )
    read_count: int = Field(
        default=0, ge=0, description="Frames read so far this run, across every pass."
    )
    sample_rate: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Frames a second the capture loop actually achieved. "
            "CYCLIC_MESSAGES_SAMPLE_INTERVAL_SECONDS is a floor, not a rate -- "
            "an OBS round trip is what decides this."
        ),
    )
    recent_events: list[CyclicEvent] = Field(
        default_factory=list, description="The most recent events, newest last."
    )
    errors: list[str] = Field(
        default_factory=list, description="Non-fatal problems so far."
    )


class CyclicLiveView(BaseModel):
    """The win presentation being captured right now, frame by frame.

    Its own payload rather than a slice of :class:`CyclicRunDetail`: a run
    spans hours of attract and thousands of frames, while a page polling
    twice a second wants only the pass in front of it -- and this is read
    straight off the live run rather than the manifest, which is flushed at
    most once a second.
    """

    active: bool = Field(description="Whether a run is in progress at all.")
    run_id: str | None = Field(default=None, description="Active run, if any.")
    game: str | None = Field(default=None, description="Game it is following.")
    cycle: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Cyclic sequence these frames belong to -- the win presentation "
            "being (or last) captured. Null before any win. Uses the *first* "
            "sequence when a spin spans two, since a win taken before its "
            "strip finishes opens a second one for the between-spins strip."
        ),
    )
    strip: str | None = Field(
        default=None,
        description=(
            "Which strip these frames show: 'win-video', 'idle-video' "
            "(between spins), or 'win-then-idle' when a win is taken before "
            "the strip finishes. Null before the run has shown any."
        ),
    )
    capturing: bool = Field(
        default=False,
        description=(
            "Whether the capture loop is running *now*. False with frames "
            "present means the pass is over and these are its frames."
        ),
    )
    reading: bool = Field(
        default=False,
        description=(
            "Whether this pass's captions are being read *right now* -- only "
            "once the pass has closed and only until every frame has been "
            "read. False with `capturing` also false means the pass is fully "
            "read or nothing is reading it -- `errors` says which."
        ),
    )
    recovering: bool = Field(
        default=False,
        description=(
            "Whether a filed clip is being read back *right now*. Live "
            "stills of either strip miss captions -- an OBS screenshot costs "
            "seconds and a caption stays up for about one -- so each clip is "
            "decoded afterwards. The only work that can overlap capture, and "
            "only by the one frame already in flight."
        ),
    )
    recovery_pending: int = Field(
        default=0,
        ge=0,
        description=(
            "Clips filed and not yet read back. Non-zero while a window is "
            "open, since recovery yields to capture -- and non-zero on a "
            "sealed run means those clips were never read back."
        ),
    )
    spins_without_pay: int = Field(
        default=0,
        ge=0,
        description=(
            "Spins since the last win, all of which paid nothing -- how a "
            "view with no frames says 'nothing has paid yet' rather than "
            "leaving tracking status ambiguous."
        ),
    )
    queue_depth: int = Field(
        default=0,
        ge=0,
        description=(
            "This pass's frames not yet read. Jumps to `frame_count` the "
            "moment the pass closes, then counts down as `reading` works "
            "through them in order."
        ),
    )
    read_count: int = Field(
        default=0, ge=0, description="This run's frames read so far, across every pass."
    )
    sample_rate: float = Field(
        default=0.0, ge=0, description="Frames a second the capture loop achieved."
    )
    sample_interval_seconds: float = Field(
        default=0.0,
        ge=0,
        description=(
            "The interval the capture loop was asked for, beside the rate it "
            "managed. A message stays on the strip for a bounded time "
            "(1.3-1.8s on FortuneOx), so frames landing further apart than "
            "that skip messages with nothing on the record saying so; both "
            "numbers travel and neither is editorialised into a warning."
        ),
    )
    frame_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Frames captured in this view, across both sequences when a spin "
            "ran to two. Can exceed the length of `frames`, which is capped "
            "at CYCLIC_MESSAGES_LIVE_FRAMES."
        ),
    )
    frames: list[CyclicEvent] = Field(
        default_factory=list,
        description=(
            "The pass's captured frames in order, newest last, capped to "
            "CYCLIC_MESSAGES_LIVE_FRAMES. Whole events, so the live view and "
            "the run view render the same thing from the same fields."
        ),
    )
    errors: list[str] = Field(
        default_factory=list, description="Non-fatal problems so far."
    )
