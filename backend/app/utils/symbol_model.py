"""EfficientNet-B0 over the pictures :mod:`app.utils.symbol_dataset` builds."""

from __future__ import annotations

import json
import math
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image
from torch import nn
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
from torchvision.models import (
    EfficientNet_B0_Weights,
    ResNet34_Weights,
    efficientnet_b0,
    resnet34,
)
from torchvision.transforms import v2

from app.utils import symbol_dataset

__all__ = [
    "ARCHITECTURES",
    "DEFAULT_ARCHITECTURE",
    "TRANSFORM_VERSION",
    "Checkpoint",
    "EpochRecord",
    "ModelError",
    "TrainResult",
    "TrainingCancelled",
    "configure_threads",
    "load",
    "predict",
    "train",
]

# Bumped whenever the preprocessing below changes in a way that makes an older
# checkpoint predict differently. It travels *on* the checkpoint, so a stale
# model announces itself instead of quietly being wrong.
TRANSFORM_VERSION = 2

# ImageNet statistics, because the backbone's weights were fitted against them.
_MEAN = (0.485, 0.456, 0.406)
_STD = (0.229, 0.224, 0.225)

# The measured span of real tile widths (61 to 128 across every written split),
# opened out a little at both ends.
_RESOLUTION_RANGE = (52, 140)

# Aspect ratios a training crop may take. Real tiles measure 1.03 to 1.45 wide
# for tall; the artwork is square.
_CROP_RATIO = (0.85, 1.6)
_CROP_SCALE = (0.65, 1.0)

# How much of a picture the evaluation transform keeps before scaling it to the
# input size -- the same slight zoom training's random crop applies on average. A
# correctness fix, not a spare knob: without it inference showed the network the
# whole picture while training had only ever shown it zoomed crops, and DD scored
# 0.42 on pictures it had trained on versus 1.00 with a 10% centre zoom. Has to stay
# paired with _CROP_SCALE, which is what TRANSFORM_VERSION exists to catch.
_EVAL_CROP = 0.90


# The networks that can be fitted, and where each keeps its classifier head. Both
# take the same input through the same transforms, so only the backbone differs and
# adding a third is one entry here rather than a second code path. `head` is the
# parameter-name prefix `_freeze` keeps trainable during the first stage -- get it
# wrong and stage one silently trains nothing, which is why it lives beside the
# builder that replaces that exact layer.
_ARCHITECTURES: dict[str, dict[str, Any]] = {
    "efficientnet_b0": {
        "label": "EfficientNet-B0",
        "head": "classifier",
        "weights": EfficientNet_B0_Weights.IMAGENET1K_V1,
    },
    "resnet34": {
        "label": "ResNet34",
        "head": "fc",
        "weights": ResNet34_Weights.IMAGENET1K_V1,
    },
}

ARCHITECTURES: tuple[str, ...] = tuple(_ARCHITECTURES)
DEFAULT_ARCHITECTURE = "efficientnet_b0"


def architecture_label(architecture: str) -> str:
    """Display name for an architecture, or the key itself if it is unknown."""
    entry = _ARCHITECTURES.get(architecture)
    return str(entry["label"]) if entry else architecture


class ModelError(Exception):
    """The model could not be built, trained, saved or loaded."""


class TrainingCancelled(Exception):
    """A caller's cancellation predicate returned true mid-run."""


@dataclass(frozen=True)
class EpochRecord:
    """One epoch, as the dashboard shows it."""

    stage: str
    epoch: int
    epochs: int
    loss: float
    accuracy: float
    seconds: float


@dataclass(frozen=True)
class Metrics:
    """What a finished run measured, and how much each number is worth."""

    classes: list[str]
    architecture: str
    train_accuracy: float
    train_loss: float
    frame_holdout_accuracy: float | None
    frame_holdout_samples: int
    frame_holdout_classes: list[str]
    frame_holdout_leakage: float
    augmented_accuracy: float
    augmented_samples: int
    per_class_holdout: dict[str, float] = field(default_factory=dict)
    per_class_augmented: dict[str, float] = field(default_factory=dict)
    single_image_classes: list[str] = field(default_factory=list)
    epochs: list[EpochRecord] = field(default_factory=list)
    seconds: float = 0.0


@dataclass(frozen=True)
class TrainResult:
    """A finished run: where the model went and what it scored."""

    checkpoint: Path
    metrics: Metrics


@dataclass(frozen=True)
class Checkpoint:
    """A loaded model, with everything needed to preprocess exactly as trained."""

    model: nn.Module
    classes: list[str]
    image_size: int
    architecture: str
    transform_version: int
    dataset_fingerprint: str
    trained_at: str
    metrics: dict[str, Any]
    path: Path

    @property
    def label(self) -> str:
        """Display name of the network these weights are for."""
        return architecture_label(self.architecture)

    @property
    def stale(self) -> bool:
        """Whether this checkpoint predates the current preprocessing."""
        return self.transform_version != TRANSFORM_VERSION


def configure_threads(threads: int) -> None:
    """Cap torch's thread pool."""
    torch.set_num_threads(max(1, threads))


def _resolution_jitter(image: Image.Image, rng: random.Random) -> Image.Image:
    """Knock a picture down to a real tile's resolution and back up again."""
    target = rng.randint(*_RESOLUTION_RANGE)
    if target >= min(image.size):
        return image
    ratio = image.height / image.width if image.width else 1.0
    small = image.resize(
        (target, max(1, round(target * ratio))), Image.Resampling.BILINEAR
    )
    return small.resize(image.size, Image.Resampling.BICUBIC)


def _train_transform(size: int) -> v2.Compose:
    return v2.Compose(
        [
            v2.RandomResizedCrop(
                size, scale=_CROP_SCALE, ratio=_CROP_RATIO, antialias=True
            ),
            v2.RandomRotation(6),
            v2.ColorJitter(brightness=0.35, contrast=0.35, saturation=0.25, hue=0.03),
            v2.RandomApply([v2.GaussianBlur(3, sigma=(0.1, 1.2))], p=0.3),
            v2.ToImage(),
            v2.ToDtype(torch.float32, scale=True),
            v2.Normalize(mean=_MEAN, std=_STD),
            v2.RandomErasing(p=0.15, scale=(0.02, 0.12)),
        ]
    )


def _eval_transform(size: int) -> v2.Compose:
    """The counterpart of :func:`_train_transform`, and it must stay one."""
    outer = max(size + 1, round(size / _EVAL_CROP))
    return v2.Compose(
        [
            v2.Resize((outer, outer), antialias=True),
            v2.CenterCrop(size),
            v2.ToImage(),
            v2.ToDtype(torch.float32, scale=True),
            v2.Normalize(mean=_MEAN, std=_STD),
        ]
    )


class _Samples(Dataset[tuple[torch.Tensor, int]]):
    """Composed pictures, synthesised per access rather than pre-rendered."""

    def __init__(
        self,
        classes: Sequence[symbol_dataset.ClassSources],
        indices: Sequence[Sequence[int]],
        size: int,
        style: str,
        seed: int,
        *,
        augment: bool,
    ) -> None:
        self.size = size
        self.style = style
        self.augment = augment
        self.seed = seed
        self.transform = _train_transform(size) if augment else _eval_transform(size)
        self.items: list[tuple[int, symbol_dataset.SymbolSource]] = []
        for label, (entry, chosen) in enumerate(zip(classes, indices, strict=True)):
            for index in chosen:
                self.items.append((label, entry.sources[index]))
        self.labels = [label for label, _ in self.items]

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int]:
        label, source = self.items[index]
        # A fixed seed per item when not augmenting, so an evaluation run is the
        # same pictures every time and two runs are comparable.
        rng = random.Random(
            self.seed + index if not self.augment else random.randrange(2**32)
        )
        picture = symbol_dataset.compose(source, self.size, rng, style=self.style)
        if self.augment:
            picture = _resolution_jitter(picture, rng)
        tensor: torch.Tensor = self.transform(picture)
        return tensor, label


def _build(classes: int, pretrained: bool, architecture: str) -> nn.Module:
    entry = _ARCHITECTURES.get(architecture)
    if entry is None:
        raise ModelError(
            f"unknown architecture {architecture!r}; expected one of "
            f"{', '.join(ARCHITECTURES)}"
        )
    try:
        weights = entry["weights"] if pretrained else None
        if architecture == "resnet34":
            model = resnet34(weights=weights)
            model.fc = nn.Linear(model.fc.in_features, classes)
        else:
            model = efficientnet_b0(weights=weights)
            model.classifier[1] = nn.Linear(model.classifier[1].in_features, classes)
    except Exception as exc:
        raise ModelError(
            f"{entry['label']}'s pretrained weights could not be loaded. They are a "
            "20-90MB download from download.pytorch.org on first use; set "
            "CLASSIFIER_PRETRAINED=false to train without them, or TORCH_HOME to a "
            f"cache that already has them. ({exc})"
        ) from exc
    # Annotated rather than returned bare: torchvision ships no py.typed, so the
    # builders are Any and strict mypy will not infer a Module from them.
    built: nn.Module = model
    return built


def _freeze(model: nn.Module, architecture: str, *, frozen: bool) -> None:
    """Freeze everything but the head, or nothing at all."""
    head = str(_ARCHITECTURES.get(architecture, {}).get("head", "classifier"))
    for name, parameter in model.named_parameters():
        parameter.requires_grad = not frozen or name.startswith(head)


def _sampler(labels: Sequence[int], samples: int, seed: int) -> WeightedRandomSampler:
    """Draw every class equally often, whatever the file counts say."""
    counts = np.bincount(labels)
    weights = [1.0 / float(counts[label]) for label in labels]
    generator = torch.Generator().manual_seed(seed)
    return WeightedRandomSampler(
        weights=weights, num_samples=samples, replacement=True, generator=generator
    )


def _accuracy(
    model: nn.Module,
    dataset: _Samples,
    batch_size: int,
    should_cancel: Callable[[], bool],
) -> tuple[float, dict[str, int], dict[str, int]]:
    """Accuracy over a dataset, plus right/total per class label index."""
    if len(dataset) == 0:
        return 0.0, {}, {}
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=0)
    model.eval()
    correct = 0
    total = 0
    right: dict[str, int] = {}
    seen: dict[str, int] = {}
    with torch.inference_mode():
        for images, labels in loader:
            if should_cancel():
                raise TrainingCancelled
            predicted = model(images).argmax(dim=1)
            for label, guess in zip(labels.tolist(), predicted.tolist(), strict=True):
                key = str(label)
                seen[key] = seen.get(key, 0) + 1
                if label == guess:
                    right[key] = right.get(key, 0) + 1
            correct += int((predicted == labels).sum().item())
            total += int(labels.numel())
    return (correct / total if total else 0.0), right, seen


def _per_class(
    names: Sequence[str], right: dict[str, int], seen: dict[str, int]
) -> dict[str, float]:
    return {
        names[int(key)]: right.get(key, 0) / count
        for key, count in sorted(seen.items(), key=lambda item: int(item[0]))
        if count
    }


def _run_stage(
    model: nn.Module,
    loader: DataLoader[tuple[torch.Tensor, int]],
    optimiser: torch.optim.Optimizer,
    criterion: nn.Module,
    scheduler: torch.optim.lr_scheduler.LRScheduler | None,
    *,
    stage: str,
    epochs: int,
    on_epoch: Callable[[EpochRecord], None],
    should_cancel: Callable[[], bool],
) -> tuple[float, float]:
    """Train for ``epochs``, reporting each one. Returns the last loss/accuracy."""
    loss_value = 0.0
    accuracy = 0.0
    for epoch in range(1, epochs + 1):
        started = time.monotonic()
        model.train()
        running = 0.0
        correct = 0
        total = 0
        for images, labels in loader:
            # Checked per batch rather than per epoch: an epoch is ~20s and a
            # cancel the operator has to wait 20s for reads as a hang.
            if should_cancel():
                raise TrainingCancelled
            optimiser.zero_grad(set_to_none=True)
            outputs = model(images)
            loss = criterion(outputs, labels)
            loss.backward()
            optimiser.step()
            running += float(loss.item()) * int(labels.numel())
            correct += int((outputs.argmax(dim=1) == labels).sum().item())
            total += int(labels.numel())
        if scheduler is not None:
            scheduler.step()
        loss_value = running / total if total else 0.0
        accuracy = correct / total if total else 0.0
        on_epoch(
            EpochRecord(
                stage=stage,
                epoch=epoch,
                epochs=epochs,
                loss=loss_value,
                accuracy=accuracy,
                seconds=time.monotonic() - started,
            )
        )
    return loss_value, accuracy


def train(
    *,
    dataset_dir: Path,
    checkpoint_path: Path,
    image_size: int,
    epochs_head: int,
    epochs_finetune: int,
    samples_per_epoch: int,
    batch_size: int,
    lr_head: float,
    lr_finetune: float,
    background: str,
    holdout_frames: int,
    pretrained: bool,
    seed: int,
    threads: int,
    architecture: str = DEFAULT_ARCHITECTURE,
    sample_dir: Path | None = None,
    on_epoch: Callable[[EpochRecord], None] | None = None,
    on_stage: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> TrainResult:
    """Fit EfficientNet-B0 to the artwork under ``dataset_dir`` and save it."""
    configure_threads(threads)
    torch.manual_seed(seed)
    report = on_epoch or (lambda _record: None)
    stage = on_stage or (lambda _name: None)
    cancelled = should_cancel or (lambda: False)
    started = time.monotonic()

    stage("prepare")
    classes = symbol_dataset.load_sources(dataset_dir)
    names = symbol_dataset.symbols(classes)

    kept: list[list[int]] = []
    held: list[list[int]] = []
    leaks: list[float] = []
    for entry in classes:
        keep, hold = symbol_dataset.frame_split(len(entry), holdout_frames)
        kept.append(keep)
        held.append(hold)
        if hold:
            leaks.append(symbol_dataset.leakage(entry.sources, keep, hold))

    if sample_dir is not None:
        _write_samples(classes, kept, sample_dir, image_size, background, seed)

    train_set = _Samples(classes, kept, image_size, background, seed, augment=True)
    loader: DataLoader[tuple[torch.Tensor, int]] = DataLoader(
        train_set,
        batch_size=batch_size,
        sampler=_sampler(train_set.labels, samples_per_epoch, seed),
        num_workers=0,
    )

    model = _build(len(names), pretrained, architecture)
    criterion = nn.CrossEntropyLoss(label_smoothing=0.05)
    records: list[EpochRecord] = []

    def collect(record: EpochRecord) -> None:
        records.append(record)
        report(record)

    loss = accuracy = 0.0

    # Stage one: the new head alone. Its weights are random, and letting their
    # gradients through the backbone on the first pass is what undoes the
    # pretrained features this whole approach depends on.
    if epochs_head > 0:
        stage("head")
        _freeze(model, architecture, frozen=True)
        optimiser = torch.optim.AdamW(
            [p for p in model.parameters() if p.requires_grad], lr=lr_head
        )
        loss, accuracy = _run_stage(
            model,
            loader,
            optimiser,
            criterion,
            None,
            stage="head",
            epochs=epochs_head,
            on_epoch=collect,
            should_cancel=cancelled,
        )

    if epochs_finetune > 0:
        stage("finetune")
        _freeze(model, architecture, frozen=False)
        optimiser = torch.optim.AdamW(model.parameters(), lr=lr_finetune)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimiser, T_max=max(1, epochs_finetune)
        )
        loss, accuracy = _run_stage(
            model,
            loader,
            optimiser,
            criterion,
            scheduler,
            stage="finetune",
            epochs=epochs_finetune,
            on_epoch=collect,
            should_cancel=cancelled,
        )

    stage("evaluate")
    holdout_classes = [name for name, hold in zip(names, held, strict=True) if hold]
    holdout_accuracy: float | None = None
    holdout_samples = 0
    per_class_holdout: dict[str, float] = {}
    if holdout_classes:
        holdout_set = _Samples(
            classes, held, image_size, background, seed, augment=False
        )
        holdout_samples = len(holdout_set)
        holdout_accuracy, right, seen = _accuracy(
            model, holdout_set, batch_size, cancelled
        )
        per_class_holdout = _per_class(names, right, seen)

    augmented_set = _Samples(classes, kept, image_size, background, seed, augment=False)
    augmented_accuracy, right, seen = _accuracy(
        model, augmented_set, batch_size, cancelled
    )
    per_class_augmented = _per_class(names, right, seen)

    metrics = Metrics(
        classes=names,
        architecture=architecture,
        train_accuracy=accuracy,
        train_loss=loss,
        frame_holdout_accuracy=holdout_accuracy,
        frame_holdout_samples=holdout_samples,
        frame_holdout_classes=holdout_classes,
        frame_holdout_leakage=float(np.mean(leaks)) if leaks else 0.0,
        augmented_accuracy=augmented_accuracy,
        augmented_samples=len(augmented_set),
        per_class_holdout=per_class_holdout,
        per_class_augmented=per_class_augmented,
        single_image_classes=[entry.symbol for entry in classes if len(entry) == 1],
        epochs=records,
        seconds=time.monotonic() - started,
    )

    stage("save")
    _save(
        model,
        checkpoint_path,
        names,
        image_size,
        architecture,
        symbol_dataset.fingerprint(dataset_dir),
        metrics,
    )
    return TrainResult(checkpoint=checkpoint_path, metrics=metrics)


def _write_samples(
    classes: Sequence[symbol_dataset.ClassSources],
    indices: Sequence[Sequence[int]],
    directory: Path,
    size: int,
    style: str,
    seed: int,
) -> None:
    """Save one composed picture per class, for a human to look at."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        rng = random.Random(seed)
        for entry, chosen in zip(classes, indices, strict=True):
            if not chosen:
                continue
            source = entry.sources[chosen[len(chosen) // 2]]
            picture = symbol_dataset.compose(source, size, rng, style=style)
            picture.save(directory / f"{entry.symbol}.png")
    except OSError:
        # A preview that cannot be written is not worth failing a training run
        # that otherwise succeeded.
        return


def _save(
    model: nn.Module,
    path: Path,
    classes: Sequence[str],
    image_size: int,
    architecture: str,
    fingerprint: str,
    metrics: Metrics,
) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "state_dict": model.state_dict(),
            "classes": list(classes),
            "image_size": image_size,
            # Which network these weights belong to. Without it `load` would build
            # the default backbone and the state dict simply would not fit -- an
            # obscure shape error instead of "this is a ResNet checkpoint".
            "architecture": architecture,
            "transform_version": TRANSFORM_VERSION,
            "dataset_fingerprint": fingerprint,
            "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "metrics": asdict(metrics),
            "mean": _MEAN,
            "std": _STD,
        }
        # Written beside, then moved over: a run interrupted mid-write would
        # otherwise leave a truncated checkpoint where a working one had been.
        temporary = path.with_suffix(path.suffix + ".tmp")
        torch.save(payload, temporary)
        temporary.replace(path)
        # Named after the architecture, like the checkpoint, so training the
        # other kind does not overwrite the first one's figures.
        (path.parent / f"metrics-{architecture}.json").write_text(
            json.dumps(asdict(metrics), indent=2), encoding="utf-8"
        )
    except OSError as exc:
        raise ModelError(f"the trained model could not be written to {path}") from exc


def load(path: Path) -> Checkpoint:
    """Read a checkpoint back, rebuilding the network around its weights."""
    try:
        payload = torch.load(path, map_location="cpu", weights_only=False)
    except FileNotFoundError as exc:
        raise ModelError(f"no model at {path}") from exc
    except Exception as exc:
        raise ModelError(f"the model at {path} could not be read ({exc})") from exc

    try:
        classes = [str(name) for name in payload["classes"]]
        architecture = str(payload.get("architecture", DEFAULT_ARCHITECTURE))
        model = _build(len(classes), False, architecture)
        model.load_state_dict(payload["state_dict"])
        model.eval()
        return Checkpoint(
            model=model,
            classes=classes,
            image_size=int(payload["image_size"]),
            architecture=architecture,
            transform_version=int(payload.get("transform_version", 0)),
            dataset_fingerprint=str(payload.get("dataset_fingerprint", "")),
            trained_at=str(payload.get("trained_at", "")),
            metrics=dict(payload.get("metrics", {})),
            path=path,
        )
    except (KeyError, RuntimeError, TypeError, ValueError) as exc:
        raise ModelError(f"the model at {path} is not usable ({exc})") from exc


def predict(
    checkpoint: Checkpoint, images: Sequence[Image.Image], threads: int
) -> np.ndarray:
    """Probabilities per class for each picture, as an ``(n, classes)`` array."""
    if not images:
        return np.zeros((0, len(checkpoint.classes)), dtype=np.float64)
    configure_threads(threads)
    transform = _eval_transform(checkpoint.image_size)
    try:
        batch = torch.stack([transform(image.convert("RGB")) for image in images])
        with torch.inference_mode():
            logits = checkpoint.model(batch)
            probabilities = torch.softmax(logits, dim=1)
        return np.asarray(probabilities.numpy(), dtype=np.float64)
    except (RuntimeError, ValueError) as exc:
        raise ModelError(f"the tiles could not be classified ({exc})") from exc


def epoch_total(epochs_head: int, epochs_finetune: int) -> int:
    """How many epochs a run of this configuration will report."""
    return max(0, epochs_head) + max(0, epochs_finetune)


def estimate_seconds(epochs: int, samples: int, batch_size: int) -> float:
    """A rough wall-clock estimate, for the UI to set expectations with."""
    batches = math.ceil(samples / max(1, batch_size))
    return epochs * batches * 1.4
