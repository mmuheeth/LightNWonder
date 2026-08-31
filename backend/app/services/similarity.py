"""Scores one picture against every image in a folder, by cosine similarity.

The measure is :mod:`app.utils.similarity`'s, the same one the payline check
reads a split by -- and the default cut is the same ``PAYLINE_MATCH_THRESHOLD``,
because lining ~90 reference scores up against it is how that threshold gets
tuned. The source is converted to a vector once and every candidate is compared
against it (:func:`app.utils.similarity.vector_cosine` exists for exactly this).
Every picture passes through :mod:`app.utils.image_prep` first -- transparency
composited onto black, the transparent margin cropped, a size mismatch filling
the target with its overflow cropped rather than stretched or padded -- since a
reference library is rendered art, not a capture, and comparing it raw
measures the framing instead of the symbol.

One deliberate departure from the house path rules: ``source`` and ``directory``
are client-named paths -- absolute, or relative to the backend's working
directory, which the shipped ``symbol-validation`` defaults are -- rather than
bare names inside a configured root. The whole point is a folder the caller
points at, which :func:`app.utils.paths.resolve_within` structurally cannot
express; like a game config's ``game_config`` key, the path is trusted from a
host-machine-local caller and the read is where a wrong one becomes an error.

Holds no state, so no ``reset()``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
from PIL import Image

from app.core.config import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    SimilarityDirectoryNotFoundError,
    SimilarityNoCandidatesError,
    SimilaritySourceNotFoundError,
    SimilaritySourceUnreadableError,
)
from app.schemas.similarity import (
    SimilarityCompareRequest,
    SimilarityCompareResult,
    SimilarityMatch,
    SimilaritySource,
    SimilarityStats,
)
from app.services import roi as roi_service
from app.utils import image_prep, similarity

logger = get_logger("similarity")

# Candidate files under the folder; a stray .txt/.json is skipped rather than
# listed as a candidate and failing to open. Mirrors the ROI service's set.
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})


# --- the two paths ----------------------------------------------------------


def _source_path(requested: str | None) -> Path:
    """The source image: the one that was named, or the configured default."""
    path = (
        Path(requested) if requested is not None else settings.SIMILARITY_SOURCE
    ).resolve()
    if not path.is_file():
        raise SimilaritySourceNotFoundError(f"No source image at {path}")
    return path


def _candidates_dir(requested: str | None) -> Path:
    """The candidates folder: the one that was named, or the configured default."""
    path = (
        Path(requested) if requested is not None else settings.SIMILARITY_CANDIDATES_DIR
    ).resolve()
    if not path.is_dir():
        raise SimilarityDirectoryNotFoundError(f"No candidates folder at {path}")
    return path


def _candidate_paths(directory: Path) -> list[Path]:
    """Every image file under the folder, any depth, sorted by relative name."""
    files = sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
    )
    if not files:
        raise SimilarityNoCandidatesError(
            f"No images under {directory} (looked for {', '.join(sorted(_IMAGE_SUFFIXES))})"
        )
    return files


# --- scoring ----------------------------------------------------------------


def _open_source(path: Path) -> Image.Image:
    """Read the source fully, so the comparison outlives the file handle."""
    try:
        with Image.open(path) as image:
            image.load()
            return image.copy()
    except OSError as exc:
        raise SimilaritySourceUnreadableError(
            f"{path.name} could not be read as an image: {exc}"
        ) from exc


def _score_candidate(
    path: Path,
    file_name: str,
    source_size: tuple[int, int],
    source_vector: np.ndarray,
    cut: float,
    include_image: bool,
) -> SimilarityMatch:
    """One candidate's row: its score, or the reason it has none."""
    try:
        with Image.open(path) as image:
            image.load()
            candidate = image.copy()
    except OSError as exc:
        return SimilarityMatch(
            file_name=file_name,
            matched=False,
            resized=False,
            trimmed=False,
            error=f"could not be read as an image: {exc}",
        )
    width, height = candidate.size
    prepared = image_prep.prepare(candidate, source_size)
    score = round(
        similarity.vector_cosine(source_vector, similarity.vector(prepared.image)), 6
    )
    return SimilarityMatch(
        file_name=file_name,
        score=score,
        matched=score >= cut,
        resized=prepared.resized,
        trimmed=prepared.trimmed,
        width=width,
        height=height,
        # The picture *as compared* -- after the preparation -- so a surprising
        # score can be judged against the same pixels the measure saw.
        image_data=(roi_service.encode_png(prepared.image) if include_image else None),
    )


def _stats(results: list[SimilarityMatch], candidates: int) -> SimilarityStats:
    """The spread of the scores, for reading a threshold off one run."""
    scored = [match.score for match in results if match.score is not None]
    matched = [
        match.score for match in results if match.matched and match.score is not None
    ]
    rejected = [
        match.score
        for match in results
        if not match.matched and match.score is not None
    ]
    return SimilarityStats(
        candidates=candidates,
        compared=len(scored),
        errors=candidates - len(scored),
        resized=sum(1 for match in results if match.resized),
        matches=len(matched),
        score_min=min(scored) if scored else None,
        score_max=max(scored) if scored else None,
        matched_min=min(matched) if matched else None,
        rejected_max=max(rejected) if rejected else None,
    )


# --- public API -------------------------------------------------------------


def _compare(request: SimilarityCompareRequest) -> SimilarityCompareResult:
    """Blocking half of :func:`compare`."""
    source_path = _source_path(request.source)
    directory = _candidates_dir(request.directory)
    candidates = _candidate_paths(directory)

    source = _open_source(source_path)
    # The source gets the same trim-and-flatten (a tile is opaque, so this is
    # a no-op for one), and the candidates fit the source *as compared*.
    compared_source = image_prep.flatten(image_prep.trim_transparent(source)[0])
    source_vector = similarity.vector(compared_source)
    cut = (
        request.threshold
        if request.threshold is not None
        else settings.PAYLINE_MATCH_THRESHOLD
    )

    results = [
        _score_candidate(
            path,
            path.relative_to(directory).as_posix(),
            compared_source.size,
            source_vector,
            cut,
            request.include_images,
        )
        for path in candidates
    ]
    # Scored rows first, best score first; unreadable rows last, by name --
    # which is also the order the chart draws them in.
    results.sort(
        key=lambda match: (
            match.score is None,
            -(match.score if match.score is not None else 0.0),
            match.file_name,
        )
    )
    stats = _stats(results, len(candidates))

    logger.info(
        "Compared %d of %d images under %s against %s: %d matched at %g",
        stats.compared,
        stats.candidates,
        directory,
        source_path.name,
        stats.matches,
        cut,
    )
    return SimilarityCompareResult(
        source=SimilaritySource(
            file_name=source_path.name,
            width=source.width,
            height=source.height,
            image_data=(
                roi_service.encode_png(compared_source)
                if request.include_images
                else None
            ),
        ),
        directory=str(directory),
        threshold=cut,
        summary=(
            f"{stats.matches} of {stats.compared} matched {source_path.name} at {cut:g}"
        ),
        results=results,
        stats=stats,
    )


async def compare(request: SimilarityCompareRequest) -> SimilarityCompareResult:
    """Score every image in one folder against one source picture."""
    return await asyncio.to_thread(_compare, request)
