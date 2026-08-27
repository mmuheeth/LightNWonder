"""Scores one picture against a folder of reference symbols.

The question the reel-grid and payline services cannot answer between them.
:mod:`app.services.paylines` asks whether two tiles of the *same* screenshot are
alike; this asks what a single tile actually **is**, by putting it next to
artwork that has a name on it. Same measure either way --
:func:`app.utils.similarity.vector_cosine`, the same trim and corner-rounding --
so a score here is comparable with a score there and
``PAYLINE_MATCH_THRESHOLD`` can be read against both.

Two preparations stand between a source file and a comparable one, and both are
the caller's own point rather than incidental:

- **The padding is trimmed.** Exported symbol artwork sits in the middle of a
  square canvas of transparent black, so most of an untrimmed file is background
  the candidate does not have -- which drags every score toward every other one.
  :func:`app.utils.letterbox.content_box` finds the artwork, with this feature's
  own floor (see :mod:`app.config.symbol_validation`).
- **The resolution becomes the candidate's.** Cosine similarity is between two
  vectors of the same length, so 600x600 artwork has to become a 100x82 tile
  before it can be scored at all. The candidate is never resized: it is the
  measurement, and resampling it would make the answer depend on which source
  happened to be compared first.

Alpha is composited onto **black**, never dropped. Discarding the channel would
take whatever colour the exporter happened to leave under a transparent pixel --
white for some tools -- and a white surround scores nothing like the dark reel a
tile is cut from.

Both paths come off the wire as *paths*, not as names resolved inside a managed
directory, because neither input lives in one: the candidate is written by
:mod:`app.services.grid` under ``obs-captured-files/``, the sources are artwork
kept wherever they were exported. That is a deliberate departure from
:mod:`app.utils.paths` -- this is a local operator tool on a machine whose game
installs it already reads -- and it is why every response repeats the absolute
path it resolved to. Holds no state, so no ``reset()``.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from app.config.symbol_validation import BACKEND_ROOT, REPO_ROOT
from app.core.config import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    BadRequestError,
    RoiExtractFailedError,
    SymbolCompareFailedError,
    SymbolSourceNotFoundError,
)
from app.schemas.symbol_validation import (
    SymbolCandidate,
    SymbolComparison,
    SymbolGroupResult,
    SymbolSkipped,
    SymbolValidationRequest,
    SymbolValidationResult,
    SymbolValidationStats,
)
from app.services import roi as roi_service
from app.utils import letterbox, similarity

logger = get_logger("symbol-validation")

# What counts as a source picture under the root; anything else in the folder
# (a .psd, a README) is passed over silently rather than reported as unreadable.
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})

# Downscaling 600px artwork to a 100px tile throws away most of every symbol, so
# the filter matters: nearest-neighbour would alias the fine strokes that
# separate two symbols into noise.
_RESAMPLE = Image.Resampling.LANCZOS


# --- resolving a typed path -----------------------------------------------


def _search_roots() -> tuple[Path, ...]:
    """Where a relative path is looked for, in order.

    The working directory first (the backend is run from ``backend/``), then the
    repository and the backend directory -- so a path copied out of an editor's
    sidebar (``backend/assets/...``) and one typed from the running process's own
    directory (``assets/...``) both land on the same file.
    """
    return (Path.cwd(), REPO_ROOT, BACKEND_ROOT)


def _candidate_paths(value: str) -> list[Path]:
    """Every place one typed path could mean, de-duplicated, in search order."""
    typed = Path(value)
    if typed.is_absolute():
        return [typed]
    seen: dict[Path, None] = {}
    for root in _search_roots():
        seen.setdefault(root / typed, None)
    return list(seen)


def _resolve(raw: str, *, field: str, directory: bool) -> Path:
    """Turn a typed path into an existing file or folder, or say where it looked.

    The message lists every path tried rather than repeating the one that was
    typed: a relative path that resolved somewhere unexpected is the whole
    failure mode here, and only the list shows it.
    """
    # Quotes survive a copy out of Explorer's address bar or a PowerShell prompt.
    value = raw.strip().strip('"').strip("'").strip()
    if not value:
        raise BadRequestError(f"{field} must not be empty")

    tried = _candidate_paths(value)
    for path in tried:
        resolved = path.resolve()
        if resolved.is_dir() if directory else resolved.is_file():
            return resolved

    what = "folder" if directory else "file"
    where = ", ".join(str(path.resolve()) for path in tried)
    raise SymbolSourceNotFoundError(
        f"{field} does not name a {what} that exists. Looked in: {where}"
    )


# --- reading pictures -----------------------------------------------------


def _flatten(image: Image.Image) -> Image.Image:
    """One picture as RGB, compositing any alpha onto black.

    ``convert("RGB")`` alone keeps whatever colour sits *under* a transparent
    pixel, which is the exporter's business and not always black; compositing
    states the backdrop instead of inheriting it.
    """
    has_alpha = image.mode in {"RGBA", "LA", "PA"} or (
        image.mode == "P" and "transparency" in image.info
    )
    if not has_alpha:
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    backdrop = Image.new("RGB", rgba.size, (0, 0, 0))
    backdrop.paste(rgba, mask=rgba.getchannel("A"))
    return backdrop


def _open(path: Path) -> Image.Image:
    """Read one picture fully, flattened, so it outlives the file handle."""
    with Image.open(path) as image:
        image.load()
        return _flatten(image)


def _encode(image: Image.Image) -> str:
    """One picture as a PNG data URI, in this feature's own error. ROI owns the
    encoding; a failure here is a symbol comparison failing, not a region."""
    try:
        return roi_service.encode_png(image)
    except RoiExtractFailedError as exc:
        raise SymbolCompareFailedError(str(exc)) from exc


def _trim(image: Image.Image) -> tuple[Image.Image, letterbox.ContentBox]:
    """Cut the padding off a source picture, reporting the box it kept."""
    box = letterbox.content_box(
        image,
        threshold=settings.SYMBOL_VALIDATION_TRIM_THRESHOLD,
        min_fraction=settings.SYMBOL_VALIDATION_TRIM_MIN_FRACTION,
    )
    return (image.crop(box.box) if box.letterboxed else image), box


def _sources(root: Path) -> list[Path]:
    """Every picture below the root, sorted, or a 404 saying there are none."""
    found = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
    )
    if not found:
        suffixes = ", ".join(sorted(_IMAGE_SUFFIXES))
        raise SymbolSourceNotFoundError(
            f"{root} holds no pictures to compare against (looked for {suffixes}, "
            "recursively)"
        )
    limit = settings.SYMBOL_VALIDATION_MAX_SOURCES
    if len(found) > limit:
        # Failed rather than cut short: a truncated sweep reports the best of
        # what it happened to read and looks exactly like a complete one.
        raise BadRequestError(
            f"{root} holds {len(found)} pictures, more than the {limit} one "
            "comparison may read. Point source_dir at one symbol's folder, or "
            "raise SYMBOL_VALIDATION_MAX_SOURCES."
        )
    return found


def _group_of(relative: Path, root: Path) -> str:
    """Which folder a source belongs to -- its symbol code, in a folder laid out
    one directory per symbol. A file sitting directly in the root is grouped
    under the root's own name rather than ``.``."""
    parent = relative.parent.as_posix()
    return root.name if parent == "." else parent


# --- comparing ------------------------------------------------------------


@dataclass(frozen=True)
class _Scored:
    """One source's result, before the sweep as a whole can be ordered.

    Everything :class:`SymbolComparison` carries except ``rank``, which is a
    fact about the *sweep* rather than about this file -- so the response model
    is built once sorting can supply it, rather than being constructed with a
    placeholder rank and patched afterwards.
    """

    group: str
    name: str
    relative_path: str
    similarity: float
    source_width: int
    source_height: int
    content_box: list[int]
    trimmed: bool
    image_data: str | None

    def at(self, rank: int) -> SymbolComparison:
        """This result as the API reports it, in its place in the order."""
        return SymbolComparison(rank=rank, **asdict(self))


def _compare_one(
    path: Path,
    *,
    root: Path,
    size: tuple[int, int],
    candidate_vector: np.ndarray,
    include_images: bool,
) -> _Scored:
    """Trim, resize and score one source against the candidate."""
    relative = path.relative_to(root)
    image = _open(path)
    trimmed, box = _trim(image)
    prepared = trimmed if trimmed.size == size else trimmed.resize(size, _RESAMPLE)
    return _Scored(
        group=_group_of(relative, root),
        name=path.name,
        relative_path=relative.as_posix(),
        similarity=similarity.vector_cosine(
            candidate_vector, similarity.vector(prepared)
        ),
        source_width=image.width,
        source_height=image.height,
        content_box=list(box.box),
        trimmed=box.letterboxed,
        image_data=_encode(prepared) if include_images else None,
    )


def _group_results(comparisons: list[SymbolComparison]) -> list[SymbolGroupResult]:
    """The sweep reduced per folder, **in the order the folders were read**.

    Not by score, deliberately -- see the ordering note on :func:`_compare`. A
    dict preserves insertion order, and the comparisons arrive in source order,
    so first appearance is folder order for free.

    ``best`` is still the number that decides a folder, and ``mean`` travels
    beside it: a symbol animated over 48 frames only resembles the candidate in
    the frames the animation was near the pose the screenshot caught, so its
    mean is dragged down by frames that were never a candidate for the answer.
    A folder winning on one frame and a folder winning throughout are different
    claims, and only the pair distinguishes them.
    """
    by_group: dict[str, list[SymbolComparison]] = {}
    for comparison in comparisons:
        by_group.setdefault(comparison.group, []).append(comparison)

    return [
        SymbolGroupResult(
            name=name,
            count=len(members),
            best=max(member.similarity for member in members),
            best_source=max(members, key=lambda member: member.similarity).name,
            worst=min(member.similarity for member in members),
            mean=sum(member.similarity for member in members) / len(members),
        )
        for name, members in by_group.items()
    ]


def _ranked(groups: list[SymbolGroupResult]) -> list[SymbolGroupResult]:
    """The folders by score, which is a *question about* the list rather than
    the order it travels in -- `stats` and the summary need it, nothing else."""
    return sorted(groups, key=lambda group: (-group.best, group.name))


def _summary(ranked: list[SymbolGroupResult], stats: SymbolValidationStats) -> str:
    """One sentence: which folder won, and whether it won by anything."""
    if not ranked:
        return "Nothing was compared"
    top = ranked[0]
    if stats.runner_up_group is None:
        return f"{top.name} at {top.best:.4f} — the only folder compared"
    return (
        f"{top.name} at {top.best:.4f}, {stats.margin:.4f} clear of "
        f"{stats.runner_up_group} ({stats.sources} sources in {stats.groups} folders)"
    )


def _compare(request: SymbolValidationRequest) -> SymbolValidationResult:
    """Blocking half of :func:`compare`."""
    candidate_path = _resolve(
        request.candidate_path, field="candidate_path", directory=False
    )
    root = (
        _resolve(request.source_dir, field="source_dir", directory=True)
        if request.source_dir is not None
        else settings.SYMBOL_VALIDATION_SOURCE_DIR.resolve()
    )
    if not root.is_dir():
        raise SymbolSourceNotFoundError(
            f"The configured symbol folder {root} does not exist. Name one on "
            "the request, or set SYMBOL_VALIDATION_SOURCE_DIR."
        )

    try:
        candidate = _open(candidate_path)
    except OSError as exc:
        raise SymbolCompareFailedError(
            f"{candidate_path.name} could not be read as an image: {exc}"
        ) from exc
    candidate_vector = similarity.vector(candidate)

    scored: list[_Scored] = []
    skipped: list[SymbolSkipped] = []
    for path in _sources(root):
        try:
            scored.append(
                _compare_one(
                    path,
                    root=root,
                    size=candidate.size,
                    candidate_vector=candidate_vector,
                    include_images=request.include_images,
                )
            )
        except (
            OSError,
            Image.DecompressionBombError,
            similarity.SimilarityError,
        ) as exc:
            # One unreadable file does not cost the other two hundred their
            # answer -- but it is reported, since a source missing from the
            # sweep is indistinguishable from one that scored badly. Narrow on
            # purpose: a blanket `except Exception` here turns a bug in the
            # preparation into two hundred files that "could not be read".
            skipped.append(
                SymbolSkipped(
                    relative_path=path.relative_to(root).as_posix(), reason=str(exc)
                )
            )

    if not scored:
        raise SymbolCompareFailedError(
            f"None of the {len(skipped)} pictures under {root} could be compared"
        )

    # The sweep travels in **source order** -- the order the folder was read, so
    # AA_00000 through AA_00047 then BB_00000, an animation in the sequence it
    # was exported. Score order is carried by `rank` instead of by the position
    # in the list, because the two answer different questions: sorting by score
    # turns a symbol's 48 frames into 48 rows scattered through the list, and a
    # chart drawn over that has an x-axis of nothing in particular. Order is the
    # response's to state rather than the browser's to reconstruct: only this
    # end knows what "the order the sources were read" was.
    by_score = sorted(
        range(len(scored)),
        key=lambda index: (-scored[index].similarity, scored[index].relative_path),
    )
    ranks = [0] * len(scored)
    for rank, index in enumerate(by_score, start=1):
        ranks[index] = rank
    comparisons = [item.at(rank) for item, rank in zip(scored, ranks, strict=True)]

    best = comparisons[by_score[0]]
    worst = comparisons[by_score[-1]]
    groups = _group_results(comparisons)
    ranked = _ranked(groups)
    runner_up = ranked[1] if len(ranked) > 1 else None
    stats = SymbolValidationStats(
        sources=len(comparisons),
        skipped=len(skipped),
        groups=len(groups),
        best=best.similarity,
        best_source=best.relative_path,
        best_group=best.group,
        worst=worst.similarity,
        mean=sum(item.similarity for item in comparisons) / len(comparisons),
        runner_up_group=runner_up.name if runner_up else None,
        margin=ranked[0].best - runner_up.best if runner_up else None,
    )

    logger.info(
        "Compared %s (%dx%d) against %d symbols in %s: %s at %.4f%s",
        candidate_path.name,
        candidate.width,
        candidate.height,
        len(comparisons),
        root,
        stats.best_group,
        stats.best or 0.0,
        f" ({len(skipped)} skipped)" if skipped else "",
    )
    return SymbolValidationResult(
        candidate=SymbolCandidate(
            path=str(candidate_path),
            name=candidate_path.name,
            width=candidate.width,
            height=candidate.height,
            image_data=_encode(candidate) if request.include_images else None,
        ),
        source_dir=str(root),
        summary=_summary(ranked, stats),
        comparisons=comparisons,
        groups=groups,
        stats=stats,
        skipped=skipped,
    )


async def compare(request: SymbolValidationRequest) -> SymbolValidationResult:
    """Score one picture against every symbol under a folder, in source order."""
    return await asyncio.to_thread(_compare, request)
