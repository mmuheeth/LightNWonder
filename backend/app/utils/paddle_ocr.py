"""Read what is printed on a symbol orb with PaddleOCR -- a prize figure, or the
jackpot tier name an orb carries instead of one.

Deliberately narrow: this is the reader for **what an orb says** and nothing
else. Every other reading in the service -- meters, named screen regions, whole
frames -- stays on Tesseract in :mod:`app.utils.ocr`, which is why that module is
untouched by this one. Two engines are carried because the orb is the one crop
Tesseract measurably cannot read: on this project's own tiles it returns *nothing*
for ``dataset/SC/r1c4.png`` where Paddle reads ``160`` at 0.9998, and scores a
correct ``50`` on ``dataset/SC/SC_50.png`` at confidence 0.0 against Paddle's
0.9997.

Unlike Tesseract this is a Python library, not a program, so there is no
executable to find -- the "is it installed" question is an import, and the
expensive part is building the model, which is why the engine is cached.
"""

from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from PIL import Image

from app.utils.ocr import OcrError, OcrUnavailableError, parse_numbers

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

__all__ = [
    "PaddleLineOptions",
    "PaddleOptions",
    "PaddleResult",
    "PaddleWord",
    "available",
    "engine_version",
    "prepare",
    "prepare_line",
    "read_image",
    "read_line",
    "read_number",
    "read_prize",
    "reset",
]


@dataclass(frozen=True)
class PaddleOptions:
    """How one orb crop is preprocessed and read."""

    language: str = "en"

    # **No upscaling, and this is the setting that decides how long a spin
    # takes.** Tesseract needs ~30px glyphs, so the tile reader upscales 4x for
    # it; Paddle's detector resizes internally and needs nothing of the sort.
    # Measured over all 131 written scatter tiles, 1x reads 126 of them
    # identically to 4x at 1.56s a tile against 10.07s -- 6.4x faster for the
    # same figures. Of the 5 that differ, none is a prize: they are jackpot
    # banner text bleeding into the crop ('MINOR 669', '$1,004.00', '10F'),
    # which 4x reads *more* of. Raising this buys nothing and costs seconds per
    # orb.
    upscale: float = 1.0

    # Below this, a recognised string is treated as noise off the artwork rather
    # than a figure. Paddle scores a real orb figure at ~0.999, so this rejects
    # junk without touching a genuine reading.
    min_confidence: float = 0.5

    # A reading must be at least this many digits to be a prize. Same reasoning
    # as `app.services.ocr.TILE_MIN_DIGITS`: a prize is never one digit.
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


DEFAULT_OPTIONS = PaddleOptions()


@dataclass(frozen=True)
class PaddleWord:
    """One string the recogniser returned, and how sure it was."""

    text: str
    confidence: float
    """0-1, as Paddle reports it -- *not* Tesseract's 0-100."""


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
    """Import PaddleOCR, or explain that it is not installed.

    **torch is imported first, on purpose.** torch and paddle each bundle their
    own ``libiomp5md.dll`` (Intel OpenMP) and on Windows the first one loaded
    wins the name. Paddle's copy is too old for torch, so importing paddle first
    breaks the symbol classifier with a ``shm.dll`` load failure -- the classifier
    being the very thing that decides a tile is an orb worth reading. Importing
    torch first is harmless to paddle, so the order is fixed here rather than
    left to whichever service happens to run first. Not an optional nicety: it is
    why both engines can share one process at all.
    """
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
    """The recogniser, built once and kept.

    ``enable_mkldnn=False`` is required, not tuning: with paddle 3.3.1's default
    oneDNN path the detector dies on this machine with ``NotImplementedError:
    ConvertPirAttribute2RuntimeAttribute not support``. The CPU kernels read the
    same orbs correctly.

    The three ``use_*`` document stages are off because an orb is a cropped game
    tile, not a scanned page: there is no orientation to detect and no page to
    unwarp, and each stage is another model to download and run.
    """
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
    """Build the recogniser now, and report the version that will do the reading.

    :func:`available` answers "is it installed", which is an import; this
    answers "will it run", which is the model load. The two are worth keeping
    apart for a caller reading *many* crops in a row: a build that fails inside
    a per-crop loop is indistinguishable from a crop with no text on it, so one
    broken install comes back as a hundred blank frames instead of one error.
    Cheap to call again -- the engine is cached, so every read after the first
    is this same object.

    Raises the same two exceptions a read does:
    :class:`app.utils.ocr.OcrUnavailableError` when Paddle is not installed and
    :class:`app.utils.ocr.OcrError` when it is and will not start.
    """
    _, version = _import_paddleocr()
    _cached_engine(options)
    return _version or version


# --- recognising a crop that is already one line --------------------------


@dataclass(frozen=True)
class PaddleLineOptions:
    """How one crop that is *known* to be a single line of text is read."""

    # The recognition model, and **the setting that decides whether reading a
    # clip fits in one request**. Measured on this machine against a
    # caption-sized crop (194x37), reading the same string correctly each time:
    #
    #   PP-OCRv6_medium_rec (Paddle's default)  1.468 s/frame  @0.991
    #   PP-OCRv4_mobile_rec                     0.166 s/frame  @0.979
    #   PP-OCRv5_mobile_rec                     0.081 s/frame  @0.881
    #   en_PP-OCRv5_mobile_rec                  0.064 s/frame  @0.974
    #
    # 23x between the ends of that list, which over the ~90 frames of a 90s
    # clip is 132s against 6s -- the difference between a request that answers
    # and one that times out. The English model is the pick because a caption
    # is English words and it is both the fastest and, of the two mobile
    # models, the more confident. A game that draws its strip in another
    # language wants another model here, not another engine.
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
    """The recogniser, built once and kept.

    No ``enable_mkldnn=False`` here, unlike :func:`_build`: that is required to
    keep the *detector* from dying on this machine, and there is no detector on
    this path. Paddle's own defaults (oneDNN, 10 of 12 threads) read a caption
    correctly and are what the timings above were taken with.
    """
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
    """Build the recogniser now, and report the model that will read.

    The recognise-only counterpart of :func:`prepare`, and there for the same
    reason: a build that fails inside a per-crop loop is indistinguishable from
    a crop with no text on it.
    """
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
    """Read a crop that is already cut down to one line of text.

    Skips detection, which is not an optimisation so much as the removal of a
    step that had nothing to do: the caller cropped a named region whose whole
    content is the caption, so asking Paddle to *find* text inside it is asking
    it to rediscover the rectangle it was handed. The same reasoning puts
    Tesseract on ``psm 7`` ("a single text line") for these crops. Detection
    still earns its place on an orb, where the figure sits somewhere inside a
    tile that is mostly artwork.

    Returns the same :class:`PaddleResult` a detected read does, with the whole
    line as one "word", so a caller handles either the same way. Raises the
    same two exceptions.
    """
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
    """Return the copy of ``image`` that will be handed to the engine.

    Far less than Tesseract's preprocessing: no greyscale, no autocontrast, no
    thresholding. Paddle's recogniser was trained on colour photographs of text
    and reads gold-on-light digits as they are -- the flattening that Tesseract
    needs is what loses them. Upscaling is kept, because the detector still needs
    a region big enough to find.
    """
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


def _words(pages: Any) -> tuple[PaddleWord, ...]:
    """The recognised strings out of whatever shape this Paddle returned.

    Paddle 3.x yields dicts of parallel ``rec_texts``/``rec_scores`` lists; 2.x
    yielded ``[[box, (text, score)], ...]``. Both are read so an environment
    pinned to the older release is not a silent no-reading.
    """
    words: list[PaddleWord] = []
    for page in pages or ():
        if isinstance(page, dict):
            texts = page.get("rec_texts") or ()
            scores = page.get("rec_scores") or ()
            for text, score in zip(texts, scores, strict=False):
                cleaned = str(text).strip()
                if cleaned:
                    words.append(PaddleWord(text=cleaned, confidence=float(score)))
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
                words.append(PaddleWord(text=cleaned, confidence=float(payload[1])))
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
    """Read the number on one orb crop.

    Raises :class:`app.utils.ocr.OcrUnavailableError` when Paddle is not
    installed and :class:`app.utils.ocr.OcrError` when it ran and could not
    produce a reading -- the same two exceptions the Tesseract reader raises, so
    a caller handles one pair either way.
    """
    prepared = preprocess(image, options)
    engine = _cached_engine(options)
    pages = _predict(engine, _as_array(prepared))
    words = _words(pages)
    return PaddleResult(
        text=" ".join(word.text for word in words),
        words=words,
        confidence=max((word.confidence for word in words), default=None),
        size=prepared.size,
    )


def read_number(
    image: Image.Image, *, options: PaddleOptions = DEFAULT_OPTIONS
) -> tuple[Decimal | None, PaddleResult]:
    """The prize figure on one orb, as ``(value, result)``.

    ``value`` is ``None`` for an orb carrying no figure, which is the ordinary
    answer for a feature scatter and not an error -- Paddle returns no text at
    all for those, where Tesseract's ``psm 8`` would invent a digit. The full
    result comes back either way so a rejected reading stays diagnosable.

    A candidate must clear both ``min_confidence`` and ``min_digits`` to count.
    """
    value, _, result = read_prize(image, options=options)
    return value, result


# Characters that are decoration rather than part of what the orb says: the
# currency mark, the thousands/decimal separators a figure is drawn with, and
# the punctuation Paddle picks out of the filigree.
_STRIPPED = " \t\r\n$€£¥.,:;!|/\\-_'\"()[]{}*"


def read_prize(
    image: Image.Image, *, options: PaddleOptions = DEFAULT_OPTIONS
) -> tuple[Decimal | None, str | None, PaddleResult]:
    """What one orb says, as ``(value, label, result)``.

    An orb carries **either** a prize figure or a jackpot tier name, and both are
    real readings:

    * digits (``150``, ``$1,004.00``) come back as ``value``, ``label`` null;
    * a word (``MAJOR``, ``MINI``, ``GRAND`` -- whatever the game prints) comes
      back as ``label``, ``value`` null;
    * an orb drawn with neither, which is the ordinary case for a feature
      scatter, comes back as both null and no error.

    **No list of tier names is hardcoded**, deliberately. Which words a game
    prints on its jackpot orbs is the game's business, and a list here would
    silently drop the first tier a new game names differently. The rule is only
    "digits are a figure, letters are a label", so an orb saying ``SUPER MEGA``
    reads as ``SUPER MEGA`` without this module being taught about it.

    A digit reading must clear ``min_digits``; both must clear
    ``min_confidence``. Numbers are looked for across every word before any word
    is accepted as a label, so ``MINOR 669`` reads as the figure 669 rather than
    stopping at the tier beside it.
    """
    result = read_image(image, options=options)
    believable = [
        word for word in result.words if word.confidence >= options.min_confidence
    ]

    # A figure first, wherever it sits: an orb printing both a tier and an amount
    # is paying the amount.
    for word in believable:
        for number in parse_numbers(word.text):
            digits = sum(character.isdigit() for character in str(number))
            if digits >= options.min_digits:
                return number, None, result

    # No figure, so whatever letters the orb carries are what it says.
    for word in believable:
        label = word.text.strip(_STRIPPED)
        # At least two letters, so a stray `L` off the filigree is not a tier.
        if sum(character.isalpha() for character in label) >= 2:
            return None, label.upper(), result

    return None, None, result
