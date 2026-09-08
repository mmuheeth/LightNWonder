"""Analyze Spin runtime settings: which keys drive one spin, how long each stage is
allowed to take, and where a run's record is kept."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

__all__ = ["AnalyzeSpinSettings"]


class AnalyzeSpinSettings(BaseSettings):
    """Runtime options for the orchestrated single-spin analysis."""

    # Subdirectory of the capture root holding one folder per run, and the
    # recording subdirectory under the recording root.
    ANALYZE_SPIN_DIR_NAME: str = "analyze-spin"

    # The i-deck key that spins. A layout id from IDECK_PANEL_XML, not a game
    # concept: FortuneOx binds `Rebet` to SpinButtonMsg (see backend/README).
    ANALYZE_SPIN_SPIN_BUTTON: str = "Rebet"

    # The `button_targets` entry clicked to collect a win. Take-win is not on
    # the deck's fourteen keys, so it is a click into the game's own window.
    ANALYZE_SPIN_TAKE_WIN_TARGET: str = "take_win"

    # Whether one run also records a video of itself. Off by default -- a
    # caller opts in per run via `/start?record=true` rather than through this
    # setting, which only supplies the default when the request leaves it out.
    ANALYZE_SPIN_RECORD: bool = False

    # How often the game log is re-read while waiting for the next event.
    ANALYZE_SPIN_POLL_SECONDS: float = Field(default=0.1, gt=0)

    # A confirmed i-deck press proves the panel saw it, not that the game acted,
    # so the spin is not considered started until the game publishes it.
    ANALYZE_SPIN_SPIN_TIMEOUT_SECONDS: float = Field(default=10.0, gt=0)

    # Reel spin plus every stop delay the theme adds.
    ANALYZE_SPIN_REELS_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0)

    # How long to wait for the win meter to finish counting up before calling
    # the spin a loss. Must exceed the longest count-up the game animates: too
    # short reports a win as a loss, and the cost of too long is only that a
    # losing spin waits it out.
    ANALYZE_SPIN_WIN_WAIT_SECONDS: float = Field(default=10.0, gt=0)

    # Breathing room between the win meter settling and its screenshot.
    ANALYZE_SPIN_WIN_SETTLE_SECONDS: float = Field(default=0.5, ge=0)

    # After OBS is re-pointed at the game's window, before the first screenshot.
    # Re-pointing a window capture makes OBS render nothing for a moment, and a
    # screenshot taken inside it comes back black -- which is not an error
    # anywhere, just an empty frame that quietly invalidates both validations.
    ANALYZE_SPIN_SOURCE_SETTLE_SECONDS: float = Field(default=1.0, ge=0)

    # A screenshot with nothing in it is retried rather than accepted, since
    # every reading taken off a black frame is meaningless rather than dark.
    ANALYZE_SPIN_BLANK_RETRIES: int = Field(default=2, ge=0)
    ANALYZE_SPIN_BLANK_RETRY_SECONDS: float = Field(default=0.75, ge=0)

    # Between a confirmed take-win and the screenshot proving it landed -- the
    # click is proven by the log, the balance moving is an animation.
    ANALYZE_SPIN_COLLECT_SETTLE_SECONDS: float = Field(default=1.5, ge=0)

    # --- per-tile clips of a winning spin ---------------------------------
    # Only ever made on a run that is *also* recording a video, and only when
    # the spin won. Not a step of the sequence and deliberately not shown as
    # one: it produces no reading, nothing is graded by it, and a failure to
    # make it is reported on `errors` rather than as a failed stage.
    ANALYZE_SPIN_TILE_CLIPS: bool = True

    # How long a clip covers, from just after the result screenshot -- which is
    # to say from the win presentation's first moments, since it starts as soon
    # as the reels stop and the win meter has counted up.
    ANALYZE_SPIN_TILE_CLIP_SECONDS: float = Field(default=5.0, gt=0, le=30)

    # Frames a second to aim for. A ceiling, not a promise: obs-websocket has no
    # video stream, so each frame is a screenshot request and the loop goes as
    # fast as OBS answers. What was achieved is reported as `fps` on the set.
    ANALYZE_SPIN_TILE_CLIP_FPS: float = Field(default=10.0, gt=0, le=30)

    # The image format the clip's frames are grabbed in. JPEG on purpose: these
    # are video frames rather than evidence, and PNG-encoding a 1080p canvas ten
    # times a second is the slowest part of the loop by a distance. Settable
    # because the list of formats is Qt's, so it belongs to the OBS build.
    ANALYZE_SPIN_TILE_CLIP_IMAGE_FORMAT: str = Field(default="jpg", min_length=1)
    ANALYZE_SPIN_TILE_CLIP_QUALITY: int = Field(default=90, ge=-1, le=100)

    # Preferred encoder, as a four-character code: VP80, avc1, mp4v or MJPG. Blank
    # takes the first that works. Only ever a preference -- OpenCV reports a writer
    # as open for an encoder that then fails to initialise, so
    # `app/utils/tile_video.py` probes each candidate by writing a frame. VP8 in
    # WebM leads because it is the only one of the four a browser plays inline, at
    # the cost of one unsuppressable stderr line per file.
    ANALYZE_SPIN_TILE_CLIP_CODEC: str = ""

    # The run directory's subdirectory holding them, one file per tile.
    ANALYZE_SPIN_TILE_CLIP_DIR_NAME: str = "tile-clips"

    # Screenshot width, or null for the OBS canvas's own. Left native by
    # default: the same frames are cropped for OCR and split into tiles, and
    # both lose more to a downscale than the file size is worth.
    ANALYZE_SPIN_SCREENSHOT_WIDTH: int | None = Field(default=None, ge=8, le=4096)

    # How far two meter readings may differ and still be called equal. Cash is
    # drawn to two decimals, so this only absorbs the OCR of the last one.
    ANALYZE_SPIN_METER_TOLERANCE: float = Field(default=0.005, ge=0)

    # The same, for the relations checked in credits. A credit is a whole number
    # and a credit figure is usually *converted* from a money one, so this absorbs
    # that division's rounding and nothing else -- half a credit cannot hide a
    # real discrepancy, since the smallest real one is a whole credit.
    ANALYZE_SPIN_METER_CREDIT_TOLERANCE: float = Field(default=0.5, ge=0)

    # Recognised log events kept on a run, so a game that logs continuously
    # cannot grow one run's record without bound.
    ANALYZE_SPIN_MAX_EVENTS: int = Field(default=200, ge=1)

    # Which trained network names the tiles of the spin's reels when the request
    # does not say -- the same relationship `ANALYZE_SPIN_RECORD` has to
    # `/start?record=`. Blank uses CLASSIFIER_ARCHITECTURE, which is
    # EfficientNet-B0. Both networks stay trained at once and they do not read the
    # same split equally well, so which one grades a spin is a per-run choice
    # first and a setting second.
    ANALYZE_SPIN_CLASSIFIER_ARCHITECTURE: str = ""

    # The confidence floor a tile has to clear to be named while grading a spin.
    # 0.85 rather than the classifier page's own 0.90: that floor sits far above
    # where the classes separate, which is safe on a page that shows the ranked
    # candidates beside every tile but costly here -- a payline through an unnamed
    # tile stops there, so the spin looks like it paid less than it did. Blank opts
    # back into CLASSIFIER_MIN_CONFIDENCE.
    ANALYZE_SPIN_CLASSIFIER_MIN_CONFIDENCE: float | None = Field(
        default=0.85, ge=0.0, le=1.0
    )

    @field_validator("ANALYZE_SPIN_SCREENSHOT_WIDTH", mode="before")
    @classmethod
    def _blank_width_is_native(cls, value: object) -> object:
        """Read ``ANALYZE_SPIN_SCREENSHOT_WIDTH=`` as "the canvas's own"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("ANALYZE_SPIN_CLASSIFIER_MIN_CONFIDENCE", mode="before")
    @classmethod
    def _blank_floor_is_configured(cls, value: object) -> object:
        """Read a blank ``ANALYZE_SPIN_CLASSIFIER_MIN_CONFIDENCE`` as the classifier's
        own floor, which absent (the default above) does not mean."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def analyze_spin_classifier_architecture(self) -> str | None:
        """Which network grades a spin, or ``None`` to let the classifier decide."""
        chosen = self.ANALYZE_SPIN_CLASSIFIER_ARCHITECTURE.strip()
        return chosen or None
