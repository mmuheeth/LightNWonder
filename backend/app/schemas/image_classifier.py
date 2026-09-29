"""Image classifier status, dataset, training and classification payloads."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.roi import RoiFrame


class ClassifierState(StrEnum):
    """Whether a tile can be named right now."""

    DISABLED = "disabled"
    """``CLASSIFIER_ENABLED`` is false; torch is not even imported."""

    NOT_INSTALLED = "not_installed"
    """torch or torchvision could not be imported."""

    UNTRAINED = "untrained"
    """The stack is there but no model has been trained."""

    TRAINING = "training"
    """A run is under way; the previous model, if any, still answers."""

    STALE = "stale"
    """A model is loaded but predates the current preprocessing."""

    ERROR = "error"
    """A checkpoint exists and will not load."""

    READY = "ready"


class TrainingStageState(StrEnum):
    """How far one stage of a training run got."""

    PENDING = "pending"
    """Not reached yet."""

    RUNNING = "running"

    COMPLETED = "completed"

    SKIPPED = "skipped"
    """Deliberately not run -- zero epochs configured for it."""

    FAILED = "failed"


class TrainingRunState(StrEnum):
    """How the run as a whole ended, or that it has not."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DatasetClass(BaseModel):
    """One symbol's training pictures."""

    symbol: str = Field(description="Directory name, which is the symbol code.")
    label: str = Field(description="Display name from the game config, or the code.")
    count: int = Field(ge=0, description="Readable images in the directory.")
    widths: list[int] = Field(
        default_factory=list, description="Distinct widths present, ascending."
    )
    heights: list[int] = Field(default_factory=list, description="Distinct heights.")
    composited: bool = Field(
        description=(
            "Whether this artwork already carries the game's reel field and frame. "
            "False means it is a transparent cut-out, composited onto a synthesised "
            "background before training."
        )
    )
    mean_opacity: float = Field(
        ge=0.0, le=1.0, description="Mean opaque fraction inside the alpha box."
    )


class DatasetSummary(BaseModel):
    """What is available to train on, and what is wrong with it."""

    directory: str = Field(description="Absolute path the images were read from.")
    exists: bool = Field(description="Whether that directory is there at all.")
    classes: list[DatasetClass] = Field(default_factory=list)
    total_images: int = Field(default=0, ge=0)
    fingerprint: str = Field(
        default="", description="Digest of the files, for spotting a stale model."
    )
    single_image_classes: list[str] = Field(
        default_factory=list,
        description="Codes with exactly one picture; these cannot be validated.",
    )
    missing_symbols: list[str] = Field(
        default_factory=list,
        description=(
            "Codes the active game declares with no artwork here; each will be "
            "shown to the model eventually and can only come back as another "
            "class or as unknown."
        ),
    )
    warnings: list[str] = Field(default_factory=list)
    error: str | None = Field(
        default=None, description="Why the dataset is unreadable; null when it is."
    )


class ClassifierMetrics(BaseModel):
    """What the last training run measured."""

    classes: list[str] = Field(default_factory=list)
    architecture: str = Field(
        default="", description="Which network produced these figures."
    )
    train_accuracy: float = Field(default=0.0, ge=0.0, le=1.0)
    train_loss: float = Field(default=0.0, ge=0.0)
    frame_holdout_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    frame_holdout_samples: int = Field(default=0, ge=0)
    frame_holdout_classes: list[str] = Field(default_factory=list)
    frame_holdout_leakage: float = Field(default=0.0, ge=0.0, le=1.0)
    augmented_accuracy: float = Field(default=0.0, ge=0.0, le=1.0)
    augmented_samples: int = Field(default=0, ge=0)
    per_class_holdout: dict[str, float] = Field(default_factory=dict)
    per_class_augmented: dict[str, float] = Field(default_factory=dict)
    single_image_classes: list[str] = Field(default_factory=list)
    seconds: float = Field(default=0.0, ge=0.0)


class ModelSummary(BaseModel):
    """The checkpoint currently answering, if there is one."""

    path: str
    architecture: str = Field(description="Which network these weights are for.")
    label: str = Field(description="That network's display name, e.g. 'ResNet34'.")
    trained_at: str = Field(description="When the checkpoint was written.")
    classes: list[str] = Field(default_factory=list)
    image_size: int = Field(ge=1)
    transform_version: int = Field(
        description="Preprocessing generation the model was fitted under."
    )
    expected_transform_version: int = Field(
        description="What this build preprocesses with; a mismatch means stale."
    )
    dataset_fingerprint: str = Field(default="")
    dataset_changed: bool = Field(
        description="Whether the images on disk differ from the ones trained on."
    )
    metrics: ClassifierMetrics = Field(default_factory=ClassifierMetrics)


class TrainingEpoch(BaseModel):
    """One epoch's figures, for the progress panel."""

    stage: str
    epoch: int = Field(ge=1)
    epochs: int = Field(ge=1)
    loss: float
    accuracy: float = Field(ge=0.0, le=1.0)
    seconds: float = Field(ge=0.0)


class TrainingStage(BaseModel):
    """One stage of the run."""

    key: str
    label: str
    state: TrainingStageState = TrainingStageState.PENDING
    detail: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None


class TrainingRun(BaseModel):
    """A training run, live or finished."""

    run_id: str
    architecture: str = Field(default="", description="Which network this run fitted.")
    state: TrainingRunState
    message: str = Field(description="A sentence naming where the run has got to.")
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    stages: list[TrainingStage] = Field(default_factory=list)
    epochs: list[TrainingEpoch] = Field(default_factory=list)
    epoch: int = Field(default=0, ge=0, description="Epochs finished so far.")
    epoch_total: int = Field(default=0, ge=0, description="Epochs the run will do.")
    progress: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Fraction of epochs finished."
    )
    estimated_seconds: float = Field(
        default=0.0, ge=0.0, description="Rough total, so a slow run looks alive."
    )
    cancel_requested: bool = False
    metrics: ClassifierMetrics | None = None
    error: str | None = None
    error_code: str | None = None


class ClassifierStatus(BaseModel):
    """What the page polls; always returned, engine or no engine."""

    state: ClassifierState
    detail: str | None = Field(
        default=None, description="Why a tile cannot be named; null when ready."
    )
    torch_version: str | None = None
    torchvision_version: str | None = None
    threads: int = Field(ge=1, description="Threads torch is allowed.")
    min_confidence: float = Field(ge=0.0, le=1.0)
    architecture: str = Field(
        default="", description="Which network names a tile, e.g. 'resnet34'."
    )
    model: ModelSummary | None = Field(
        default=None, description="The trained model, if one has been fitted."
    )
    dataset: DatasetSummary
    training: TrainingRun | None = Field(
        default=None, description="The live or most recent run; null if never run."
    )
    active: bool = Field(default=False, description="Whether a run is under way.")


class TrainRequest(BaseModel):
    """Overrides for one training run, leaving the rest as configured."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"epochs_finetune": 6}, {}]},
    )

    epochs_head: int | None = Field(default=None, ge=0, le=200)
    epochs_finetune: int | None = Field(default=None, ge=0, le=200)
    samples_per_epoch: int | None = Field(default=None, ge=8, le=100_000)
    batch_size: int | None = Field(default=None, ge=1, le=512)
    background: str | None = Field(
        default=None, description="'plate', 'solid' or 'none'."
    )
    pretrained: bool | None = None
    seed: int | None = Field(default=None, ge=0)


class SymbolPrediction(BaseModel):
    """One candidate for a tile, with the probability the model gave it."""

    symbol: str
    label: str = Field(description="Display name from the game config, or the code.")
    confidence: float = Field(ge=0.0, le=1.0)


class ClassifiedTile(BaseModel):
    """One tile of the split, named or explicitly not."""

    name: str = Field(description="Grid position, e.g. 'r1c1'.")
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    symbol: str | None = Field(
        description="The code, or null when nothing cleared the confidence floor."
    )
    label: str = Field(description="Display name, or 'unknown'.")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Probability of the leading candidate."
    )
    known: bool = Field(description="Whether the leading candidate cleared the floor.")
    predictions: list[SymbolPrediction] = Field(
        default_factory=list,
        description=(
            "Candidates ranked by probability, highest first. Shown for every "
            "tile: on a named one they show how far ahead the winner was, and on "
            "a rejected one they are the whole evidence for the rejection -- also "
            "what the dashboard shows in its place."
        ),
    )
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    image_data: str | None = Field(
        default=None, description="The tile itself as a data URI, when asked for."
    )


class ClassifyRequest(BaseModel):
    """Which split to read and how strictly to name its tiles."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {},
                {"split": "2026-08-24_14-16-16_002_spin-stop", "min_confidence": 0.8},
            ]
        },
    )

    split: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Split directory name; omit for the newest.",
    )
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=1, le=20)
    include_images: bool = True
    include_overlay: bool = True


class ClassifyResult(BaseModel):
    """Every tile of one split, named."""

    game: str
    split: str = Field(description="Split directory the tiles were read from.")
    source_frame: RoiFrame | None = Field(
        default=None, description="The screenshot that split came from, if readable."
    )
    rows: int = Field(ge=1)
    columns: int = Field(ge=1)
    min_confidence: float = Field(ge=0.0, le=1.0)
    model: ModelSummary
    symbol_grid: list[list[str | None]] = Field(default_factory=list)
    label_grid: list[list[str | None]] = Field(
        default_factory=list,
        description=(
            "The same matrix in display names rather than codes -- computed here "
            "rather than in the dashboard so the two grids cannot disagree. Null "
            "wherever 'symbol_grid' is null."
        ),
    )
    tiles: list[ClassifiedTile] = Field(default_factory=list)
    named: int = Field(ge=0, description="Tiles that cleared the floor.")
    unknown: int = Field(ge=0, description="Tiles that did not.")
    summary: str
    output_dir: str | None = None
    overlay_file: str | None = None
    overlay_image: str | None = None


class SplitSummary(BaseModel):
    """One split on disk that could be classified."""

    name: str
    written_at: datetime
    rows: int = Field(ge=0)
    columns: int = Field(ge=0)
    tiles: int = Field(ge=0)


class SplitCatalog(BaseModel):
    """Which splits exist and which one a request with no name would read."""

    splits: list[SplitSummary] = Field(default_factory=list)
    latest: str | None = None
    error: str | None = None
