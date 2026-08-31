"""Similarity check payloads. Unlike the payline schemas next door, the score
here is the whole answer -- nothing pays, nothing is drawn. Every candidate
comes back on its own row so the separation between same-symbol and
different-symbol scores can be read (and charted) off one response."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "SimilarityCompareRequest",
    "SimilarityCompareResult",
    "SimilarityMatch",
    "SimilaritySource",
    "SimilarityStats",
]


class SimilarityCompareRequest(BaseModel):
    """Which picture to compare, against which folder, and how strictly."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {},
                {"threshold": 0.93},
                {
                    "source": "symbol-validation/r1c2.png",
                    "directory": "symbol-validation/AA",
                },
            ]
        },
    )

    source: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description=(
            "Path to the image every candidate is scored against, absolute or "
            "relative to the backend's working directory. Omit for "
            "SIMILARITY_SOURCE."
        ),
    )
    directory: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description=(
            "Folder whose images are the candidates, searched recursively; "
            "absolute or relative to the backend's working directory. Omit "
            "for SIMILARITY_CANDIDATES_DIR."
        ),
    )
    threshold: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description=(
            "Cosine similarity a candidate must reach to count as a match. "
            "Omit for PAYLINE_MATCH_THRESHOLD."
        ),
    )
    include_images: bool = Field(
        default=True,
        description=(
            "Return the compared pictures as data URIs beside the scores. "
            "Set false when only the numbers are wanted."
        ),
    )


class SimilaritySource(BaseModel):
    """The picture everything was scored against."""

    file_name: str = Field(description="Name of the source image file.")
    width: int = Field(ge=1, description="Source width in pixels.")
    height: int = Field(ge=1, description="Source height in pixels.")
    image_data: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of the source picture. Null when the request "
            "asked for numbers only."
        ),
    )


class SimilarityMatch(BaseModel):
    """One candidate, scored -- or the reason it could not be."""

    file_name: str = Field(
        description=(
            "Path relative to the scanned folder, posix separators, e.g. "
            "'AA/AA_00001.png'."
        )
    )
    score: float | None = Field(
        default=None,
        description=(
            "Cosine similarity against the source, in [-1, 1]. Read it against "
            "the run's matched_min/rejected_max, not as a plain fraction. Null "
            "when the file could not be read."
        ),
    )
    matched: bool = Field(
        description=(
            "Whether the score reached the threshold. False for a candidate "
            "that could not be read."
        )
    )
    resized: bool = Field(
        description=(
            "Whether the candidate was scaled to fill the source's size "
            "before comparing (aspect preserved, overflow centre-cropped) -- "
            "read its score with that in mind."
        )
    )
    trimmed: bool = Field(
        description=(
            "Whether a transparent margin was cropped off before comparing, "
            "so the artwork rather than its framing is what was measured."
        )
    )
    width: int | None = Field(
        default=None,
        ge=1,
        description="Candidate width in pixels, before any resize; null when unreadable.",
    )
    height: int | None = Field(
        default=None,
        ge=1,
        description="Candidate height in pixels, before any resize; null when unreadable.",
    )
    error: str | None = Field(
        default=None,
        description="Why this candidate could not be scored; null when it was.",
    )
    image_data: str | None = Field(
        default=None,
        description=(
            "Base64 data URI of the candidate *as compared* -- after any "
            "resize -- so what the score measured is what is shown. Null when "
            "the file was unreadable or the request asked for numbers only."
        ),
    )


class SimilarityStats(BaseModel):
    """The run as a whole: what was scored, and how well the scores separated."""

    candidates: int = Field(ge=0, description="Image files found under the folder.")
    compared: int = Field(ge=0, description="How many of them were scored.")
    errors: int = Field(ge=0, description="How many could not be read.")
    resized: int = Field(
        ge=0, description="How many were resized to the source's size first."
    )
    matches: int = Field(ge=0, description="Scores that reached the threshold.")
    score_min: float | None = Field(default=None, description="Lowest score seen.")
    score_max: float | None = Field(default=None, description="Highest score seen.")
    matched_min: float | None = Field(
        default=None,
        description=(
            "Lowest score that was counted as a match. Null when nothing matched."
        ),
    )
    rejected_max: float | None = Field(
        default=None,
        description=(
            "Highest score not counted as a match; with matched_min, the gap "
            "a working threshold sits in."
        ),
    )


class SimilarityCompareResult(BaseModel):
    """Every candidate's score against one source, and the spread of them."""

    source: SimilaritySource = Field(description="The picture that was compared from.")
    directory: str = Field(
        description="Absolute folder the candidates were read out of."
    )
    threshold: float = Field(description="Similarity cut that was applied.")
    summary: str = Field(
        description="The result as one sentence, e.g. '48 of 48 matched r1c2.png at 0.86'."
    )
    results: list[SimilarityMatch] = Field(
        description=(
            "Every candidate: scored ones first, highest score first; "
            "unreadable ones last, by name."
        )
    )
    stats: SimilarityStats = Field(description="The run as a whole.")
