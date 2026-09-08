"""Image classifier endpoints over :mod:`app.services.image_classifier`. ``/status``
never fails; only training and classifying raise for missing torch."""

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
    """Engine state, the trained model, the dataset and any live training run."""
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
    """Per-class counts and sizes, plus warnings a listing cannot show: single-picture
    classes, and declared codes with no artwork."""
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
    """Start a training run and return as soon as it is under way."""
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
    """Ask the run to stop."""
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
    """Classify the tiles of one split (the newest if ``split`` is omitted)."""
    result = await classifier_service.classify(payload or ClassifyRequest())
    return ApiResponse[ClassifyResult].ok(data=result, message=result.summary)
