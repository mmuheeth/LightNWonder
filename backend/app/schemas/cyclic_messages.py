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


class CyclicFrameReading(BaseModel):
    """What one *line* of the strip read as, on one captured frame.

    One of these per line the strip draws, not one per frame: FortuneOx stacks
    two, "LINE 25 PAYS 15" with "PLAY 880 CREDITS" beneath it, each cycling its
    own messages. They are cropped and recognised separately because a caption
    is read recognise-only, and handing the recogniser both at once reads one
    wrong line rather than two right ones -- see CYCLIC_MESSAGES_TEXT_REGIONS.

    Filled in by the reader worker *after* the frame was taken, never by the
    capture loop -- so an event carries a screenshot the moment OBS wrote one
    and gains this a beat later. A frame whose reading is still ``null`` has
    not reached the front of the queue yet; one whose ``error`` is set reached
    it and could not be read, which is a different fact and rendered as one.
    """

    text: str = Field(description="What Paddle read, verbatim and uncorrected.")
    repaired: str = Field(
        default="",
        description=(
            "The same reading with its number slots and vocabulary put right "
            "against the strip's known wording -- the same repair the clip "
            "reader applies, and kept beside the raw text for the same reason: "
            "a repair moves a character to the class the grammar demands, so "
            "it can be confidently wrong about which digit."
        ),
    )
    confidence: float = Field(
        ge=0,
        le=100,
        description=(
            "Lowest per-word confidence, 0-100. Paddle's own 0-1 score scaled "
            "onto Tesseract's range, so one floor serves every reading in this "
            "feature whichever engine produced it."
        ),
    )
    reliable: bool = Field(
        description=(
            "Whether the confidence cleared CYCLIC_MESSAGES_TEXT_MIN_CONFIDENCE. "
            "An unreliable reading keeps its text -- see the settings note; "
            "that floor was measured on Tesseract and not on Paddle."
        )
    )
    engine: str = Field(
        default="paddle",
        description=(
            "Which engine read it. Always 'paddle' for a live frame -- the "
            "clip reader's Tesseract option is not offered here, because a "
            "reader that has to keep up with the capture loop is not the place "
            "to shell out to a subprocess per frame."
        ),
    )
    region: str = Field(
        default="",
        description=(
            "Named game-config region this line was cropped from, one of "
            "CYCLIC_MESSAGES_TEXT_REGIONS. On the reading rather than the run "
            "because a frame has several: it is what says which line of the "
            "strip the text came off."
        ),
    )
    key: str = Field(
        default="",
        description=(
            "What two frames have to share to be showing the same caption -- "
            "the repaired text with case, spacing and punctuation removed. "
            "Sampling runs faster than the strip changes on purpose, so most "
            "captions are caught by two or three frames in a row, and this is "
            "what collapses those into one entry. "
            "On the payload rather than worked out by the reader, because the "
            "rule is subtle enough to be worth having only once: it is "
            "deliberately *not* tolerant of a wrong character, since two "
            "genuinely different captions here differ by exactly one ('Line 1 "
            "Pays 250' against 'Line 4 Pays 250') and any slack wide enough to "
            "merge a misread is wide enough to merge two real messages. Empty "
            "for a frame that read nothing, which is what stops blanks "
            "grouping with each other or bridging two showings of one line."
        ),
    )
    crop: str | None = Field(
        default=None,
        description=(
            "Filename of the caption crop, written beside the screenshot and "
            "served by the same route. There so a wrong reading can be told "
            "apart from a badly aimed region without re-cropping anything."
        ),
    )
    read_ms: int = Field(
        default=0, ge=0, description="How long cropping and recognising took."
    )
    error: str | None = Field(
        default=None,
        description=(
            "Why this frame could not be read. Set instead of dropping the "
            "reading, so a frame the reader choked on is visible as one."
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

    readings: list[CyclicFrameReading] = Field(
        default_factory=list,
        description=(
            "What each line of the strip read as on this frame, top line "
            "first -- one entry per CYCLIC_MESSAGES_TEXT_REGIONS. Empty while "
            "nothing has read it: the frame is still waiting for its pass to "
            "close, live reading is off, or the event never took a screenshot. "
            "A list rather than one reading because the strip is stacked and "
            "the lines cycle independently, so 'what was on screen' is all of "
            "them together."
        ),
    )


class CyclicRunVideo(BaseModel):
    """One recording: a single window of the strip, not the whole run.

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
    kind: str = Field(
        default="win-video",
        description=(
            "Which strip this is a recording of: 'win-video' for a win paying "
            "out, 'idle-video' for the between-spins strip that follows a spin "
            "(after a loss, or after a win is taken). A run holds both kinds "
            "and they are different things to watch, so the clip says which "
            "rather than leaving it to be guessed from where it sits."
        ),
    )
    cycle: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Cyclic sequence this clip covers, matching the ``cycle`` on the "
            "events of that window -- so a clip can be shown beside the frames "
            "taken inside it."
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
    key: str = Field(
        default="",
        description=(
            "What this message had to share with a frame for the two to be "
            "one showing -- `text` with case, spacing and punctuation taken "
            "out (`cyclic_text.caption_key`). On the payload so that a client "
            "reading a clip in windows can stitch them: the last message of "
            "one window and the first of the next are the same showing when "
            "their keys match. Sent rather than left to be recomputed because "
            "a second implementation of 'the same caption' is a second thing "
            "to keep in step, and this one only ever compares two strings."
        ),
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
    key: str = Field(
        default="",
        description=(
            "The grouping key, as on `CyclicTextMessage` and for the same "
            "reason: a client stitching windows counts showings by it."
        ),
    )
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
    from_seconds: float = Field(
        default=0.0,
        ge=0,
        description="Offset into the clip this reading starts at.",
    )
    to_seconds: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Offset it stops at, which is what a caller pages with: ask again "
            "from here, and stop when it reaches `duration_seconds`. **The "
            "server's answer, not the caller's request** -- a window longer "
            "than CYCLIC_MESSAGES_TEXT_WINDOW_SECONDS is clamped to it, so a "
            "caller can ask for the whole clip and be told how much of it it "
            "is actually getting rather than having to know the budget."
        ),
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
            "Of those, how many actually went to the recogniser. Lower than "
            "`frames_sampled`, usually several times lower, and that is what "
            "makes this request finish: a caption stays on the strip for "
            "several of the seconds the clip is sampled at, so most frames are "
            "the frame before them again and share its reading, while a frame "
            "whose bands are all blank is taken as blank without being read "
            "at all. Reading every frame instead is what timed a 90s clip's "
            "~180 recognitions out against the client's 290s."
        ),
    )
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
    reading: bool = Field(
        default=False,
        description=(
            "Whether captions are being read *right now*. Capture is the only "
            "priority while a pass is going, so reading never runs alongside "
            "it -- this goes true only once a pass has closed, for exactly as "
            "long as it takes to read that pass's frames in order, and false "
            "again once it has (or once nothing was captured to read)."
        ),
    )
    recovering: bool = Field(
        default=False,
        description=(
            "Whether a filed clip is being read back *right now*. The live "
            "stills of either strip miss captions -- an OBS screenshot costs "
            "seconds while a recording is running and a caption stays up for "
            "about one -- so each clip is decoded afterwards and the frames "
            "where its caption changed are written out as screenshots of "
            "their own. This is the only work on a run that can overlap a "
            "capture, and only by the one frame already in flight: a window "
            "opening asks it to give way and its queue survives to be "
            "finished at the next quiet moment."
        ),
    )
    recovery_pending: int = Field(
        default=0,
        ge=0,
        description=(
            "Clips filed and not yet read back. Non-zero while a window is "
            "open, since recovery gives way to capture -- and non-zero on a "
            "sealed run means those clips never were read back, which "
            "'Read the messages' on each still answers completely."
        ),
    )
    recovered_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Frames recovered from clips so far this run. Beside "
            "`sampled_count` rather than folded into it: a sampled frame is "
            "what the stills caught while the strip played, and this is what "
            "they went past."
        ),
    )
    no_pay_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Spins this run that paid nothing. Those show no win strip at all, "
            "so they are recorded as a marker with no screenshot and open no "
            "sequence -- but they are counted, because a run capturing nothing "
            "because the game keeps losing and a run capturing nothing because "
            "it has broken look identical otherwise. The majority case on "
            "FortuneOx: 29 losing spins against 26 winning ones in one log."
        ),
    )
    queue_depth: int = Field(
        default=0,
        ge=0,
        description=(
            "Frames captured in the pass still going, or read so far out of "
            "the pass just closed while `reading` is true. Always 0 once a "
            "pass's reading has finished -- there is no persistent backlog "
            "that can grow, because nothing competes with capture for OBS or "
            "the CPU until capture is done."
        ),
    )
    read_count: int = Field(
        default=0, ge=0, description="Frames read so far this run, across every pass."
    )
    sample_rate: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Frames a second the capture loop actually achieved on the pass "
            "running now, or the last one. Reported because "
            "CYCLIC_MESSAGES_SAMPLE_INTERVAL_SECONDS is a floor and not a "
            "rate -- an OBS round trip is what decides this."
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

    Its own payload rather than a slice of :class:`CyclicRunDetail`, for two
    reasons. A run is every event since Start -- hours of attract, thousands of
    frames -- and a page polling twice a second wants only the pass in front of
    it. And the manifest on disk is written at most once a second
    (CYCLIC_MESSAGES_MANIFEST_INTERVAL_SECONDS), while this is read straight
    off the live run, so a frame appears here the moment OBS wrote it rather
    than at the next flush.
    """

    active: bool = Field(description="Whether a run is in progress at all.")
    run_id: str | None = Field(default=None, description="Active run, if any.")
    game: str | None = Field(default=None, description="Game it is following.")
    cycle: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Cyclic sequence these frames belong to -- the win presentation "
            "being captured, or the last one that was. Null before the run has "
            "seen a win. The *first* of them where a spin ran to two "
            "sequences: a win taken after its line messages finished opens a "
            "second one for the between-spins strip, and `frames` carries "
            "both, since the two are one spin and a tester watching the card "
            "should not have the win's frames taken off it at the moment they "
            "press Take Win."
        ),
    )
    strip: str | None = Field(
        default=None,
        description=(
            "Which strip these frames are of: 'win-video' for a win paying "
            "out, 'idle-video' for the between-spins strip that follows a "
            "spin -- after a loss, or once a win has been taken -- and "
            "'win-then-idle' for both of them in one view, which is what a "
            "win taken before the strip has finished produces. A run shows "
            "all three and they are different things to read, so a view of "
            "one should not be captioned as another. Null before the run has "
            "shown any."
        ),
    )
    capturing: bool = Field(
        default=False,
        description=(
            "Whether the capture loop is running *now*, i.e. the run is "
            "between 'cyclic-game-pays' and the line messages finishing. False "
            "with frames present means the pass is over and these are its "
            "frames."
        ),
    )
    reading: bool = Field(
        default=False,
        description=(
            "Whether captions for this pass are being read *right now* -- true "
            "only once the pass has closed and only until every one of its "
            "frames has been read, since reading never runs while capture is "
            "still going. False with `capturing` also false and frames present "
            "means either the pass is fully read or nothing is reading it -- "
            "`errors` says which."
        ),
    )
    recovering: bool = Field(
        default=False,
        description=(
            "Whether a filed clip is being read back *right now*. The live "
            "stills of either strip miss captions -- an OBS screenshot costs "
            "seconds while a recording is running and a caption stays up for "
            "about one -- so each clip is decoded afterwards and the frames "
            "where its caption changed are written out as screenshots of "
            "their own. This is the only work on a run that can overlap a "
            "capture, and only by the one frame already in flight: a window "
            "opening asks it to give way and its queue survives to be "
            "finished at the next quiet moment."
        ),
    )
    recovery_pending: int = Field(
        default=0,
        ge=0,
        description=(
            "Clips filed and not yet read back. Non-zero while a window is "
            "open, since recovery gives way to capture -- and non-zero on a "
            "sealed run means those clips never were read back, which "
            "'Read the messages' on each still answers completely."
        ),
    )
    spins_without_pay: int = Field(
        default=0,
        ge=0,
        description=(
            "Spins since the last win, all of which paid nothing. A strip that "
            "shows no win messages is what a losing spin looks like, so this is "
            "how a view with no frames on it says 'nothing has paid yet' rather "
            "than leaving the reader to wonder whether tracking is working."
        ),
    )
    queue_depth: int = Field(
        default=0,
        ge=0,
        description=(
            "This pass's frames not yet read. Climbs to `frame_count` the "
            "moment the pass closes (nothing was read while it was open) and "
            "counts down to 0 as `reading` works through them in order."
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
            "managed. The pair is the coverage question and neither half "
            "answers it alone: a message stays on the strip for a bounded time "
            "(1.3-1.8s on FortuneOx), so frames landing further apart than "
            "that skip messages with nothing on the record saying so. Both "
            "numbers travel and neither is editorialised into a warning: the "
            "clip the run recorded covers the window either way, and "
            "'Read the messages' off it is the complete list."
        ),
    )
    frame_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Frames captured in this view, across both sequences when a spin "
            "ran to two. Can exceed the length of `frames` -- that list is "
            "capped at CYCLIC_MESSAGES_LIVE_FRAMES."
        ),
    )
    frames: list[CyclicEvent] = Field(
        default_factory=list,
        description=(
            "The pass's captured frames in order, newest last, capped to the "
            "most recent CYCLIC_MESSAGES_LIVE_FRAMES. Whole events rather than "
            "a reduced shape, so the live view and the run view render the "
            "same thing from the same fields."
        ),
    )
    errors: list[str] = Field(
        default_factory=list, description="Non-fatal problems so far."
    )
