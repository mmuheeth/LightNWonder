"""Cyclic message capture payloads; also the shape of the ``run.json`` manifest
written to disk, deliberately shared so the UI and the folder never disagree.

Mirrors :mod:`app.schemas.event_capture` -- one event, one run, one status --
with two additions that feature has no use for: an event's ``captured`` flag
(the loop boundaries are recorded without a frame) and the run's ``videos``,
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
    INTERRUPTED = "interrupted"  # backend shut down while the run was going


class CyclicEventSource(StrEnum):
    """Whether a log line triggered this event, or a clock did.

    The distinction is the whole honesty of the feature. Every ``LOG`` event
    stands on a line the game wrote, quoted in :attr:`CyclicEvent.log_line`.
    A ``SAMPLED`` event does not: the individual line messages are never
    logged, so those frames are taken on a timer between the two boundaries
    that *are* logged, and ``log_line`` quotes the boundary that opened the
    window rather than the message in the picture. Collapsing the two would
    make a frame taken on a guess look like a frame taken on evidence.
    """

    LOG = "log"
    SAMPLED = "sampled"


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
            "two unrelated families of message and both count here: an idle "
            "attract loop (from 'cyclic-cycle-started' to its "
            "'cyclic-cycle-completed') and one win presentation (from "
            "'cyclic-game-pays' through its line messages). So a run groups "
            "into sequences without re-reading."
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
            "Whether a log line triggered this event or a timer did. Defaults "
            "to 'log' so a manifest written before sampling existed still "
            "reads correctly."
        ),
    )
    log_line: str = Field(
        description=(
            "The log line the rule matched, verbatim. For a 'sampled' event "
            "this is the boundary line that opened the sampling window, not a "
            "line describing the message in the picture -- there is none."
        )
    )


class CyclicRunVideo(BaseModel):
    """One recording: a single win presentation, not the whole run.

    Recording opens on ``cyclic-game-pays`` and closes on
    ``cyclic-line-pays-cycle-finished``, so a clip is exactly the window the
    strip spends displaying "GAME PAYS 168" and the line messages after it. A
    run holds one of these per win it saw rather than one video of everything
    between Start and Stop -- an hour of idle attract is not what a reader of
    this feature is looking for.
    """

    file_name: str | None = Field(
        default=None,
        description=(
            "Video filename inside the run directory, served by the same route "
            "as the screenshots. Null when recording never started or the file "
            "could not be moved beside them."
        ),
    )
    source_path: str | None = Field(
        default=None,
        description="Where OBS actually wrote it, before it was moved.",
    )
    error: str | None = Field(
        default=None,
        description=(
            "Why there is no video. A win whose recording failed still keeps "
            "every screenshot, so this is reported rather than raised."
        ),
    )
    cycle: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Cyclic sequence this clip covers, matching the ``cycle`` on the "
            "events of that win presentation -- so a clip can be shown beside "
            "the frames taken inside it."
        ),
    )
    started_at: datetime | None = Field(
        default=None,
        description=(
            "When recording began, which is when 'cyclic-game-pays' was read "
            "rather than when the game logged it."
        ),
    )
    stopped_at: datetime | None = Field(
        default=None, description="When recording ended."
    )
    closed_by: str | None = Field(
        default=None,
        description=(
            "What ended the clip: 'cyclic-line-pays-cycle-finished' for the "
            "ordinary full pass, 'cyclic-results-cycle-stopped' for a pass the "
            "next spin cut short, 'cyclic-game-pays' for a win that began "
            "before the previous one reported finishing, 'run-stopped' for a "
            "clip still open when tracking stopped, or 'time-limit' for the "
            "closing line that never arrived. Reported because a clip that "
            "ended early is a shorter video for a reason, not a broken one."
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
            "Messages seen, i.e. events that take a frame -- the boundary "
            "markers are excluded because they are timeline structure."
        ),
    )
    sampled_count: int = Field(
        default=0,
        ge=0,
        description=(
            "How many of those messages were sampled on a timer rather than "
            "triggered by a log line. Reported so a reader can tell at a "
            "glance how much of a run rests on evidence."
        ),
    )
    cycle_count: int = Field(
        ge=0,
        description=(
            "Cyclic sequences that completed during the run -- idle attract "
            "loops and win presentations together."
        ),
    )
    log_path: str = Field(description="Game log that was followed.")
    videos: list[CyclicRunVideo] = Field(
        default_factory=list,
        description=(
            "One clip per win presentation the run saw, in the order they "
            "happened. Empty for a run during which nothing paid -- there is "
            "no whole-run recording to fall back on, deliberately."
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
            "The same reading with its number slots and vocabulary put right "
            "against the strip's known wording -- what the messages were "
            "grouped by. Kept beside the raw text rather than replacing it: a "
            "repair moves a character to the class the grammar demands, so it "
            "can be confidently wrong about which digit, and the only way to "
            "notice is to see both."
        ),
    )
    confidence: float = Field(
        ge=0,
        le=100,
        description=(
            "Lowest per-word confidence on the frame, 0-100. The lowest, not "
            "the mean: one mangled word is what makes a caption wrong, and "
            "averaging hides it behind the words that read cleanly. Paddle's "
            "own 0-1 score is scaled onto this range, and a 'word' there is "
            "one region its detector found rather than one word of English."
        ),
    )
    reliable: bool = Field(
        description=(
            "Whether the confidence cleared CYCLIC_MESSAGES_TEXT_MIN_CONFIDENCE. "
            "An unreliable frame keeps its text anyway -- it is the evidence "
            "that the recording, not the reader, is what failed."
        )
    )


class CyclicTextMessage(BaseModel):
    """One message the strip displayed, from the run of frames showing it.

    Consecutive frames reading the same caption are one message: the frames are
    taken faster than the strip changes, so how many frames a message spans is
    how long it was up rather than how many times it appeared. "The same
    caption" ignores case, spacing and punctuation but never a differing
    character -- see ``cyclic_text._key`` for why that line is drawn there.
    """

    text: str = Field(
        description=(
            "The best-scoring of the readings that formed this message. Not "
            "necessarily the first: grouping tolerates spacing and punctuation, "
            "so the frames need not agree character for character, and the one "
            "the engine was surest of is the better bet."
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

    The strip *loops*: a win presentation walks Line 1 to Line 40 and then
    starts again, so the chronological list has the same caption in it several
    times over. That repetition is a true fact about the strip and a poor way
    to read one, so it is counted here rather than listed -- ``showings`` is
    how many separate times the caption came up, and the timeline it was
    counted from is still on ``messages``.
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
            "Which OCR engine read the frames -- 'paddle' or 'tesseract'. On "
            "the reading rather than inferred from the settings, because the "
            "two are selectable per host and disagree about a caption: two "
            "readings of one clip are only comparable if each says who made it."
        )
    )
    interval_seconds: float = Field(
        gt=0, description="Gap between sampled frames, as asked for."
    )
    duration_seconds: float = Field(
        ge=0, description="Length of the clip, from its own header."
    )
    frames_sampled: int = Field(ge=0, description="Frames decoded and read.")
    frames_unreadable: int = Field(
        ge=0,
        description=(
            "Of those, how many fell below the confidence floor. High here "
            "means the recording smeared the caption -- see the settings note "
            "on CYCLIC_MESSAGES_TEXT_MIN_CONFIDENCE."
        ),
    )
    captions: list[CyclicTextCaption] = Field(
        default_factory=list,
        description=(
            "Each caption once, in the order the strip first showed it, with "
            "the repeats counted rather than listed. This is the answer to "
            "'what did the strip say'; `messages` is the answer to 'when'."
        ),
    )
    messages: list[CyclicTextMessage] = Field(
        default_factory=list,
        description=(
            "Every showing in order, repeats included -- the strip loops, so a "
            "caption appears once per time round. Kept beside `captions` "
            "because when a line came up is a different question from whether "
            "it did."
        ),
    )
    frames: list[CyclicTextFrame] = Field(
        default_factory=list,
        description=(
            "Every frame's own reading, in order. Kept beside the collapsed "
            "messages rather than instead of them: the messages are the answer, "
            "and these are what the answer was built from."
        ),
    )


class CyclicStatus(BaseModel):
    """What the panel polls; always returned, running or not."""

    active: bool = Field(description="Whether a run is in progress.")
    run_id: str | None = Field(default=None, description="Active run, if any.")
    game: str | None = Field(
        default=None,
        description=(
            "Game the active run is following. A run keeps its game even if the "
            "active selection changes, so this can differ from the selected game."
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
            "Whether a line-message pass is being sampled right now. Live "
            "state, so the panel can show that a win is being followed."
        ),
    )
    recording: bool = Field(
        default=False,
        description=(
            "Whether a clip is being recorded *right now* -- true only between "
            "'cyclic-game-pays' and the line messages finishing, so an idle "
            "run reports false while still tracking."
        ),
    )
    video_count: int = Field(
        default=0, ge=0, description="Clips saved so far, one per win presentation."
    )
    recent_events: list[CyclicEvent] = Field(
        default_factory=list, description="The most recent events, newest last."
    )
    errors: list[str] = Field(
        default_factory=list, description="Non-fatal problems so far."
    )
