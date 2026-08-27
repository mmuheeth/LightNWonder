"""Image classifier settings: where the training images and the trained
checkpoint live, how a symbol is named from a tile, and how much of the machine
training is allowed to take.

Consumed by :mod:`app.utils.symbol_dataset` (which builds training samples out of
the artwork), :mod:`app.utils.symbol_model` (the only module that imports torch)
and :mod:`app.services.image_classifier`. Like OCR, the engine is optional: a
machine without torch installed still starts and only training and classifying
fail, so nothing here is required to be satisfiable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings

__all__ = ["BackgroundStyle", "ImageClassifierSettings"]

# How a cut-out symbol is given the background the game draws it on. `plate` is
# the measured reel cell (see app/utils/symbol_dataset.py); `solid` is the flat
# field colour with no texture, gradient or frame; `none` composites over black
# and is only useful for proving the background is what matters.
BackgroundStyle = Literal["plate", "solid", "none"]


class ImageClassifierSettings(BaseSettings):
    """Training and inference options for naming a reel tile's symbol."""

    # false makes every request answer "the classifier is disabled" without
    # importing torch, for a host that is not meant to train anything.
    CLASSIFIER_ENABLED: bool = True

    # Root of the training images, one directory per symbol code holding that
    # symbol's artwork (the torchvision ImageFolder shape). Relative resolves
    # against backend/, as OBS_CAPTURE_DIR does.
    CLASSIFIER_DATASET_DIR: Path = Path("dataset")

    # Where the checkpoint, its metrics and the synthesised sample previews go.
    # Under obs-captured-files/ so it is gitignored output like every other
    # artefact this service writes, and sits beside grid/ and cash-meter/.
    CLASSIFIER_MODEL_DIR: Path = Path("obs-captured-files/classifier")

    # Square input the network sees. 224 is EfficientNet-B0's native size, and
    # the pretrained weights are worth more than the cost of upscaling a 61px
    # tile -- what matters is that training and inference upscale identically.
    CLASSIFIER_IMAGE_SIZE: int = Field(default=224, ge=32, le=600)

    # Probability the winning symbol must reach for the tile to be named at all.
    # Load-bearing rather than decorative: this game declares eighteen symbol
    # codes and the dataset covers nine, so a softmax over the nine would name a
    # cash orb with conviction. Below this a tile reports `unknown` and still
    # carries its top few probabilities, which is the honest answer.
    #
    # 0.65 is measured, not guessed, and unlike the payline check's cosine
    # threshold it sits in an actual gap. Over 210 real tiles from 14 written
    # splits: 29% score below 0.50, and the widest empty band in the whole
    # distribution runs from **0.563 to 0.681**. Everything from 0.681 up is a
    # real symbol -- the weakest are `JJ` (Ten) tiles at 0.681-0.76, checked by
    # eye -- and the highest thing the model has no class for is a cash orb at
    # 0.563. So the cut belongs inside that band and 0.65 is inside it.
    #
    # Placed nearer the symbol edge than the midpoint on purpose. The two
    # mistakes are not equally bad: naming a symbol the model was never trained
    # on is a silent wrong answer, while rejecting a real symbol is a visible
    # non-answer that the reported probabilities immediately explain.
    #
    # Re-measure against written splits rather than adjusting by intuition, and
    # re-measure after *any* change to the transforms -- an earlier build put this
    # at 0.70 on numbers taken before the evaluation transform was fixed, which
    # then rejected genuine Ten tiles at 0.681. The better fix is artwork for the
    # missing nine codes.
    CLASSIFIER_MIN_CONFIDENCE: float = Field(default=0.65, ge=0.0, le=1.0)

    # How many of the ranked probabilities travel back per tile. Three is enough
    # to see whether a rejection was a close call or the model had no idea.
    CLASSIFIER_TOP_K: int = Field(default=3, ge=1, le=20)

    # --- training ---------------------------------------------------------
    # Two stages: the new classifier head alone against a frozen backbone, then
    # everything at a tenth of the rate. The first stage is what stops the
    # randomly-initialised head from wrecking the pretrained features.
    #
    # Measured on this machine at 224px, 320 samples and six threads: a head
    # epoch is ~19s and a finetune epoch ~47s, and training accuracy is already
    # 0.96 after the *first* finetune epoch -- nine classes of distinct artwork is
    # an easy fit. So these are set for a run of about four minutes rather than
    # the eight a longer schedule would cost for no measurable gain.
    CLASSIFIER_EPOCHS_HEAD: int = Field(default=3, ge=0, le=200)
    CLASSIFIER_EPOCHS_FINETUNE: int = Field(default=4, ge=0, le=200)

    # Samples drawn per epoch, deliberately decoupled from how many files there
    # are. The dataset is ~200 files of which several classes hold one, so with a
    # class-balanced sampler "an epoch" has to be a number rather than a pass.
    CLASSIFIER_SAMPLES_PER_EPOCH: int = Field(default=320, ge=8, le=100_000)

    CLASSIFIER_BATCH_SIZE: int = Field(default=16, ge=1, le=512)

    CLASSIFIER_LR_HEAD: float = Field(default=1e-3, gt=0.0)
    CLASSIFIER_LR_FINETUNE: float = Field(default=1e-4, gt=0.0)

    # How the cut-out artwork is given a background before training. Only one of
    # the nine classes ships with the game's field and frame already drawn on it;
    # the other eight are transparent cut-outs, and training on those against
    # tiles whose symbols sit on dark purple is what made the previous
    # similarity-based attempt miss the card symbols entirely.
    CLASSIFIER_BACKGROUND: BackgroundStyle = "plate"

    # Frames held out of each animation loop for the one honest accuracy figure.
    # Classes with a single image cannot contribute to it, and the response says
    # so rather than averaging the two kinds of number together.
    CLASSIFIER_HOLDOUT_FRAMES: int = Field(default=8, ge=0, le=1000)

    # ImageNet weights, a ~21MB download from download.pytorch.org on the first
    # train. false trains from scratch, which on ~9 distinct images is much worse
    # -- it exists so a machine with no network can still prove the pipeline runs.
    CLASSIFIER_PRETRAINED: bool = True

    # A rerun over unchanged files gives the same model.
    CLASSIFIER_SEED: int = Field(default=1337, ge=0)

    # Threads torch may use. 0 means half the machine's cores, which is the point
    # of the setting: torch takes every core by default, and this process is also
    # the one driving OBS, pressing the i-deck and clicking the game window. A
    # training run that starves that loop makes the whole dashboard look hung.
    CLASSIFIER_TORCH_THREADS: int = Field(default=0, ge=0, le=256)

    @property
    def classifier_dataset_dir(self) -> Path:
        """Absolute root of the training images."""
        return self.CLASSIFIER_DATASET_DIR.expanduser().resolve()

    @property
    def classifier_model_dir(self) -> Path:
        """Absolute directory holding the checkpoint and its metrics."""
        return self.CLASSIFIER_MODEL_DIR.expanduser().resolve()

    @property
    def classifier_checkpoint_path(self) -> Path:
        """The trained model itself."""
        return self.classifier_model_dir / "model.pt"

    @property
    def classifier_metrics_path(self) -> Path:
        """What the last training run measured, beside the model it measured."""
        return self.classifier_model_dir / "metrics.json"

    @property
    def classifier_sample_dir(self) -> Path:
        """Where synthesised training samples are written to be eyeballed.

        Worth having as a real directory rather than a debug flag: if accuracy
        disappoints, whether the background was composited correctly is the first
        question, and it is answerable by looking.
        """
        return self.classifier_model_dir / "samples"

    @property
    def classifier_torch_threads(self) -> int:
        """Threads to allow torch, resolving 0 to half the machine."""
        configured = self.CLASSIFIER_TORCH_THREADS
        if configured > 0:
            return configured
        return max(1, (os.cpu_count() or 2) // 2)
