"""Naming the symbol on a reel tile, from the picture rather than from the log."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

from app.config.game_config import GameConfigError, load_game_config
from app.config.runtime import settings
from app.exceptions.base import (
    BadRequestError,
    ClassifierAlreadyTrainingError,
    ClassifierDatasetNotFoundError,
    ClassifierNotTrainingError,
    ClassifierPredictFailedError,
    ClassifierTrainFailedError,
    ClassifierUnavailableError,
    ClassifierUntrainedError,
)
from app.schemas.image_classifier import (
    ArchitectureOption,
    ClassifiedTile,
    ClassifierMetrics,
    ClassifierState,
    ClassifierStatus,
    ClassifyRequest,
    ClassifyResult,
    DatasetClass,
    DatasetSummary,
    ModelSummary,
    SplitCatalog,
    SplitSummary,
    SymbolPrediction,
    TrainingEpoch,
    TrainingRun,
    TrainingRunState,
    TrainingStage,
    TrainingStageState,
    TrainRequest,
)
from app.services import grid as grid_service
from app.services import roi as roi_service
from app.utils import symbol_dataset, symbol_overlay

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

logger = logging.getLogger(__name__)

__all__ = [
    "abort",
    "cancel",
    "classify",
    "dataset",
    "reset",
    "splits",
    "status",
    "train",
]

# Where a classification's annotated picture goes: inside the split it read, one
# file per split, replacing its own record -- the convention the payline check
# already follows with `paylines/<set>.png`. Re-running at a different floor
# should overwrite its own answer, not accumulate them.
_OUTPUT_DIR = "classifier"
_OVERLAY_FILE = "symbols.png"

# Every stage of a run, in order, created `pending` up front so a failure part
# way through leaves the rest visibly unreached rather than absent.
_STAGES: tuple[tuple[str, str], ...] = (
    ("prepare", "Read the training images"),
    ("head", "Train the classifier head"),
    ("finetune", "Fine-tune the whole network"),
    ("evaluate", "Measure what it learned"),
    ("save", "Write the checkpoint"),
)

# How many epochs to keep on a run record. Enough for the whole default
# schedule several times over; a bound rather than a page size.
_MAX_EPOCHS_KEPT = 200

_UNKNOWN_LABEL = "unknown"


# --- state ----------------------------------------------------------------
# Module-level singletons, like every other service here. The lock is built
# lazily because one made at import time binds to the wrong event loop in tests.

_run: _TrainingRunRecord | None = None
# Keyed by architecture: both networks can be trained and kept, so both can be
# loaded, and a request naming one must not evict the other.
_checkpoints: dict[str, Any] = {}
_checkpoint_keys: dict[str, tuple[str, int, int]] = {}
_checkpoint_errors: dict[str, str] = {}
_dataset_cache: tuple[str, DatasetSummary] | None = None
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


@dataclass
class _StageRecord:
    """The mutable twin of :class:`TrainingStage`."""

    key: str
    label: str
    state: TrainingStageState = TrainingStageState.PENDING
    detail: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None

    def as_payload(self) -> TrainingStage:
        duration = None
        if self.started_at is not None and self.finished_at is not None:
            duration = int((self.finished_at - self.started_at).total_seconds() * 1000)
        return TrainingStage(
            key=self.key,
            label=self.label,
            state=self.state,
            detail=self.detail,
            started_at=self.started_at,
            finished_at=self.finished_at,
            duration_ms=duration,
            error=self.error,
        )


@dataclass
class _TrainingRunRecord:
    """One training run, mutated in place as it progresses."""

    run_id: str
    architecture: str
    started_at: datetime
    epoch_total: int
    estimated_seconds: float
    stages: list[_StageRecord]
    state: TrainingRunState = TrainingRunState.RUNNING
    message: str = "Starting"
    finished_at: datetime | None = None
    epochs: list[TrainingEpoch] = field(default_factory=list)
    metrics: ClassifierMetrics | None = None
    error: str | None = None
    error_code: str | None = None
    cancel_requested: bool = False
    task: asyncio.Task[None] | None = None

    def stage(self, key: str) -> _StageRecord | None:
        return next((entry for entry in self.stages if entry.key == key), None)

    def as_payload(self) -> TrainingRun:
        duration = None
        if self.finished_at is not None:
            duration = int((self.finished_at - self.started_at).total_seconds() * 1000)
        finished = len(self.epochs)
        return TrainingRun(
            run_id=self.run_id,
            architecture=self.architecture,
            state=self.state,
            message=self.message,
            started_at=self.started_at,
            finished_at=self.finished_at,
            duration_ms=duration,
            stages=[entry.as_payload() for entry in self.stages],
            epochs=list(self.epochs),
            epoch=finished,
            epoch_total=self.epoch_total,
            progress=(
                min(1.0, finished / self.epoch_total) if self.epoch_total else 0.0
            ),
            estimated_seconds=self.estimated_seconds,
            cancel_requested=self.cancel_requested,
            metrics=self.metrics,
            error=self.error,
            error_code=self.error_code,
        )


def reset() -> None:
    """Drop the cached model and any run record. Tests only."""
    global _run, _dataset_cache, _lock
    _run = None
    _checkpoints.clear()
    _checkpoint_keys.clear()
    _checkpoint_errors.clear()
    _dataset_cache = None
    _lock = None


async def abort() -> None:
    """Stop a run and forget everything. Called from the lifespan and by tests."""
    global _run
    run, _run = _run, None
    if run is not None and run.task is not None:
        run.cancel_requested = True
        task = run.task
        if not task.done():
            task.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.gather(task, return_exceptions=True)
    reset()


# --- the engine -----------------------------------------------------------


def _import_model() -> Any:
    """The torch-backed module, or ``None`` when the stack is not installed."""
    try:
        from app.utils import symbol_model
    except ImportError:
        return None
    return symbol_model


def _require_model() -> Any:
    if not settings.CLASSIFIER_ENABLED:
        raise ClassifierUnavailableError(
            "The image classifier is disabled; set CLASSIFIER_ENABLED=true to turn "
            "it on"
        )
    module = _import_model()
    if module is None:
        raise ClassifierUnavailableError(
            "torch and torchvision are not installed. Install them with "
            "`pip install -r requirements.txt` from backend/"
        )
    return module


def _versions() -> tuple[str | None, str | None]:
    try:
        import torch
        import torchvision
    except ImportError:
        return None, None
    return str(torch.__version__), str(torchvision.__version__)


# --- the active game ------------------------------------------------------


def _active_game() -> tuple[str, dict[str, str]]:
    """The selected game and its symbol display names."""
    try:
        name = settings.ideck_active_game
    except Exception:  # noqa: BLE001 -- an unreadable selection is not fatal here
        return "", {}
    try:
        config = load_game_config(settings.ideck_game_config_path_for(name))
    except (GameConfigError, OSError, ValueError):
        return name, {}
    return name, dict(config.symbols)


def _label(symbol: str, names: dict[str, str]) -> str:
    return names.get(symbol, symbol)


# --- the dataset ----------------------------------------------------------


def _build_dataset(names: dict[str, str]) -> DatasetSummary:
    root = settings.classifier_dataset_dir
    if not root.is_dir():
        return DatasetSummary(
            directory=str(root),
            exists=False,
            error=(
                f"No training images at {root}. Point CLASSIFIER_DATASET_DIR at a "
                "directory holding one folder per symbol code."
            ),
            warnings=["There is nothing to train on."],
        )

    try:
        summaries = symbol_dataset.summarise(root)
    except (symbol_dataset.DatasetError, OSError) as exc:
        return DatasetSummary(directory=str(root), exists=True, error=str(exc))

    classes = [
        DatasetClass(
            symbol=entry.symbol,
            label=_label(entry.symbol, names),
            count=entry.count,
            widths=list(entry.widths),
            heights=list(entry.heights),
            composited=entry.dense == entry.count and entry.count > 0,
            mean_opacity=entry.mean_opacity,
        )
        for entry in summaries
    ]
    singles = [entry.symbol for entry in classes if entry.count == 1]
    present = {entry.symbol for entry in classes}
    missing = sorted(code for code in names if code not in present)

    warnings: list[str] = []
    if not classes:
        warnings.append("There is nothing to train on.")
    if singles:
        warnings.append(
            f"{len(singles)} of {len(classes)} classes hold a single picture "
            f"({', '.join(singles)}). They cannot be validated, and a symbol seen "
            "once is the weakest part of the model."
        )
    if missing:
        warnings.append(
            f"{len(missing)} symbol codes the game declares have no artwork here "
            f"({', '.join(missing)}). Every one will eventually be on screen and "
            "can only come back as another symbol or as unknown, which is what the "
            "confidence floor is for."
        )
    if any(entry.count > 1 and not entry.composited for entry in classes):
        warnings.append(
            "Some classes are transparent cut-outs, so a reel-cell background is "
            "synthesised behind them before training. Check "
            f"{settings.classifier_sample_dir} if accuracy disappoints."
        )

    return DatasetSummary(
        directory=str(root),
        exists=True,
        classes=classes,
        total_images=sum(entry.count for entry in classes),
        fingerprint=symbol_dataset.fingerprint(root),
        single_image_classes=singles,
        missing_symbols=missing,
        warnings=warnings,
    )


def _dataset(names: dict[str, str]) -> DatasetSummary:
    """The dataset summary, cached on the dataset's own fingerprint."""
    global _dataset_cache
    root = settings.classifier_dataset_dir
    key = symbol_dataset.fingerprint(root)
    cached = _dataset_cache
    if cached is not None and cached[0] == key:
        return cached[1]
    summary = _build_dataset(names)
    _dataset_cache = (key, summary)
    return summary


async def dataset() -> DatasetSummary:
    """What is available to train on. Never fails."""
    _, names = _active_game()
    return await asyncio.to_thread(_dataset, names)


# --- the checkpoint -------------------------------------------------------


def _checkpoint_stat(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), int(stat.st_mtime), int(stat.st_size))


def resolve_architecture(requested: str | None) -> str:
    """The architecture a request meant, defaulting to the configured one."""
    module = _import_model()
    known = tuple(module.ARCHITECTURES) if module is not None else ()
    name = requested or settings.CLASSIFIER_ARCHITECTURE
    if known and name not in known:
        raise BadRequestError(
            f"Unknown architecture {name!r}; expected one of {', '.join(known)}"
        )
    return name


def _load_checkpoint(architecture: str | None = None) -> Any | None:
    """One architecture's trained model, cached on the file's mtime and size."""
    module = _import_model()
    if module is None:
        return None
    name = architecture or settings.CLASSIFIER_ARCHITECTURE
    path = settings.classifier_checkpoint_for(name)
    key = _checkpoint_stat(path)
    if key is None:
        _checkpoints.pop(name, None)
        _checkpoint_keys.pop(name, None)
        _checkpoint_errors.pop(name, None)
        return None
    if _checkpoint_keys.get(name) == key and name in _checkpoints:
        return _checkpoints[name]
    try:
        loaded = module.load(path)
    except module.ModelError as exc:
        _checkpoints.pop(name, None)
        _checkpoint_keys[name] = key
        _checkpoint_errors[name] = str(exc)
        return None
    _checkpoints[name] = loaded
    _checkpoint_keys[name] = key
    _checkpoint_errors.pop(name, None)
    return loaded


def _metrics_payload(raw: dict[str, Any]) -> ClassifierMetrics:
    """A checkpoint's stored metrics, tolerating one written by an older build."""
    allowed = set(ClassifierMetrics.model_fields)
    return ClassifierMetrics.model_validate(
        {key: value for key, value in raw.items() if key in allowed}
    )


def _model_summary(loaded: Any, fingerprint: str, expected: int) -> ModelSummary:
    return ModelSummary(
        path=str(loaded.path),
        architecture=loaded.architecture,
        label=loaded.label,
        trained_at=loaded.trained_at,
        classes=list(loaded.classes),
        image_size=loaded.image_size,
        transform_version=loaded.transform_version,
        expected_transform_version=expected,
        dataset_fingerprint=loaded.dataset_fingerprint,
        dataset_changed=bool(
            fingerprint
            and loaded.dataset_fingerprint
            and fingerprint != loaded.dataset_fingerprint
        ),
        metrics=_metrics_payload(loaded.metrics),
    )


# --- status ---------------------------------------------------------------


def _architecture_options(module: Any) -> list[ArchitectureOption]:
    """Every network that can be fitted, and whether one is already trained."""
    default = settings.CLASSIFIER_ARCHITECTURE
    options: list[ArchitectureOption] = []
    for name in module.ARCHITECTURES:
        loaded = _load_checkpoint(name)
        metrics = _metrics_payload(loaded.metrics) if loaded is not None else None
        options.append(
            ArchitectureOption(
                name=name,
                label=module.architecture_label(name),
                trained=loaded is not None,
                is_default=name == default,
                trained_at=loaded.trained_at if loaded is not None else None,
                holdout_accuracy=(
                    metrics.frame_holdout_accuracy if metrics is not None else None
                ),
                detail=_checkpoint_errors.get(name),
            )
        )
    return options


def _status() -> ClassifierStatus:
    _, names = _active_game()
    summary = _dataset(names)
    threads = settings.classifier_torch_threads
    floor = settings.CLASSIFIER_MIN_CONFIDENCE
    torch_version, torchvision_version = _versions()

    if not settings.CLASSIFIER_ENABLED:
        return ClassifierStatus(
            state=ClassifierState.DISABLED,
            detail="The classifier is disabled; set CLASSIFIER_ENABLED=true.",
            threads=threads,
            min_confidence=floor,
            architecture=settings.CLASSIFIER_ARCHITECTURE,
            dataset=summary,
            training=_run.as_payload() if _run is not None else None,
            active=False,
        )

    module = _import_model()
    if module is None:
        return ClassifierStatus(
            state=ClassifierState.NOT_INSTALLED,
            detail=(
                "torch and torchvision are not installed. Run "
                "`pip install -r requirements.txt` from backend/."
            ),
            threads=threads,
            min_confidence=floor,
            architecture=settings.CLASSIFIER_ARCHITECTURE,
            dataset=summary,
            training=_run.as_payload() if _run is not None else None,
            active=False,
        )

    run = _run.as_payload() if _run is not None else None
    active = _run is not None and _run.state is TrainingRunState.RUNNING
    options = _architecture_options(module)
    loaded = _load_checkpoint()
    model = (
        _model_summary(loaded, summary.fingerprint, module.TRANSFORM_VERSION)
        if loaded is not None
        else None
    )

    if active:
        state = ClassifierState.TRAINING
        detail = "A training run is in progress."
    elif loaded is None and settings.CLASSIFIER_ARCHITECTURE in _checkpoint_errors:
        state = ClassifierState.ERROR
        detail = _checkpoint_errors[settings.CLASSIFIER_ARCHITECTURE]
    elif loaded is None:
        state = ClassifierState.UNTRAINED
        trained = [option.label for option in options if option.trained]
        detail = (
            f"{module.architecture_label(settings.CLASSIFIER_ARCHITECTURE)} has not "
            "been trained yet. Press Train to fit it"
            + (f", or classify with {' or '.join(trained)}." if trained else ".")
        )
    elif loaded.stale:
        state = ClassifierState.STALE
        detail = (
            "This model was trained under an older preprocessing pipeline "
            f"(version {loaded.transform_version}, this build uses "
            f"{module.TRANSFORM_VERSION}) and would not predict the way it was "
            "measured. Train again."
        )
    elif model is not None and model.dataset_changed:
        state = ClassifierState.READY
        detail = (
            "The training images have changed since this model was fitted; its "
            "accuracy figures describe the older set."
        )
    else:
        state = ClassifierState.READY
        detail = None

    return ClassifierStatus(
        state=state,
        detail=detail,
        torch_version=torch_version,
        torchvision_version=torchvision_version,
        threads=threads,
        min_confidence=floor,
        architecture=settings.CLASSIFIER_ARCHITECTURE,
        architectures=options,
        model=model,
        dataset=summary,
        training=run,
        active=active,
    )


async def status() -> ClassifierStatus:
    """Whether a tile can be named right now. Never fails -- a missing engine is
    a state, exactly as with OBS and OCR."""
    return await asyncio.to_thread(_status)


# --- training -------------------------------------------------------------


def _plan(request: TrainRequest) -> dict[str, Any]:
    """Settings for one run, with the request's overrides applied."""

    def pick(value: Any, default: Any) -> Any:
        return default if value is None else value

    return {
        "epochs_head": pick(request.epochs_head, settings.CLASSIFIER_EPOCHS_HEAD),
        "epochs_finetune": pick(
            request.epochs_finetune, settings.CLASSIFIER_EPOCHS_FINETUNE
        ),
        "samples_per_epoch": pick(
            request.samples_per_epoch, settings.CLASSIFIER_SAMPLES_PER_EPOCH
        ),
        "batch_size": pick(request.batch_size, settings.CLASSIFIER_BATCH_SIZE),
        "background": pick(request.background, settings.CLASSIFIER_BACKGROUND),
        "pretrained": pick(request.pretrained, settings.CLASSIFIER_PRETRAINED),
        "seed": pick(request.seed, settings.CLASSIFIER_SEED),
        "architecture": resolve_architecture(request.architecture),
    }


def _begin_stage(run: _TrainingRunRecord, key: str) -> None:
    """Mark ``key`` running and everything before it done."""
    for entry in run.stages:
        if entry.key == key:
            if entry.state is TrainingStageState.PENDING:
                entry.state = TrainingStageState.RUNNING
                entry.started_at = datetime.now(UTC)
            run.message = entry.label
            return
        if entry.state is TrainingStageState.RUNNING:
            entry.state = TrainingStageState.COMPLETED
            entry.finished_at = datetime.now(UTC)


def _finish_stages(run: _TrainingRunRecord, state: TrainingStageState) -> None:
    """Close whatever is open, and mark anything unreached with ``state``."""
    now = datetime.now(UTC)
    for entry in run.stages:
        if entry.state is TrainingStageState.RUNNING:
            entry.state = TrainingStageState.COMPLETED
            entry.finished_at = now
        elif entry.state is TrainingStageState.PENDING:
            entry.state = state


def _train_blocking(run: _TrainingRunRecord, module: Any, plan: dict[str, Any]) -> Any:
    """The whole fit, on a worker thread."""

    def on_epoch(record: Any) -> None:
        if len(run.epochs) < _MAX_EPOCHS_KEPT:
            run.epochs.append(
                TrainingEpoch(
                    stage=record.stage,
                    epoch=record.epoch,
                    epochs=record.epochs,
                    loss=record.loss,
                    accuracy=record.accuracy,
                    seconds=record.seconds,
                )
            )
        run.message = (
            f"{record.stage} epoch {record.epoch}/{record.epochs} — "
            f"accuracy {record.accuracy:.0%}"
        )

    def on_stage(key: str) -> None:
        _begin_stage(run, key)

    return module.train(
        dataset_dir=settings.classifier_dataset_dir,
        checkpoint_path=settings.classifier_checkpoint_for(plan["architecture"]),
        image_size=settings.CLASSIFIER_IMAGE_SIZE,
        lr_head=settings.CLASSIFIER_LR_HEAD,
        lr_finetune=settings.CLASSIFIER_LR_FINETUNE,
        holdout_frames=settings.CLASSIFIER_HOLDOUT_FRAMES,
        threads=settings.classifier_torch_threads,
        sample_dir=settings.classifier_sample_dir,
        on_epoch=on_epoch,
        on_stage=on_stage,
        should_cancel=lambda: run.cancel_requested,
        **plan,
    )


async def _watch(run: _TrainingRunRecord, module: Any, plan: dict[str, Any]) -> None:
    """Run the fit and record how it ended. Never raises."""
    architecture = str(plan["architecture"])
    try:
        result = await asyncio.to_thread(_train_blocking, run, module, plan)
    except module.TrainingCancelled:
        run.state = TrainingRunState.CANCELLED
        run.message = "Cancelled"
        _finish_stages(run, TrainingStageState.SKIPPED)
    except asyncio.CancelledError:
        run.state = TrainingRunState.CANCELLED
        run.message = "Cancelled at shutdown"
        _finish_stages(run, TrainingStageState.SKIPPED)
        raise
    except (module.ModelError, symbol_dataset.DatasetError, OSError, ValueError) as exc:
        run.state = TrainingRunState.FAILED
        run.message = "Training failed"
        run.error = str(exc)
        run.error_code = ClassifierTrainFailedError.error_code
        _finish_stages(run, TrainingStageState.SKIPPED)
        stage = next(
            (
                entry
                for entry in run.stages
                if entry.state is TrainingStageState.SKIPPED
            ),
            None,
        )
        if stage is not None:
            stage.state = TrainingStageState.FAILED
            stage.error = str(exc)
        logger.warning("Classifier training failed: %s", exc)
    except Exception as exc:
        run.state = TrainingRunState.FAILED
        run.message = "Training failed"
        run.error = str(exc)
        run.error_code = ClassifierTrainFailedError.error_code
        _finish_stages(run, TrainingStageState.SKIPPED)
        logger.exception("Classifier training raised")
    else:
        run.state = TrainingRunState.COMPLETED
        run.metrics = _metrics_payload(_as_dict(result.metrics))
        run.message = _outcome_message(run.metrics)
        _finish_stages(run, TrainingStageState.COMPLETED)
        # Drop only this architecture's cached model, so the very next classify
        # uses what was just fitted -- and the other network's model, which this run
        # did not touch, stays loaded.
        _checkpoints.pop(architecture, None)
        _checkpoint_keys.pop(architecture, None)
    finally:
        run.finished_at = datetime.now(UTC)


def _as_dict(metrics: Any) -> dict[str, Any]:
    from dataclasses import asdict, is_dataclass

    if is_dataclass(metrics) and not isinstance(metrics, type):
        return asdict(metrics)
    return dict(metrics)


def _outcome_message(metrics: ClassifierMetrics | None) -> str:
    if metrics is None:
        return "Trained"
    if metrics.frame_holdout_accuracy is None:
        return (
            f"Trained {len(metrics.classes)} symbols; "
            f"{metrics.augmented_accuracy:.0%} on re-augmented training pictures "
            "(no frames could be held back)"
        )
    return (
        f"Trained {len(metrics.classes)} symbols; "
        f"{metrics.frame_holdout_accuracy:.0%} on {metrics.frame_holdout_samples} "
        f"held-back frames, {metrics.augmented_accuracy:.0%} augmented"
    )


async def train(request: TrainRequest | None = None) -> TrainingRun:
    """Start a training run, returning as soon as it is under way."""
    global _run
    module = _require_model()
    plan = _plan(request or TrainRequest())

    root = settings.classifier_dataset_dir
    if not root.is_dir():
        raise ClassifierDatasetNotFoundError(
            f"No training images at {root}. Point CLASSIFIER_DATASET_DIR at a "
            "directory holding one folder per symbol code."
        )

    async with _get_lock():
        if _run is not None and _run.state is TrainingRunState.RUNNING:
            raise ClassifierAlreadyTrainingError(
                f"Training run {_run.run_id} is already in progress"
            )
        epochs = int(plan["epochs_head"]) + int(plan["epochs_finetune"])
        run = _TrainingRunRecord(
            run_id=uuid.uuid4().hex[:12],
            architecture=str(plan["architecture"]),
            started_at=datetime.now(UTC),
            epoch_total=epochs,
            estimated_seconds=module.estimate_seconds(
                epochs, int(plan["samples_per_epoch"]), int(plan["batch_size"])
            ),
            stages=[
                _StageRecord(
                    key=key,
                    label=label,
                    state=(
                        TrainingStageState.SKIPPED
                        if (key == "head" and not plan["epochs_head"])
                        or (key == "finetune" and not plan["epochs_finetune"])
                        else TrainingStageState.PENDING
                    ),
                )
                for key, label in _STAGES
            ],
        )
        _run = run
        run.task = asyncio.create_task(
            _watch(run, module, plan), name=f"classifier-train-{run.run_id}"
        )
    return run.as_payload()


async def cancel() -> TrainingRun:
    """Ask a run to stop. The flag is read between batches, never a task.cancel."""
    run = _run
    if run is None or run.state is not TrainingRunState.RUNNING:
        raise ClassifierNotTrainingError
    run.cancel_requested = True
    run.message = "Cancelling"
    return run.as_payload()


# --- splits ---------------------------------------------------------------


def _splits() -> SplitCatalog:
    try:
        latest = grid_service.latest_split()
    except Exception:  # noqa: BLE001 -- an unreadable output dir is not an error
        return SplitCatalog(error="The grid output directory could not be read.")
    root = settings.obs_capture_dir / "grid"
    if not root.is_dir():
        return SplitCatalog(error="Nothing has been split yet; split the reels first.")
    found: list[SplitSummary] = []
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        tiles = directory / "tiles"
        names = (
            [entry.name for entry in tiles.iterdir() if entry.is_file()]
            if tiles.is_dir()
            else []
        )
        rows = columns = 0
        for name in names:
            match = grid_service._TILE_POSITION.search(name)
            if match:
                rows = max(rows, int(match.group(1)))
                columns = max(columns, int(match.group(2)))
        try:
            written = datetime.fromtimestamp(directory.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        found.append(
            SplitSummary(
                name=directory.name,
                written_at=written,
                rows=rows,
                columns=columns,
                tiles=len(names),
            )
        )
    found.sort(key=lambda entry: entry.written_at, reverse=True)
    return SplitCatalog(
        splits=found,
        latest=latest.name if latest is not None else None,
        error=None if found else "Nothing has been split yet.",
    )


async def splits() -> SplitCatalog:
    """Which splits exist and which one a request with no name would read."""
    return await asyncio.to_thread(_splits)


# --- classifying ----------------------------------------------------------


def _position(name: str) -> tuple[int, int]:
    match = grid_service._TILE_POSITION.search(name)
    if match is None:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def _classify(request: ClassifyRequest, module: Any) -> ClassifyResult:
    game, names = _active_game()
    architecture = resolve_architecture(request.architecture)
    loaded = _load_checkpoint(architecture)
    if loaded is None:
        detail = _checkpoint_errors.get(architecture) or (
            f"{module.architecture_label(architecture)} has not been trained yet."
        )
        raise ClassifierUntrainedError(f"{detail} Train it before classifying.")

    split = grid_service.read_split(grid_service.resolve_split(request.split))
    floor = (
        settings.CLASSIFIER_MIN_CONFIDENCE
        if request.min_confidence is None
        else request.min_confidence
    )
    top_k = settings.CLASSIFIER_TOP_K if request.top_k is None else request.top_k

    ordered = sorted(split.tiles.items(), key=lambda item: _position(item[0]))
    images = [image for _, image in ordered]
    probabilities = module.predict(loaded, images, settings.classifier_torch_threads)

    tiles: list[ClassifiedTile] = []
    grid_rows: list[list[str | None]] = [
        [None] * split.columns for _ in range(split.rows)
    ]
    # The same matrix in display names. Built here rather than in the dashboard so
    # the two grids cannot end up disagreeing about which cell is which.
    label_rows: list[list[str | None]] = [
        [None] * split.columns for _ in range(split.rows)
    ]
    drawn: list[symbol_overlay.DrawnSymbol] = []

    for index, (name, image) in enumerate(ordered):
        row, column = _position(name)
        scores = probabilities[index]
        ranked = sorted(
            zip(loaded.classes, scores.tolist(), strict=True),
            key=lambda pair: pair[1],
            reverse=True,
        )
        best_symbol, best_score = ranked[0]
        known = best_score >= floor
        tiles.append(
            ClassifiedTile(
                name=name,
                row=row,
                column=column,
                symbol=best_symbol if known else None,
                label=_label(best_symbol, names) if known else _UNKNOWN_LABEL,
                confidence=best_score,
                known=known,
                predictions=[
                    SymbolPrediction(
                        symbol=symbol,
                        label=_label(symbol, names),
                        confidence=score,
                    )
                    for symbol, score in ranked[:top_k]
                ],
                width=image.width,
                height=image.height,
                image_data=(
                    roi_service.encode_png(image) if request.include_images else None
                ),
            )
        )
        if 1 <= row <= split.rows and 1 <= column <= split.columns and known:
            grid_rows[row - 1][column - 1] = best_symbol
            label_rows[row - 1][column - 1] = _label(best_symbol, names)
        drawn.append(symbol_overlay.DrawnSymbol(row=row, column=column, known=known))

    named = sum(1 for tile in tiles if tile.known)
    overlay_file: str | None = None
    overlay_image: str | None = None
    output_dir: str | None = None
    if request.include_overlay:
        overlay_file, overlay_image, output_dir = _write_overlay(split, drawn)

    frame: Any = None
    with contextlib.suppress(Exception):
        frame = roi_service.describe_path(
            roi_service.resolve_frame(f"{split.name}.png")
        )

    return ClassifyResult(
        game=game,
        split=split.name,
        source_frame=frame,
        rows=split.rows,
        columns=split.columns,
        min_confidence=floor,
        model=_model_summary(loaded, "", module.TRANSFORM_VERSION),
        symbol_grid=grid_rows,
        label_grid=label_rows,
        tiles=tiles,
        named=named,
        unknown=len(tiles) - named,
        summary=(
            f"{named} of {len(tiles)} tiles named; "
            f"{len(tiles) - named} below {floor:.2f}"
        ),
        output_dir=output_dir,
        overlay_file=overlay_file,
        overlay_image=overlay_image,
    )


def _write_overlay(
    split: grid_service.SplitOnDisk,
    drawn: list[symbol_overlay.DrawnSymbol],
) -> tuple[str | None, str | None, str | None]:
    """Draw and save the annotated reels; a failure here is not fatal."""
    try:
        picture = symbol_overlay.draw(
            split.crop,
            drawn,
            rows=split.rows,
            columns=split.columns,
            minimum_width=settings.PAYLINE_OVERLAY_MIN_WIDTH,
        )
    except (OSError, ValueError):
        return None, None, None

    directory = split.directory / _OUTPUT_DIR
    # Encoded whenever the overlay was asked for. Deliberately not gated on
    # `include_images`, which is about the fifteen per-tile pictures: the dashboard
    # shows this one and not those, so tying them together would mean paying for
    # fifteen data URIs to get the one that is rendered.
    encoded = roi_service.encode_png(picture)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        picture.save(directory / _OVERLAY_FILE)
    except OSError:
        return None, encoded, None
    return _OVERLAY_FILE, encoded, str(directory)


async def classify(request: ClassifyRequest | None = None) -> ClassifyResult:
    """Name every tile of a written split."""
    module = _require_model()
    payload = request or ClassifyRequest()
    try:
        return await asyncio.to_thread(_classify, payload, module)
    except module.ModelError as exc:
        raise ClassifierPredictFailedError(str(exc)) from exc


def tile_image(split: str | None, name: str) -> Image.Image:
    """One tile of a split, by position name -- for the raw-image endpoint."""
    directory = grid_service.resolve_split(split)
    return grid_service.read_split(directory).tiles[name.lower()]
