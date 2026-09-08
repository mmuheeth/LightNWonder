"""Read the text in an image with the Tesseract engine (a program, not a library, so
this wraps running it)."""

from __future__ import annotations

import dataclasses
import io
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

__all__ = [
    "OVERRIDE_KEYS",
    "OcrError",
    "OcrOptions",
    "OcrOptionsError",
    "OcrResult",
    "OcrUnavailableError",
    "OcrWord",
    "engine_languages",
    "engine_version",
    "parse_number",
    "parse_numbers",
    "parse_overrides",
    "preprocess",
    "read_file",
    "read_image",
]


class OcrError(RuntimeError):
    """The engine ran and could not produce a reading."""


class OcrUnavailableError(OcrError):
    """The engine is not installed where expected. Kept apart from
    :class:`OcrError`: this is fixed by installing it, not by retrying."""


class OcrOptionsError(ValueError):
    """An option is out of range, or names something that is not an option."""


# Every option a game config may override per region. Timeouts and the path to
# the engine are deliberately not among them: those are properties of the
# deployment, not of the part of the screen being read.
OVERRIDE_KEYS: tuple[str, ...] = (
    "language",
    "psm",
    "oem",
    "char_whitelist",
    "upscale",
    "grayscale",
    "autocontrast",
    "invert",
    "threshold",
    "dpi",
)

# level 5 rows in Tesseract's TSV are words; the levels above them are the page,
# blocks, paragraphs and lines those words sit in.
_WORD_LEVEL = 5
_TSV_COLUMNS = 12

# A run of digits with separators inside it, so "$1,234.56" is one number and
# "$1.20 $176" is two. Whitespace deliberately ends a number: a meter panel puts
# several values in one region, and reading two of them as one is worse than
# splitting a number the engine put a space inside.
_NUMBER = re.compile(r"[-+]?\d[\d.,]*")


@dataclass(frozen=True)
class OcrOptions:
    """How one region should be preprocessed and read."""

    language: str = "eng"
    psm: int = 7
    oem: int = 3
    char_whitelist: str = ""
    upscale: float = 3.0
    grayscale: bool = True
    autocontrast: bool = True
    invert: bool = False
    threshold: int | None = None
    dpi: int | None = 300
    timeout_seconds: float = 15.0
    tessdata_dir: Path | None = None

    def __post_init__(self) -> None:
        """Reject options the engine would refuse, or silently ignore."""
        if not self.language.strip():
            raise OcrOptionsError("language must name at least one traineddata file")
        if not 0 <= self.psm <= 13:
            raise OcrOptionsError(f"psm must be between 0 and 13, got {self.psm}")
        if not 0 <= self.oem <= 3:
            raise OcrOptionsError(f"oem must be between 0 and 3, got {self.oem}")
        if not 0 < self.upscale <= 10:
            raise OcrOptionsError(
                f"upscale must be greater than 0 and at most 10, got {self.upscale}"
            )
        if self.threshold is not None and not 0 <= self.threshold <= 255:
            raise OcrOptionsError(
                f"threshold must be a grey level between 0 and 255, got {self.threshold}"
            )
        if self.dpi is not None and not 70 <= self.dpi <= 2400:
            raise OcrOptionsError(f"dpi must be between 70 and 2400, got {self.dpi}")
        if self.timeout_seconds <= 0:
            raise OcrOptionsError(
                f"timeout_seconds must be greater than 0, got {self.timeout_seconds}"
            )

    def merged(
        self, overrides: Mapping[str, Any] | None, *, where: str = "ocr"
    ) -> OcrOptions:
        """Return these options with ``overrides`` applied over the top."""
        if not overrides:
            return self
        # parse_overrides already locates its own failures, so its message is
        # passed through rather than prefixed a second time.
        parsed = parse_overrides(overrides, where=where)
        try:
            return dataclasses.replace(self, **parsed)
        except OcrOptionsError as exc:
            raise OcrOptionsError(f"{where}: {exc}") from None

    def command_args(self) -> list[str]:
        """The flags this set of options becomes on Tesseract's command line."""
        args = [
            "-l",
            self.language,
            "--oem",
            str(self.oem),
            "--psm",
            str(self.psm),
        ]
        if self.tessdata_dir is not None:
            args += ["--tessdata-dir", str(self.tessdata_dir)]
        if self.dpi is not None:
            args += ["-c", f"user_defined_dpi={self.dpi}"]
        if self.char_whitelist:
            args += ["-c", f"tessedit_char_whitelist={self.char_whitelist}"]
        return args


DEFAULT_OPTIONS = OcrOptions()

# Which Python type each override key accepts, checked before the value reaches
# the dataclass so the message can name the key rather than the field.
_OVERRIDE_TYPES: Mapping[str, tuple[type, ...]] = {
    "language": (str,),
    "psm": (int,),
    "oem": (int,),
    "char_whitelist": (str,),
    "upscale": (int, float),
    "grayscale": (bool,),
    "autocontrast": (bool,),
    "invert": (bool,),
    "threshold": (int,),
    "dpi": (int,),
}

# Keys whose absence is meaningful: null means "leave the engine to decide",
# which is not the same as "not configured".
_NULLABLE_OVERRIDES = frozenset({"threshold", "dpi"})


def parse_overrides(raw: Any, *, where: str = "ocr") -> dict[str, Any]:
    """Narrow a decoded JSON object to a set of option overrides."""
    if not isinstance(raw, Mapping):
        raise OcrOptionsError(f"{where} must be a JSON object of OCR options")

    parsed: dict[str, Any] = {}
    for key, value in raw.items():
        expected = _OVERRIDE_TYPES.get(key) if isinstance(key, str) else None
        if expected is None:
            known = ", ".join(OVERRIDE_KEYS)
            raise OcrOptionsError(
                f"{where}: {key!r} is not an OCR option (options are: {known})"
            )
        if value is None:
            if key not in _NULLABLE_OVERRIDES:
                raise OcrOptionsError(f"{where}: {key!r} must not be null")
            parsed[key] = None
            continue
        # `bool` is an `int`, and `true` as a page-segmentation mode is a mistake.
        if isinstance(value, bool) is not (bool in expected) or not isinstance(
            value, expected
        ):
            names = " or ".join(kind.__name__ for kind in expected)
            raise OcrOptionsError(f"{where}: {key!r} must be {names}, got {value!r}")
        parsed[key] = value

    # Ranges are the dataclass's to judge, so they are judged by building it.
    try:
        dataclasses.replace(DEFAULT_OPTIONS, **parsed)
    except OcrOptionsError as exc:
        raise OcrOptionsError(f"{where}: {exc}") from None
    return parsed


@dataclass(frozen=True)
class OcrWord:
    """One word the engine recognised, and how sure it was."""

    text: str
    confidence: float
    """0-100, as the engine reports it."""

    left: int
    top: int
    width: int
    height: int

    @property
    def box(self) -> tuple[int, int, int, int]:
        """The word's rectangle as ``(left, top, right, bottom)``."""
        return self.left, self.top, self.left + self.width, self.top + self.height


@dataclass(frozen=True)
class OcrResult:
    """What one read produced."""

    text: str
    """The recognised text, words joined by spaces and lines by newlines."""

    words: tuple[OcrWord, ...]
    confidence: float | None
    """Mean word confidence, 0-100; ``None`` when nothing was recognised."""

    size: tuple[int, int]
    """Size of the image the engine was given, after preprocessing."""

    @property
    def numbers(self) -> tuple[Decimal, ...]:
        """Every number in the text, in reading order -- a meter panel is
        often one region with several values side by side."""
        return parse_numbers(self.text)

    @property
    def number(self) -> Decimal | None:
        """The first number in the text, if it has one."""
        numbers = self.numbers
        return numbers[0] if numbers else None


def preprocess(
    image: Image.Image, options: OcrOptions = DEFAULT_OPTIONS
) -> Image.Image:
    """Return the copy of ``image`` that will be handed to the engine."""
    prepared = image
    if options.grayscale or options.threshold is not None:
        # Thresholding needs a single channel, so it forces the conversion even
        # when greyscale was not asked for.
        if prepared.mode != "L":
            prepared = prepared.convert("L")
    elif prepared.mode not in ("RGB", "L"):
        # Palette and alpha images are converted rather than saved as-is: what
        # the engine makes of an alpha channel is not worth finding out.
        prepared = prepared.convert("RGB")

    if options.autocontrast:
        prepared = ImageOps.autocontrast(prepared)
    if options.invert:
        prepared = ImageOps.invert(prepared)
    if options.threshold is not None:
        limit = options.threshold
        prepared = prepared.point(lambda value: 255 if value >= limit else 0, mode="L")
    if options.upscale != 1.0:
        width = max(1, round(prepared.width * options.upscale))
        height = max(1, round(prepared.height * options.upscale))
        prepared = prepared.resize((width, height), Image.Resampling.LANCZOS)
    return prepared


def read_image(
    image: Image.Image,
    *,
    executable: Path | str,
    options: OcrOptions = DEFAULT_OPTIONS,
) -> OcrResult:
    """Read the text in ``image`` -- usually a region crop, but a whole frame
    works the same way."""
    prepared = preprocess(image, options)
    completed = _run(
        executable,
        ["-", "stdout", *options.command_args(), "tsv"],
        payload=_encode(prepared),
        timeout=options.timeout_seconds,
    )
    words, text = _parse_tsv(
        completed.stdout.decode("utf-8", "replace"), scale=options.upscale
    )
    return OcrResult(
        text=text,
        words=words,
        confidence=(
            sum(word.confidence for word in words) / len(words) if words else None
        ),
        size=prepared.size,
    )


def read_file(
    source: Path | str,
    *,
    executable: Path | str,
    options: OcrOptions = DEFAULT_OPTIONS,
) -> OcrResult:
    """Read the text in an image file. Opened, loaded and closed before the engine runs,
    so nothing holds a handle on a frame OBS may still be writing."""
    try:
        with Image.open(source) as image:
            image.load()
            return read_image(image, executable=executable, options=options)
    except OSError as exc:
        # Pillow's UnidentifiedImageError is an OSError, so "not an image" and
        # "unreadable" arrive by one door.
        raise OcrError(f"{source} could not be read as an image: {exc}") from exc


def engine_version(executable: Path | str, *, timeout: float = 10.0) -> str:
    """The engine's version line, e.g. ``tesseract v5.5.3``."""
    completed = _run(executable, ["--version"], timeout=timeout)
    first = completed.stdout.decode("utf-8", "replace").strip().splitlines()
    if not first:
        raise OcrError(f"{executable} reported no version")
    return first[0].strip()


def engine_languages(
    executable: Path | str, *, timeout: float = 10.0
) -> tuple[str, ...]:
    """The traineddata files the engine can see, sorted."""
    completed = _run(executable, ["--list-langs"], timeout=timeout)
    lines = completed.stdout.decode("utf-8", "replace").splitlines()
    # The first line is a sentence about where it looked; the rest are the
    # language names, one per line.
    return tuple(sorted(line.strip() for line in lines[1:] if line.strip()))


def parse_numbers(text: str) -> tuple[Decimal, ...]:
    """Every number in ``text``, as exact decimals."""
    numbers: list[Decimal] = []
    for match in _NUMBER.finditer(text):
        value = _to_decimal(match.group())
        if value is not None:
            numbers.append(value)
    return tuple(numbers)


def parse_number(text: str) -> Decimal | None:
    """The first number in ``text``, or ``None`` if it has none."""
    numbers = parse_numbers(text)
    return numbers[0] if numbers else None


# --- running the engine ---------------------------------------------------


def _run(
    executable: Path | str,
    args: Sequence[str],
    *,
    payload: bytes | None = None,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    """Run the engine once and return the finished process."""
    command = [str(executable), *args]
    try:
        completed = subprocess.run(
            command,
            input=payload,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise OcrUnavailableError(
            f"No Tesseract executable at {executable}. Install Tesseract OCR or "
            "set OCR_TESSERACT_CMD to where it is."
        ) from exc
    except PermissionError as exc:
        raise OcrUnavailableError(
            f"{executable} cannot be run: {exc.strerror or exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise OcrError(f"Tesseract did not finish within {timeout}s") from exc
    except OSError as exc:
        raise OcrError(f"Tesseract could not be started: {exc}") from exc

    if completed.returncode != 0:
        raise OcrError(
            f"Tesseract exited with {completed.returncode}: {_reason(completed.stderr)}"
        )
    return completed


def _encode(image: Image.Image) -> bytes:
    """The image as PNG bytes, ready for the engine's stdin. PNG rather than JPEG since
    lossy ringing around a glyph is exactly the noise that costs a digit."""
    buffer = io.BytesIO()
    try:
        image.save(buffer, format="PNG")
    except OSError as exc:
        raise OcrError(f"The image could not be encoded for OCR: {exc}") from exc
    return buffer.getvalue()


def _reason(stderr: bytes) -> str:
    """The last useful line of the engine's stderr -- Tesseract writes
    progress/resolution warnings there too, so the tail is what matters."""
    lines = [line.strip() for line in stderr.decode("utf-8", "replace").splitlines()]
    meaningful = [line for line in lines if line]
    return meaningful[-1] if meaningful else "no output on stderr"


def _parse_tsv(stdout: str, *, scale: float) -> tuple[tuple[OcrWord, ...], str]:
    """Tesseract's TSV word rows plus the text rebuilt from them. A malformed line costs
    a word, not the whole reading."""
    words: list[OcrWord] = []
    lines: list[list[str]] = []
    line_key: tuple[str, str, str] | None = None
    factor = scale if scale > 0 else 1.0

    for row in stdout.splitlines():
        columns = row.split("\t")
        if len(columns) != _TSV_COLUMNS or columns[0] == "level":
            continue
        try:
            if int(columns[0]) != _WORD_LEVEL:
                continue
            left, top, width, height = (int(value) for value in columns[6:10])
            confidence = float(columns[10])
        except ValueError:
            continue
        text = columns[11].strip()
        if not text or confidence < 0:
            continue

        words.append(
            OcrWord(
                text=text,
                confidence=confidence,
                left=round(left / factor),
                top=round(top / factor),
                width=round(width / factor),
                height=round(height / factor),
            )
        )
        # Words arrive in reading order, so a change of block, paragraph or line
        # number is where the text gains a newline.
        key = (columns[2], columns[3], columns[4])
        if line_key != key or not lines:
            lines.append([])
            line_key = key
        lines[-1].append(text)

    text = "\n".join(" ".join(line) for line in lines)
    return tuple(words), text


def _to_decimal(token: str) -> Decimal | None:
    """One matched number token as a decimal, or ``None`` if it is not one."""
    cleaned = re.sub(r"\s+", "", token).rstrip(".,")
    if not cleaned or not any(character.isdigit() for character in cleaned):
        return None

    sign = ""
    if cleaned[0] in "+-":
        sign, cleaned = cleaned[0], cleaned[1:]

    last_dot = cleaned.rfind(".")
    last_comma = cleaned.rfind(",")
    if last_dot < 0 and last_comma < 0:
        digits, fraction = cleaned, ""
    else:
        # Whichever separator comes last is the decimal point; everything before
        # it is grouping, whatever it was written with.
        cut = max(last_dot, last_comma)
        digits, fraction = cleaned[:cut], cleaned[cut + 1 :]
        # A lone separator with three digits after it is grouping, not a decimal
        # point: "$1,234" is not a fraction of a cent.
        if len(fraction) == 3 and (last_dot < 0 or last_comma < 0):
            digits, fraction = cleaned, ""

    digits = re.sub(r"[.,]", "", digits) or "0"
    fraction = re.sub(r"[.,]", "", fraction)
    if not digits.isdigit():
        return None
    try:
        # A whole number stays whole: Decimal("1234"), not Decimal("1234.0").
        return Decimal(f"{sign}{digits}.{fraction}" if fraction else f"{sign}{digits}")
    except InvalidOperation:
        return None
