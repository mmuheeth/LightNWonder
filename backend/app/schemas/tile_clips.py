"""Per-tile video clips of a winning spin."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["TileClip", "TileClipSet"]


class TileClip(BaseModel):
    """One reel position's own video."""

    name: str = Field(
        description="Matrix position, the same name the split's tile file has: 'r1c1'."
    )
    row: int = Field(ge=1, description="1-indexed row, top to bottom.")
    column: int = Field(ge=1, description="1-indexed column, which is the reel.")
    file_name: str = Field(
        description="File name inside the set's directory, extension included."
    )
    width: int = Field(ge=1, description="Frame width in pixels.")
    height: int = Field(ge=1, description="Frame height in pixels.")
    frames: int = Field(ge=0, description="Pictures in this clip.")
    bytes_written: int = Field(ge=0, description="Size of the file on disk.")


class TileClipSet(BaseModel):
    """Every tile's clip from one capture, and how it was taken."""

    directory: str | None = Field(
        default=None, description="Where the clips were written. Null when none were."
    )
    rows: int = Field(default=0, ge=0, description="Rows the grid was split into.")
    columns: int = Field(default=0, ge=0, description="Reels the grid was split into.")
    frames: int = Field(default=0, ge=0, description="Frames grabbed, per tile.")
    fps: float = Field(
        default=0.0, ge=0, description="Frames a second the clips actually play at."
    )
    requested_fps: float = Field(
        default=0.0, ge=0, description="Frames a second the capture aimed for."
    )
    duration_ms: int = Field(
        default=0, ge=0, description="Wall clock the capture covered."
    )
    codec: str | None = Field(
        default=None,
        description=(
            "Four-character code of the encoder that wrote them. Chosen by "
            "probing what this build of OpenCV can actually write, so it is not "
            "always the preferred one."
        ),
    )
    content_type: str | None = Field(
        default=None, description="What the files are, for a <video> element."
    )
    clips: list[TileClip] = Field(
        default_factory=list, description="One per tile, row-major."
    )
    error: str | None = Field(
        default=None,
        description=(
            "Why there are no clips, or fewer than expected. Reported rather "
            "than raised: the clips are a record of a spin, never a reading "
            "anything is graded by, so failing to make them must not fail the "
            "run that was analysed."
        ),
    )
