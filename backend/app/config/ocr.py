"""Tesseract OCR runtime settings: where the external engine binary is, the command-line
knobs it takes, and the preprocessing applied to a crop before OCR."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

__all__ = [
    "EXECUTABLE_NAME",
    "OcrSettings",
    "candidate_executables",
    "discover_executable",
]

# On Windows the installer names it tesseract.exe; every other platform, and the
# `tesseract` reachable from Git Bash, is the bare name.
EXECUTABLE_NAME = "tesseract.exe" if os.name == "nt" else "tesseract"

# Where the Windows installers put it: an environment variable, then the rest of
# the path. The UB-Mannheim installer offers "just me" (LOCALAPPDATA) and "all
# users" (PROGRAMFILES); other builds and package managers use the Programs form.
_WINDOWS_INSTALL_DIRS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LOCALAPPDATA", ("Tesseract-OCR",)),
    ("LOCALAPPDATA", ("Programs", "Tesseract-OCR")),
    ("PROGRAMFILES", ("Tesseract-OCR",)),
    ("PROGRAMFILES(X86)", ("Tesseract-OCR",)),
    ("PROGRAMW6432", ("Tesseract-OCR",)),
    ("SYSTEMDRIVE", ("Tesseract-OCR",)),
)

_POSIX_INSTALL_DIRS = ("/usr/bin", "/usr/local/bin", "/opt/homebrew/bin")


def candidate_executables() -> Iterator[Path]:
    """Every place the engine is worth looking for, best first (PATH wins)."""
    found = shutil.which("tesseract")
    if found:
        yield Path(found)
    if os.name == "nt":
        for variable, relative in _WINDOWS_INSTALL_DIRS:
            root = os.environ.get(variable)
            if root:
                yield Path(root).joinpath(*relative) / EXECUTABLE_NAME
    else:
        for directory in _POSIX_INSTALL_DIRS:
            yield Path(directory) / EXECUTABLE_NAME


def discover_executable() -> Path | None:
    """The installed Tesseract, or ``None`` when this machine has none."""
    for candidate in candidate_executables():
        if candidate.is_file():
            return candidate
    return None


class OcrSettings(BaseSettings):
    """Runtime options for reading text off a captured frame."""

    # false makes every OCR request answer "the engine is disabled" without
    # looking for the binary, for a host that has no engine and is not meant to
    # grow one.
    OCR_ENABLED: bool = True

    # Absolute path to tesseract.exe, or to the directory holding it. Leave it
    # unset to let discover_executable() find the install.
    OCR_TESSERACT_CMD: Path | None = None

    # Directory holding <lang>.traineddata. Unset lets Tesseract use the tessdata
    # beside its own executable, which is what an installer sets up; set it only
    # for language files kept somewhere else.
    OCR_TESSDATA_DIR: Path | None = None

    # Language, as the traineddata filename. "eng" is what the installer ships
    # by default; several combine the way Tesseract wants it, "eng+deu".
    OCR_LANGUAGE: str = Field(default="eng", min_length=1)

    # 7 = single text line (what a meter is); 6 = block, 11 = scattered, 3 = page.
    OCR_PSM: int = Field(default=7, ge=0, le=13)
    # 3 = "whatever is available", which on a 5.x install is the LSTM engine.
    OCR_OEM: int = Field(default=3, ge=0, le=3)

    # Restricts recognized characters; empty means no restriction.
    OCR_CHAR_WHITELIST: str = ""

    # --- preprocessing ----------------------------------------------------
    # Tesseract wants ~30px glyphs; measured on this project's captures, 1x is
    # illegible and 3x is where numbers appear.
    OCR_UPSCALE: float = Field(default=3.0, gt=0, le=10)
    OCR_GRAYSCALE: bool = True
    # Stretches the crop's range to full black-to-white; meters drawn over
    # artwork rarely reach either end on their own.
    OCR_AUTOCONTRAST: bool = True
    # Tesseract 5 copes with light-on-dark text, so off by default.
    OCR_INVERT: bool = False
    # Binarize at this grey level (0-255); unset leaves Tesseract's own adaptive
    # thresholding, usually better on anti-aliased glyphs.
    OCR_THRESHOLD: int | None = Field(default=None, ge=0, le=255)
    # Reported to the engine instead of letting it guess DPI from a small crop.
    OCR_DPI: int | None = Field(default=300, ge=70, le=2400)

    # --- limits -----------------------------------------------------------
    # A read is a subprocess that can hang; past this it's killed and the
    # request fails rather than holding the connection open.
    OCR_TIMEOUT_SECONDS: float = Field(default=15.0, gt=0)
    # Width of the live-read screenshot. Unset means native resolution: unlike
    # an event-capture screenshot, this frame exists only to be read.
    OCR_SCREENSHOT_WIDTH: int | None = Field(default=None, ge=8, le=4096)

    # --- orb numbers (PaddleOCR) ------------------------------------------
    # The figure printed on a symbol orb is read by PaddleOCR instead of
    # Tesseract, and *only* that: meters, named regions and whole frames stay on
    # Tesseract. Measured on this project's own tiles, Tesseract reads nothing at
    # all off dataset/SC/r1c4.png where Paddle reads 160 at 0.9998.
    #
    # false falls the orb reader back to Tesseract, for a host without Paddle
    # installed (it needs Python 3.13 or lower) or to compare the two engines.
    OCR_ORB_PADDLE_ENABLED: bool = True

    # PaddleOCR's language pack. "en" is the Latin-digit recogniser; the figures
    # on an orb are digits, so this is not the same choice as OCR_LANGUAGE.
    OCR_ORB_PADDLE_LANGUAGE: str = Field(default="en", min_length=1)

    # No upscaling, and this is what decides how long the orb step takes. Unlike
    # Tesseract (OCR_UPSCALE above, 3x, because it wants ~30px glyphs) Paddle's
    # detector resizes internally. Measured over all 131 written scatter tiles,
    # 1x reads 126 identically to 4x at 1.56s a tile against 10.07s, and the 5
    # that differ are jackpot banner text bleeding into the crop rather than
    # prizes. Raising this buys nothing and costs seconds per orb.
    OCR_ORB_PADDLE_UPSCALE: float = Field(default=1.0, gt=0, le=10)

    # Below this, a recognised string is noise off the artwork rather than a
    # prize. Paddle scores a real figure at ~0.999, so this rejects junk without
    # touching a genuine reading.
    OCR_ORB_PADDLE_MIN_CONFIDENCE: float = Field(default=0.5, ge=0.0, le=1.0)

    # Paddle runs in-process, so this bounds the worker thread rather than
    # killing a subprocess. Higher than OCR_TIMEOUT_SECONDS because the first
    # read of a process also builds the model.
    OCR_ORB_PADDLE_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)

    @property
    def ocr_tesseract_cmd(self) -> Path | None:
        """Absolute path to the engine, configured or discovered; ``None`` if missing."""
        configured = self.OCR_TESSERACT_CMD
        if configured is None:
            return discover_executable()
        resolved = configured.expanduser().resolve()
        if resolved.is_dir():
            return resolved / EXECUTABLE_NAME
        return resolved

    @property
    def ocr_tessdata_dir(self) -> Path | None:
        """Absolute path to the language files, when one is configured."""
        directory = self.OCR_TESSDATA_DIR
        return directory.expanduser().resolve() if directory is not None else None
