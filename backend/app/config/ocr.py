"""Tesseract OCR runtime settings.

Tesseract is an external program, not a Python package: the engine is installed
on the host and this backend shells out to it. So the one setting that matters is
where that program is, and the rest are the knobs it takes on the command line --
language, page-segmentation mode, engine mode -- plus the preprocessing applied
to a crop before it is handed over.

As with :mod:`app.config.obs` and :mod:`app.config.ideck` the environment names
stay flat (``OCR_*``) and :class:`app.config.runtime.Settings` inherits this
model, so callers keep the usual ``settings.OCR_*`` access.

**The executable is discovered, not required.** The Windows installer does not
put ``tesseract.exe`` on ``PATH``, so a setting that had to be filled in by hand
would be the normal case rather than the exception. :func:`discover_executable`
looks where the installers actually put it, and ``OCR_TESSERACT_CMD`` is there
for the install that is somewhere else. Nothing here fails when it is missing:
OCR is optional in the same way OBS is, and ``GET /api/ocr/status`` reports the
engine as unavailable rather than the service refusing to start.

**The defaults are the ones a meter needs, not the ones a document needs.** A
region cropped out of a game frame is one short line of large glyphs, often light
on dark, a few hundred pixels wide -- which is why ``OCR_PSM`` defaults to 7
(treat the image as a single text line) rather than to Tesseract's own 3 (a whole
page), and why the crop is enlarged before it is read. Per-region overrides live
in the game config's ``ocr`` block, because the right page-segmentation mode is a
property of the region, not of the deployment.
"""

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
    """Every place the engine is worth looking for, best first.

    ``PATH`` comes first, so a deliberately installed or shimmed ``tesseract``
    wins over an installer default that may be an older version.
    """
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

    # Page segmentation mode. 7 is "a single text line", which is what a meter
    # is; 6 for a block of several lines, 11 for scattered text with no layout,
    # 3 for a whole page. Per-region overrides belong in the game config.
    OCR_PSM: int = Field(default=7, ge=0, le=13)
    # OCR engine mode. 3 is "whatever is available", which on a 5.x install is
    # the LSTM engine.
    OCR_OEM: int = Field(default=3, ge=0, le=3)

    # Characters the engine is asked to restrict itself to; empty means no
    # restriction. Under the LSTM engine this is a strong hint rather than a
    # rule, and worth setting per region for a meter that is only ever digits.
    OCR_CHAR_WHITELIST: str = ""

    # --- preprocessing ----------------------------------------------------
    # A cropped meter is a few hundred pixels wide and Tesseract wants glyphs
    # around 30px tall, so the crop is enlarged before it is read. Measured on
    # this project's own captures: at 1x nothing legible comes back at all, and
    # 3x is where the numbers appear.
    OCR_UPSCALE: float = Field(default=3.0, gt=0, le=10)
    # Colour carries nothing for text and costs the engine work.
    OCR_GRAYSCALE: bool = True
    # Stretches the crop's own range to full black-to-white. Game meters are
    # drawn over artwork and rarely reach either end of the range on their own.
    OCR_AUTOCONTRAST: bool = True
    # Tesseract 5 copes with light-on-dark text, so inversion is off by default
    # and is here as a per-region knob for a region where it does not.
    OCR_INVERT: bool = False
    # Binarize at this grey level, 0-255. Unset leaves Tesseract's own adaptive
    # thresholding to do it, which is usually better on anti-aliased glyphs.
    OCR_THRESHOLD: int | None = Field(default=None, ge=0, le=255)
    # Reported to the engine instead of letting it guess from a small image,
    # which is what its "Estimating resolution as N" warnings are. A crop has no
    # meaningful DPI; this is the number that makes the guess a good one.
    OCR_DPI: int | None = Field(default=300, ge=70, le=2400)

    # --- limits -----------------------------------------------------------
    # A read is a subprocess, and a subprocess can hang. Past this it is killed
    # and the request fails, rather than the connection being held open.
    OCR_TIMEOUT_SECONDS: float = Field(default=15.0, gt=0)
    # Width of the OBS screenshot taken for a live read. Unset means native
    # resolution: unlike an event-capture screenshot, which is evidence and is
    # shrunk to keep runs small, this frame exists only to be read, and a pixel
    # thrown away here is one the engine never gets.
    OCR_SCREENSHOT_WIDTH: int | None = Field(default=None, ge=8, le=4096)

    @property
    def ocr_tesseract_cmd(self) -> Path | None:
        """Absolute path to the engine, configured or discovered.

        A directory is accepted for ``OCR_TESSERACT_CMD`` because that is the
        form the installer shows and the form people paste. ``None`` means no
        engine was found, which is a state to report rather than an error.
        """
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
