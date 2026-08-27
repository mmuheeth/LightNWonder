"""Symbol validation payloads.

One candidate picture against a folder of reference symbols. Every comparison
carries its own score *and* the picture that produced it, because a cosine
similarity is not checkable without seeing what was actually put in front of it
-- and what was put in front of it is not the file on disk but that file trimmed
of its padding and squeezed to the candidate's own resolution.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SymbolCandidate(BaseModel):
    """The picture everything was compared against, exactly as it was read."""

    path: str = Field(description="Absolute path the candidate resolved to.")
    name: str = Field(description="Its filename, e.g. 'r1c4.png'.")
    width: int = Field(
        ge=1, description="Width in pixels; every source is resized to it."
    )
    height: int = Field(
        ge=1, description="Height in pixels; every source is resized to it."
    )
    image_data: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of the candidate, ready for an <img> src. Null when "
            "the request asked for numbers only."
        ),
    )


class SymbolComparison(BaseModel):
    """One source picture scored against the candidate."""

    rank: int = Field(
        ge=1,
        description=(
            "Where this source places by score, 1 being the highest. The list "
            "is **not** in this order -- it is in source order, and the rank is "
            "how the score order is carried."
        ),
    )
    group: str = Field(
        description=(
            "Folder the source sits in, relative to the source root -- the "
            "symbol code when the folder is laid out one directory per symbol."
        )
    )
    name: str = Field(description="The source's filename.")
    relative_path: str = Field(
        description="Its path below the source root, in POSIX form."
    )
    similarity: float = Field(
        ge=-1.0,
        le=1.0,
        description=(
            "Cosine similarity against the candidate. Not a probability and not "
            "zero-based: unrelated pictures already score well above 0."
        ),
    )
    source_width: int = Field(
        ge=1, description="The file's own width, before trimming."
    )
    source_height: int = Field(
        ge=1, description="The file's own height, before trimming."
    )
    content_box: list[int] = Field(
        min_length=4,
        max_length=4,
        description=(
            "Pixel box [left, top, right, bottom] of the artwork inside the "
            "source file; the whole file when nothing was trimmed."
        ),
    )
    trimmed: bool = Field(
        description="Whether any padding was cut off before the comparison."
    )
    image_data: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of the picture that was actually compared -- the "
            "source trimmed and resized to the candidate. Null when the request "
            "asked for numbers only."
        ),
    )


class SymbolGroupResult(BaseModel):
    """Every source in one folder, reduced to the numbers that separate folders."""

    name: str = Field(description="Folder name, relative to the source root.")
    count: int = Field(ge=1, description="Sources compared from this folder.")
    best: float = Field(ge=-1.0, le=1.0, description="Its highest score.")
    best_source: str = Field(description="Which of its files scored highest.")
    worst: float = Field(ge=-1.0, le=1.0, description="Its lowest score.")
    mean: float = Field(ge=-1.0, le=1.0, description="Mean score across its sources.")


class SymbolSkipped(BaseModel):
    """A file under the source root that could not be compared."""

    relative_path: str = Field(description="Its path below the source root.")
    reason: str = Field(description="Why it was skipped rather than scored.")


class SymbolValidationStats(BaseModel):
    """The whole sweep in the few numbers a verdict is read off."""

    sources: int = Field(ge=0, description="Sources compared.")
    skipped: int = Field(
        ge=0, description="Files under the root that could not be read."
    )
    groups: int = Field(ge=0, description="Folders the sources came from.")
    best: float | None = Field(default=None, description="Highest score of the sweep.")
    best_source: str | None = Field(
        default=None, description="Which source scored highest."
    )
    best_group: str | None = Field(
        default=None, description="Which folder that source is in."
    )
    worst: float | None = Field(default=None, description="Lowest score of the sweep.")
    mean: float | None = Field(
        default=None, description="Mean score across every source."
    )
    runner_up_group: str | None = Field(
        default=None,
        description="Folder with the second-highest best score, or null with fewer than two folders.",
    )
    margin: float | None = Field(
        default=None,
        description=(
            "How far the best folder's best score sits above the runner-up's -- "
            "the number that says whether the top answer is actually separated "
            "from the field. Null with fewer than two folders."
        ),
    )


class SymbolValidationRequest(BaseModel):
    """Which picture to identify, and which folder to identify it from.

    Both are paths the operator types, not names resolved inside a managed
    directory: the candidate is written by the grid under
    ``obs-captured-files/`` and the sources are artwork kept wherever they were
    exported to, so neither is reachable by a bare filename. A relative path is
    tried against the working directory, the repository root and the backend
    directory, so ``backend/assets/FortuneOx/Symbols`` and
    ``assets/FortuneOx/Symbols`` name the same folder.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "candidate_path": (
                        "backend/obs-captured-files/grid/"
                        "spin-2026-08-27_16-17-49-2-outcome/tiles/r1c4.png"
                    )
                },
                {
                    "candidate_path": "backend/obs-captured-files/grid/latest/tiles/r1c1.png",
                    "source_dir": "backend/assets/FortuneOx/Symbols",
                    "include_images": False,
                },
            ]
        },
    )

    candidate_path: str = Field(
        min_length=1,
        max_length=1000,
        description="Path to the picture to identify, e.g. one tile of a reel split.",
    )
    source_dir: str | None = Field(
        default=None,
        min_length=1,
        max_length=1000,
        description=(
            "Folder of reference symbols, searched recursively. Omit to use "
            "SYMBOL_VALIDATION_SOURCE_DIR."
        ),
    )
    include_images: bool = Field(
        default=True,
        description=(
            "Return the candidate and every compared picture as data URIs. Set "
            "false for the scores alone -- a folder of a few hundred sources is "
            "a few megabytes of base64."
        ),
    )


class SymbolValidationResult(BaseModel):
    """Every source scored against one candidate, in the order they were read.

    **Source order, not score order.** A folder of symbol artwork is a sequence
    -- 48 frames of one animation, then 48 of the next -- and sorting by score
    scatters each symbol's frames through the list, leaving a chart drawn over
    it with an x-axis of nothing in particular. So the list keeps the sequence
    and ``SymbolComparison.rank`` carries the score order instead. Which source
    actually won is ``stats.best_source``.
    """

    candidate: SymbolCandidate = Field(description="The picture being identified.")
    source_dir: str = Field(description="Absolute folder the sources were read from.")
    summary: str = Field(
        description="One sentence naming the winning folder and by how much."
    )
    comparisons: list[SymbolComparison] = Field(
        description=(
            "Every source, in the order it was read: folder, then filename. "
            "See 'rank' for the score order."
        )
    )
    groups: list[SymbolGroupResult] = Field(
        description=(
            "The same sources reduced per folder, in the order the folders "
            "were read. See 'stats.best_group' for the winner."
        )
    )
    stats: SymbolValidationStats = Field(description="The sweep in numbers.")
    skipped: list[SymbolSkipped] = Field(
        default_factory=list,
        description=(
            "Files under the root that could not be read as pictures. Reported "
            "rather than dropped -- a source missing from the sweep would "
            "otherwise look like a source that scored badly."
        ),
    )
