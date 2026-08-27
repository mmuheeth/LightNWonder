"""Image classifier endpoints, thin wrappers over
:mod:`app.services.image_classifier`. Trains EfficientNet-B0 on the symbol
artwork and names the tiles of a written reel split.

``/status`` never fails: a machine with no torch installed reports
``not_installed`` on a 200, the same way OCR reports a missing Tesseract, so the
page can say what is missing rather than showing an error. Only ``/train`` and
``/classify`` raise for it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.schemas.image_classifier import (
    ClassifierStatus,
    ClassifyRequest,
    ClassifyResult,
    DatasetSummary,
    SplitCatalog,
    TrainingRun,
    TrainRequest,
)
from app.schemas.response import ApiResponse
from app.services import image_classifier as classifier_service

router = APIRouter()

ResponseSpec = dict[int | str, dict[str, Any]]

UNAVAILABLE: ResponseSpec = {
    409: {
        "description": (
            "The classifier is disabled, torch is not installed, no model has "
            "been trained, or a run is already in progress"
        )
    }
}
NOT_FOUND: ResponseSpec = {404: {"description": "No training images, or no such split"}}
FAILED: ResponseSpec = {
    502: {"description": "Training or classification could not complete"}
}


@router.get(
    "/status",
    response_model=ApiResponse[ClassifierStatus],
    summary="Whether a tile can be named right now",
)
async def get_status() -> ApiResponse[ClassifierStatus]:
    """Engine state, the trained model, the dataset and any live training run.

    Always a 200. A missing torch, an untrained model and a stale checkpoint are
    all *states* here, not errors -- the page needs to render the reason.
    """
    status = await classifier_service.status()
    return ApiResponse[ClassifierStatus].ok(
        data=status,
        message=status.detail or f"The image classifier is {status.state}",
    )


@router.get(
    "/dataset",
    response_model=ApiResponse[DatasetSummary],
    summary="Training images available, and what is wrong with them",
)
async def get_dataset() -> ApiResponse[DatasetSummary]:
    """Per-class counts and sizes, plus the warnings a file listing cannot show:
    classes holding a single picture, and symbol codes the game declares that
    have no artwork at all."""
    summary = await classifier_service.dataset()
    return ApiResponse[DatasetSummary].ok(
        data=summary,
        message=(
            summary.error
            if summary.error is not None
            else f"{summary.total_images} images across {len(summary.classes)} symbols"
        ),
    )


@router.get(
    "/splits",
    response_model=ApiResponse[SplitCatalog],
    summary="Reel splits that could be classified",
)
async def get_splits() -> ApiResponse[SplitCatalog]:
    """Every split the reel grid has written, newest first, and which one a
    classify request naming none would read."""
    catalog = await classifier_service.splits()
    return ApiResponse[SplitCatalog].ok(
        data=catalog,
        message=(
            catalog.error
            if catalog.error is not None
            else f"{len(catalog.splits)} splits; newest is {catalog.latest}"
        ),
    )


@router.post(
    "/train",
    response_model=ApiResponse[TrainingRun],
    summary="Fit a model to the training images",
    responses={**UNAVAILABLE, **NOT_FOUND, **FAILED},
)
async def train(payload: TrainRequest | None = None) -> ApiResponse[TrainingRun]:
    """Start a training run and return as soon as it is under way.

    Takes minutes on CPU, so this does not wait for it: poll ``/status`` for the
    stages and per-epoch figures. Every stage exists from the moment the run is
    created, so a failure part way through leaves the rest visibly unreached.
    """
    run = await classifier_service.train(payload or TrainRequest())
    return ApiResponse[TrainingRun].ok(
        data=run,
        message=(
            f"Training run {run.run_id} started; {run.epoch_total} epochs, "
            f"about {run.estimated_seconds / 60:.0f} minutes"
        ),
    )


@router.post(
    "/train/cancel",
    response_model=ApiResponse[TrainingRun],
    summary="Stop the training run in progress",
    responses={**UNAVAILABLE},
)
async def cancel() -> ApiResponse[TrainingRun]:
    """Ask the run to stop. The flag is read between batches, so this returns
    immediately and the run unwinds through its own code a moment later --
    leaving whatever model was already saved untouched."""
    run = await classifier_service.cancel()
    return ApiResponse[TrainingRun].ok(
        data=run, message=f"Training run {run.run_id} will stop"
    )


@router.post(
    "/classify",
    response_model=ApiResponse[ClassifyResult],
    summary="Name every tile of a reel split",
    responses={
        **UNAVAILABLE,
        **NOT_FOUND,
        **FAILED,
        400: {"description": "split is not a bare directory name"},
    },
)
async def classify(
    payload: ClassifyRequest | None = None,
) -> ApiResponse[ClassifyResult]:
    """Classify the tiles of one split (the newest if ``split`` is omitted).

    Reads a split the reel grid already wrote rather than taking a screenshot, so
    the same split classifies to the same answer twice and a threshold can be
    re-tried without the game still showing that spin.
    """
    result = await classifier_service.classify(payload or ClassifyRequest())
    return ApiResponse[ClassifyResult].ok(data=result, message=result.summary)
