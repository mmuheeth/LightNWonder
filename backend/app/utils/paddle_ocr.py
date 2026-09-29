"""Reads text with PaddleOCR: a prize figure or jackpot-tier name off a symbol
orb, a caption line, a cash-meter cell, or an arbitrary named region -- the
one OCR engine this project uses, since Tesseract measurably could not read
an orb (it returned nothing on `SC/r1c4.png` where Paddle gets 160 @0.9998,
and scored a correct `50` at 0.0 confidence vs Paddle's 0.9997) and every
other reading moved here with it. A library, not a program: "installed" is an
import, and building the model (not finding an executable) is the expensive
part, hence cached."""

from __future__ import annotations

import contextlib
import dataclasses
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from PIL import Image

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

__all__ = [
    "OVERRIDE_KEYS",
    "OcrError",
    "OcrOptionsError",
    "OcrUnavailableError",
    "PaddleLineOptions",
    "PaddleOptions",
    "PaddleResult",
    "PaddleWord",
    "available",
    "engine_version",
    "parse_number",
    "parse_numbers",
    "parse_overrides",
    "prepare",
    "prepare_line",
    "read_image",
    "read_line",
    "read_number",
    "read_prize",
    "reset",
]


class OcrError(RuntimeError):
    """The engine ran and could not produce a reading."""


class OcrUnavailableError(OcrError):
    """The engine is not installed where expected. Kept apart from
    :class:`OcrError`: this is fixed by installing it, not by retrying."""


class OcrOptionsError(ValueError):
    """An option is out of range, or names something that is not an option."""


# A run of digits with separators inside it, so "$1,234.56" is one number and
# "$1.20 $176" is two. Whitespace deliberately ends a number: a meter region
# often holds several values, and merging two is worse than splitting one.
# Moved here from the deleted Tesseract wrapper it was born in -- parsing text
# into numbers has never been engine-specific.
_NUMBER = re.compile(r"[-+]?\d[\d.,]*")


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


@dataclass(frozen=True)
class PaddleOptions:
    """How one crop -- an orb, a meter cell, or a named region -- is
    preprocessed and read with the detect-then-recognise engine
    (:func:`read_image`)."""

    language: str = "en"

    # **No upscaling -- this decides how long a spin takes.** Tesseract needs
    # ~30px glyphs and upscales 4x for them; Paddle's detector resizes
    # internally and needs none of that. Measured over all 131 written scatter
    # tiles: 1x matches 4x on 126 of them at 1.56s/tile vs 10.07s (6.4x
    # faster); the 5 that differ are jackpot banner text bleeding into the
    # crop, none a prize. Raising this buys nothing and costs seconds per orb.
    upscale: float = 1.0

    # Below this, a recognised string is treated as noise off the artwork rather
    # than a figure. Paddle scores a real orb figure at ~0.999, so this rejects
    # junk without touching a genuine reading.
    min_confidence: float = 0.5

    # Minimum digits for a reading to count as a prize -- same reasoning as
    # `app.services.ocr.TILE_MIN_DIGITS`: a bare digit is what an empty orb
    # answers with. Waived when a currency mark is present (see
    # `_AMOUNT_MARKS`), which is what makes a one-digit `$5` a prize.
    min_digits: int = 2

    # Seconds one crop may take. Paddle runs in-process, so this bounds the
    # thread the caller put it on rather than killing a subprocess.
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        """Reject options the engine would refuse, or silently ignore."""
        if not self.language.strip():
            raise OcrError("language must name a PaddleOCR language")
        if not 0 < self.upscale <= 10:
            raise OcrError(
                f"upscale must be greater than 0 and at most 10, got {self.upscale}"
            )
        if not 0.0 <= self.min_confidence <= 1.0:
            raise OcrError(
                f"min_confidence must be between 0 and 1, got {self.min_confidence}"
            )
        if self.min_digits < 1:
            raise OcrError(f"min_digits must be at least 1, got {self.min_digits}")
        if self.timeout_seconds <= 0:
            raise OcrError(
                f"timeout_seconds must be greater than 0, got {self.timeout_seconds}"
            )

    def merged(self, overrides: Any, *, where: str = "ocr") -> PaddleOptions:
        """Return these options with a game config's or a request's
        ``overrides`` applied over the top -- validated by :func:`parse_overrides`,
        so a bad key or value is reported by name rather than surfacing as a
        confusing dataclass-construction error."""
        if not overrides:
            return self
        parsed = parse_overrides(overrides, where=where)
        try:
            return dataclasses.replace(self, **parsed)
        except OcrError as exc:
            raise OcrOptionsError(f"{where}: {exc}") from None


DEFAULT_OPTIONS = PaddleOptions()

# Every option a game config or a request may override for a named region.
# `min_digits` and `timeout_seconds` stay out for the reason Tesseract's own
# `OVERRIDE_KEYS` excluded its timeout: a property of the deployment, not of
# the screen region being read. Narrower than `PaddleOptions`' own field list
# on purpose -- a region has no digit-count floor to tune, since it isn't
# read as a prize.
OVERRIDE_KEYS: tuple[str, ...] = ("language", "upscale", "min_confidence")

_OVERRIDE_TYPES: Mapping[str, tuple[type, ...]] = {
    "language": (str,),
    "upscale": (int, float),
    "min_confidence": (int, float),
}


def parse_overrides(raw: Any, *, where: str = "ocr") -> dict[str, Any]:
    """Narrow a decoded JSON object to a set of region-reading option overrides."""
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
        # `bool` is an `int`, and `true` as an upscale factor is a mistake.
        if isinstance(value, bool) or not isinstance(value, expected):
            names = " or ".join(kind.__name__ for kind in expected)
            raise OcrOptionsError(f"{where}: {key!r} must be {names}, got {value!r}")
        parsed[key] = value

    # Ranges are the dataclass's to judge, so they are judged by building it.
    try:
        dataclasses.replace(DEFAULT_OPTIONS, **parsed)
    except OcrError as exc:
        raise OcrOptionsError(f"{where}: {exc}") from None
    return parsed


def _axis_box(raw: Any, *, scale: float = 1.0) -> tuple[int, int, int, int] | None:
    """A raw Paddle box as ``(left, top, right, bottom)`` crop pixels.

    Accepts either an axis-aligned box (3.x's ``rec_boxes``, one row per
    detected line) or a four-point polygon (2.x's own box shape, kept for the
    list-of-lines branch below) -- flattened and read as whichever length it
    turns out to be, rather than trusted by caller. ``scale`` undoes
    :func:`preprocess`'s upscale, the same way the deleted Tesseract reader's
    ``_parse_tsv`` divided its own boxes by ``factor``, so a box is always in
    the *original* crop's pixels regardless of what the engine was given.
    """
    try:
        flat: list[float] = []
        for item in raw:
            if isinstance(item, (list, tuple)):
                flat.extend(float(value) for value in item)
            else:
                flat.append(float(item))
    except (TypeError, ValueError):
        return None
    if len(flat) == 4:
        left, top, right, bottom = flat
    elif len(flat) >= 8 and len(flat) % 2 == 0:
        xs, ys = flat[0::2], flat[1::2]
        left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
    else:
        return None
    factor = scale if scale > 0 else 1.0
    return (
        round(left / factor),
        round(top / factor),
        round(right / factor),
        round(bottom / factor),
    )


@dataclass(frozen=True)
class PaddleWord:
    """One string the recogniser returned, and how sure it was."""

    text: str
    confidence: float
    """0-1, as Paddle reports it -- *not* Tesseract's 0-100."""

    box: tuple[int, int, int, int] | None = None
    """``(left, top, right, bottom)`` in crop pixels, from the detector's line
    box (``rec_boxes``) -- one box per recognised *line*, not per word, since
    that is the granularity Paddle's detector gives up without asking it for
    ``return_word_box`` (a heuristic sub-division of the line box this project
    has no use for). ``None`` on the recognise-only line path
    (:func:`read_line`), which never runs the detector and so never has a box
    to report."""


@dataclass(frozen=True)
class PaddleResult:
    """What one orb read produced."""

    text: str
    """Every recognised string, joined by spaces, before any filtering."""

    words: tuple[PaddleWord, ...]
    confidence: float | None
    """The best word's confidence, 0-1; ``None`` when nothing was recognised."""

    size: tuple[int, int]
    """Size of the image the engine was given, after preprocessing."""

    @property
    def numbers(self) -> tuple[Decimal, ...]:
        """Every number in the text, in reading order. Reuses Tesseract's parser
        so ``$50`` and ``1,234`` mean the same thing whichever engine read them."""
        return parse_numbers(self.text)

    @property
    def number(self) -> Decimal | None:
        """The first number in the text, if it has one."""
        numbers = self.numbers
        return numbers[0] if numbers else None


# Building a PaddleOCR is seconds of model loading, so it is built once. The lock
# is because the models are not documented as thread-safe and every caller here
# arrives on an `asyncio.to_thread` worker.
_engine: Any | None = None
_version: str | None = None
_lock = threading.Lock()

# The recognise-only reader is its own cached object, not a mode of the one
# above: they load different models and are wanted at the same time.
_recognizer: Any | None = None
_recognizer_model: str | None = None


def reset() -> None:
    """Drop the cached engines. Tests, and after a settings change."""
    global _engine, _version, _recognizer, _recognizer_model
    with _lock:
        _engine = None
        _version = None
        _recognizer = None
        _recognizer_model = None


def _import_paddleocr() -> tuple[Any, str]:
    """Import PaddleOCR, or explain it's missing. Torch is imported first on
    purpose: torch and paddle both bundle `libiomp5md.dll`, and paddle's copy
    is too old for torch, so importing paddle first breaks the symbol
    classifier with a `shm.dll` load failure. Order is fixed here, not
    incidental -- it's why both engines can share one process."""
    # No torch in this environment means no classifier, so no conflict to avoid.
    with contextlib.suppress(ImportError):
        import torch  # noqa: F401

    try:
        import paddleocr
    except ImportError as exc:
        raise OcrUnavailableError(
            "PaddleOCR is not installed, so the number on an orb cannot be read. "
            "Install it with: pip install paddlepaddle -f "
            "https://www.paddlepaddle.org.cn/whl/windows.html && pip install "
            "paddleocr  (needs Python 3.13 or lower -- paddlepaddle publishes no "
            "wheels for 3.14)"
        ) from exc
    return paddleocr, str(getattr(paddleocr, "__version__", "unknown"))


def available() -> bool:
    """Whether an orb number can be read at all. A state, not an error -- the
    caller reports "no engine" rather than failing the spin."""
    try:
        _import_paddleocr()
    except OcrUnavailableError:
        return False
    return True


def engine_version() -> str:
    """PaddleOCR's version string. Raises :class:`OcrUnavailableError`."""
    _, version = _import_paddleocr()
    return version


def _build(options: PaddleOptions) -> Any:
    """The recogniser, built once. `enable_mkldnn=False` is required, not
    tuning: paddle 3.3.1's default oneDNN path crashes the detector here with
    `NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support`
    (CPU kernels read the same orbs fine). The `use_*` document stages are off
    because an orb is a cropped tile, not a scanned page needing
    orientation/unwarping."""
    paddleocr, version = _import_paddleocr()
    try:
        engine = paddleocr.PaddleOCR(
            lang=options.language,
            enable_mkldnn=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    except Exception as exc:  # paddle raises bare Exception
        raise OcrError(f"PaddleOCR could not be started: {exc}") from exc

    global _version
    _version = version
    return engine


def _cached_engine(options: PaddleOptions) -> Any:
    """The shared engine, built on first use."""
    global _engine
    with _lock:
        if _engine is None:
            _engine = _build(options)
        return _engine


def prepare(options: PaddleOptions = DEFAULT_OPTIONS) -> str:
    """Build the recogniser now and report its version. Unlike :func:`available`
    (an import check), this does the actual model load -- worth doing up front
    so a broken install isn't mistaken for a hundred blank crops in a per-tile
    loop. Cheap after the first call since the engine is cached. Raises the
    same two exceptions a read does: :class:`OcrUnavailableError` /
    :class:`OcrError`."""
    _, version = _import_paddleocr()
    _cached_engine(options)
    return _version or version


# --- recognising a crop that is already one line --------------------------


@dataclass(frozen=True)
class PaddleLineOptions:
    """How one crop that is *known* to be a single line of text is read."""

    # The recognition model, and what decides whether reading a clip fits in
    # one request. Measured on a 194x37 caption crop, reading the same string
    # correctly each time: Paddle's default PP-OCRv6_medium_rec is 1.468
    # s/frame @0.991 vs this one's 0.064 s/frame @0.974 -- 23x faster, which
    # over a 90-frame/90s clip is ~6s against ~132s (a timeout). Picked as the
    # fastest and most confident of the mobile models; a game drawing its
    # strip in another language wants a different model here, not engine.
    model_name: str = "en_PP-OCRv5_mobile_rec"

    # Left at 1x for the reason the orb reader's is: Paddle resizes internally.
    upscale: float = 1.0

    def __post_init__(self) -> None:
        if not self.model_name.strip():
            raise OcrError("model_name must name a PaddleOCR recognition model")
        if not 0 < self.upscale <= 10:
            raise OcrError(
                f"upscale must be greater than 0 and at most 10, got {self.upscale}"
            )


DEFAULT_LINE_OPTIONS = PaddleLineOptions()


def _build_recognizer(options: PaddleLineOptions) -> Any:
    """The recogniser, built once. No `enable_mkldnn=False` here unlike
    :func:`_build` -- that's needed to stop the *detector* dying, and there's
    no detector on this path. Paddle's oneDNN defaults read a caption fine and
    are what the timings above were measured with."""
    paddleocr, _ = _import_paddleocr()
    try:
        return paddleocr.TextRecognition(model_name=options.model_name)
    except Exception as exc:  # paddle raises bare Exception
        raise OcrError(
            f"PaddleOCR could not start the recognition model "
            f"{options.model_name!r}: {exc}"
        ) from exc


def _cached_recognizer(options: PaddleLineOptions) -> Any:
    """The shared recogniser, rebuilt when the model asked for changes."""
    global _recognizer, _recognizer_model
    with _lock:
        if _recognizer is None or _recognizer_model != options.model_name:
            _recognizer = _build_recognizer(options)
            _recognizer_model = options.model_name
        return _recognizer


def prepare_line(options: PaddleLineOptions = DEFAULT_LINE_OPTIONS) -> str:
    """Build the recogniser now and report the model that will read -- the
    recognise-only counterpart of :func:`prepare`, for the same reason (a
    failed build inside a loop otherwise looks like a blank crop)."""
    _import_paddleocr()
    _cached_recognizer(options)
    return options.model_name


def _line_words(pages: Any) -> tuple[PaddleWord, ...]:
    """The recognised string out of what ``TextRecognition`` returns: a list of
    dicts carrying ``rec_text``/``rec_score``, one per image given."""
    words: list[PaddleWord] = []
    for page in pages or ():
        if not isinstance(page, dict):
            continue
        text = str(page.get("rec_text") or "").strip()
        if text:
            words.append(
                PaddleWord(text=text, confidence=float(page.get("rec_score") or 0.0))
            )
    return tuple(words)


def read_line(
    image: Image.Image, *, options: PaddleLineOptions = DEFAULT_LINE_OPTIONS
) -> PaddleResult:
    """Reads a crop already cut to one line of text, skipping detection since
    the caller cropped exactly the caption (same reason these crops use
    Tesseract `psm 7`). Detection still matters for an orb, where a figure
    sits amid artwork. Returns the same :class:`PaddleResult` shape as a
    detected read, whole line as one "word"; raises the same two exceptions."""
    prepared = image if image.mode == "RGB" else image.convert("RGB")
    if options.upscale != 1.0:
        width = max(1, round(prepared.width * options.upscale))
        height = max(1, round(prepared.height * options.upscale))
        prepared = prepared.resize((width, height), Image.Resampling.LANCZOS)

    recognizer = _cached_recognizer(options)
    try:
        pages = recognizer.predict(_as_array(prepared))
    except Exception as exc:  # paddle raises bare Exception
        raise OcrError(f"PaddleOCR failed to read the line: {exc}") from exc

    words = _line_words(pages)
    return PaddleResult(
        text=" ".join(word.text for word in words),
        words=words,
        confidence=max((word.confidence for word in words), default=None),
        size=prepared.size,
    )


def preprocess(
    image: Image.Image, options: PaddleOptions = DEFAULT_OPTIONS
) -> Image.Image:
    """Prepares an image for the engine -- far lighter than Tesseract's: no
    greyscale/autocontrast/threshold, since Paddle trained on colour photos and
    reads gold-on-light digits as-is (the flattening Tesseract needs is what
    loses them). Upscaling stays, since the detector still needs a
    big-enough region."""
    prepared = image if image.mode == "RGB" else image.convert("RGB")
    if options.upscale != 1.0:
        width = max(1, round(prepared.width * options.upscale))
        height = max(1, round(prepared.height * options.upscale))
        prepared = prepared.resize((width, height), Image.Resampling.LANCZOS)
    return prepared


def _as_array(image: Image.Image) -> np.ndarray[Any, Any]:
    """The image as the BGR array Paddle expects. Imported here rather than at
    module scope so a machine without Paddle need not have numpy either."""
    try:
        import numpy
    except ImportError as exc:  # pragma: no cover - numpy ships with paddle
        raise OcrUnavailableError(
            f"numpy is required to read an orb with PaddleOCR: {exc}"
        ) from exc
    # Pillow is RGB and Paddle, like OpenCV, wants BGR.
    return numpy.asarray(image)[:, :, ::-1]


def _words(pages: Any, *, scale: float = 1.0) -> tuple[PaddleWord, ...]:
    """Extracts recognised strings from whatever shape Paddle returned -- 3.x
    gives parallel `rec_texts`/`rec_scores`/`rec_boxes` dicts, 2.x gives
    `[[box, (text, score)], ...]`; both are read so an env pinned to the older
    release isn't a silent no-reading. ``scale`` is the preprocessing upscale
    a box needs undone to land back in the original crop's pixels -- see
    :func:`_axis_box`."""
    words: list[PaddleWord] = []
    for page in pages or ():
        if isinstance(page, dict):
            texts = page.get("rec_texts") or ()
            scores = page.get("rec_scores") or ()
            boxes = page.get("rec_boxes")
            for index, (text, score) in enumerate(zip(texts, scores, strict=False)):
                cleaned = str(text).strip()
                if not cleaned:
                    continue
                raw_box = (
                    boxes[index] if boxes is not None and index < len(boxes) else None
                )
                box = _axis_box(raw_box, scale=scale) if raw_box is not None else None
                words.append(PaddleWord(text=cleaned, confidence=float(score), box=box))
            continue
        for line in page or ():
            # [box, (text, score)] -- anything else is not a line we know.
            if not isinstance(line, (list, tuple)) or len(line) < 2:
                continue
            payload = line[1]
            if not isinstance(payload, (list, tuple)) or len(payload) < 2:
                continue
            cleaned = str(payload[0]).strip()
            if cleaned:
                box = _axis_box(line[0], scale=scale)
                words.append(
                    PaddleWord(text=cleaned, confidence=float(payload[1]), box=box)
                )
    return tuple(words)


def _predict(engine: Any, array: np.ndarray[Any, Any]) -> Any:
    """Run the recogniser, over whichever entry point this Paddle offers.
    ``predict`` is 3.x; ``ocr`` is 2.x and still present as an alias."""
    call = getattr(engine, "predict", None) or getattr(engine, "ocr", None)
    if call is None:
        raise OcrError(
            "This PaddleOCR build offers neither .predict() nor .ocr(), so an orb "
            "cannot be read with it"
        )
    try:
        return call(array)
    except Exception as exc:  # paddle raises bare Exception
        raise OcrError(f"PaddleOCR failed to read the crop: {exc}") from exc


def read_image(
    image: Image.Image, *, options: PaddleOptions = DEFAULT_OPTIONS
) -> PaddleResult:
    """Reads every line of text Paddle's detector finds in a crop -- an orb's
    figure, or (with the detector's own boxes reported on each
    :class:`PaddleWord`) a named region of arbitrary text. Raises
    :class:`OcrUnavailableError` / :class:`OcrError`."""
    prepared = preprocess(image, options)
    engine = _cached_engine(options)
    pages = _predict(engine, _as_array(prepared))
    words = _words(pages, scale=options.upscale)
    return PaddleResult(
        text=" ".join(word.text for word in words),
        words=words,
        confidence=max((word.confidence for word in words), default=None),
        size=prepared.size,
    )


def read_number(
    image: Image.Image, *, options: PaddleOptions = DEFAULT_OPTIONS
) -> tuple[Decimal | None, PaddleResult]:
    """Prize figure on one orb, as `(value, result)`. `value` is `None` for a
    figure-less orb (ordinary for a scatter, not an error) -- Paddle returns
    no text there where Tesseract's `psm 8` would invent a digit. Must clear
    both `min_confidence` and `min_digits` to count; the full result stays
    diagnosable either way."""
    value, _, result = read_prize(image, options=options)
    return value, result


# Characters that are decoration rather than part of what the orb says: the
# currency mark, the thousands/decimal separators a figure is drawn with, and
# the punctuation Paddle picks out of the filigree.
_STRIPPED = " \t\r\n$€£¥.,:;!|/\\-_'\"()[]{}*"

# Marks that make a *one-digit* reading a figure. `min_digits` exists because
# a figure-less orb's stray reading was, every time measured, a bare single
# digit -- but the filigree never draws a currency sign, so one in the text is
# evidence of an amount, not noise (`$5` is a real $5 prize, and refusing it
# would drop a figure read at full confidence). Currency marks only,
# deliberately: a decimal point isn't included, since a figure drawn with one
# already clears `min_digits` on its own digits (`5.00` is three).
_AMOUNT_MARKS = "$€£¥"


def _is_amount(text: str) -> bool:
    """Whether ``text`` carries a currency mark, i.e. whether a single digit in it
    is a prize rather than a scrap off the artwork."""
    return any(mark in text for mark in _AMOUNT_MARKS)


def read_prize(
    image: Image.Image, *, options: PaddleOptions = DEFAULT_OPTIONS
) -> tuple[Decimal | None, str | None, PaddleResult]:
    """What one orb says, as `(value, label, result)`. Digits -> `value`; a
    word (`MAJOR`, `GRAND`, ...) -> `label`; neither present -> both null, no
    error. No tier-name list is hardcoded, deliberately -- any word reads as a
    label, so a new game's own wording just works. Numbers are checked across
    every word before a label is accepted, so `MINOR 669` reads as 669. A
    digit reading must clear `min_digits` unless it carries a currency mark
    (`_AMOUNT_MARKS`); everything must clear `min_confidence`."""
    result = read_image(image, options=options)
    believable = [
        word for word in result.words if word.confidence >= options.min_confidence
    ]

    # A figure first, wherever it sits: an orb printing both a tier and an amount
    # is paying the amount.
    for word in believable:
        marked = _is_amount(word.text)
        for number in parse_numbers(word.text):
            digits = sum(character.isdigit() for character in str(number))
            if digits >= options.min_digits or marked:
                return number, None, result

    # No figure, so whatever letters the orb carries are what it says.
    for word in believable:
        label = word.text.strip(_STRIPPED)
        # At least two letters, so a stray `L` off the filigree is not a tier.
        if sum(character.isalpha() for character in label) >= 2:
            return None, label.upper(), result

    return None, None, result
