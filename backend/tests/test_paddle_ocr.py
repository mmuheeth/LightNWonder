"""Reading the number on a symbol orb with PaddleOCR.

Most of this runs without PaddleOCR installed. ``FakePaddle`` replaces the built
engine, which is the only thing in :mod:`app.utils.paddle_ocr` that loads a
model, and answers with the shapes both Paddle 3.x and 2.x return -- enough to
exercise the option layering, the result parsing, the digit/confidence gates and
the fallback to Tesseract on a machine that has no Paddle at all.

The reads under *Against the real engine* run the installed PaddleOCR over this
project's own written orb tiles, skipped when it is not installed. Those are the
tests that prove the engine reads a figure Tesseract does not, which is the whole
reason the dependency is here.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from app.config.game_config import load_game_config
from app.config.ocr import discover_executable
from app.config.runtime import settings
from app.services import ocr as ocr_service
from app.utils import ocr, paddle_ocr

# A written orb carrying a figure, and the figure written on it. Committed
# fixtures, so this is a real tile and not a drawn approximation of one.
ORB_WITH_50 = Path("dataset/SC/SC_50.png")
ORB_WITH_160 = Path("dataset/SC/r1c4.png")
# A feature scatter: drawn with no prize on it at all.
ORB_WITH_NOTHING = Path("dataset/FG/FG_00000.png")


@pytest.fixture(autouse=True)
def _clear_engine() -> Any:
    """Never let a cached engine leak between tests."""
    paddle_ocr.reset()
    yield
    paddle_ocr.reset()


def orb(size: tuple[int, int] = (150, 140)) -> Image.Image:
    """A stand-in crop. Its content does not matter to the tests that fake the
    engine -- only that it is an image of a plausible size."""
    return Image.new("RGB", size, (210, 180, 90))


class FakePaddle:
    """Stands in for a built PaddleOCR by replacing the engine cache.

    :attr:`pages` is what ``predict`` returns; set :attr:`error` to make the read
    fail the way paddle does, with a bare ``Exception``.
    """

    def __init__(self, pages: Any = None) -> None:
        self.pages = pages if pages is not None else []
        self.error: Exception | None = None
        self.arrays: list[Any] = []

    def predict(self, array: Any) -> Any:
        self.arrays.append(array)
        if self.error is not None:
            raise self.error
        return self.pages


def install(monkeypatch: pytest.MonkeyPatch, fake: FakePaddle) -> FakePaddle:
    """Put ``fake`` in place of the engine that would otherwise be built."""
    monkeypatch.setattr(paddle_ocr, "_engine", fake, raising=False)
    return fake


def page(*words: tuple[str, float]) -> list[dict[str, list[Any]]]:
    """One Paddle 3.x result page: parallel text and score lists."""
    return [
        {
            "rec_texts": [text for text, _ in words],
            "rec_scores": [score for _, score in words],
        }
    ]


# --- options --------------------------------------------------------------


def test_defaults_are_the_measured_ones() -> None:
    """The defaults are the settings the readings in the docstring were taken at."""
    options = paddle_ocr.PaddleOptions()
    assert options.language == "en"
    assert options.min_digits == 2


def test_the_default_does_not_upscale() -> None:
    """**The setting that decides how long a spin takes.** Paddle's detector
    resizes internally, so upscaling only makes it scan a bigger picture: over
    all 131 written scatter tiles 1x read 126 of them identically to 4x, at
    1.56s a tile against 10.07s. A regression to 4x here is a 6x slower orb step
    for the same figures, which is why this is asserted rather than left to the
    dataclass."""
    assert paddle_ocr.PaddleOptions().upscale == 1.0
    assert settings.OCR_ORB_PADDLE_UPSCALE == 1.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("language", "  "),
        ("upscale", 0.0),
        ("upscale", 11.0),
        ("min_confidence", -0.1),
        ("min_confidence", 1.1),
        ("min_digits", 0),
        ("timeout_seconds", 0.0),
    ],
)
def test_impossible_options_are_refused(field: str, value: object) -> None:
    """An option the engine would ignore is refused where it is set, not silently."""
    with pytest.raises(ocr.OcrError):
        paddle_ocr.PaddleOptions(**{field: value})


# --- preprocessing --------------------------------------------------------


def test_preprocess_upscales_and_keeps_colour() -> None:
    """Colour is kept: Paddle reads gold-on-light digits that greyscaling loses."""
    prepared = paddle_ocr.preprocess(
        orb((100, 50)), paddle_ocr.PaddleOptions(upscale=3.0)
    )
    assert prepared.size == (300, 150)
    assert prepared.mode == "RGB"


def test_preprocess_leaves_the_crop_alone_at_1x() -> None:
    """No upscale means no resize, so a caller can hand over its own scaling."""
    prepared = paddle_ocr.preprocess(
        orb((80, 60)), paddle_ocr.PaddleOptions(upscale=1.0)
    )
    assert prepared.size == (80, 60)


def test_preprocess_flattens_a_palette_image() -> None:
    """A split written as a palette PNG still reaches the engine as RGB."""
    prepared = paddle_ocr.preprocess(orb().convert("P"))
    assert prepared.mode == "RGB"


# --- parsing what paddle returned -----------------------------------------


def test_reads_a_paddle_3_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 3.x shape -- parallel ``rec_texts``/``rec_scores`` lists."""
    install(monkeypatch, FakePaddle(page(("160", 0.9998))))
    result = paddle_ocr.read_image(orb())
    assert result.text == "160"
    assert result.words[0].confidence == pytest.approx(0.9998)
    assert result.numbers == (Decimal("160"),)


def test_reads_a_paddle_2_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 2.x shape -- ``[[box, (text, score)], ...]`` -- so an environment
    pinned to the older release is not a silent no-reading."""
    install(monkeypatch, FakePaddle([[[[[0, 0], [9, 9]], ("300", 0.97)]]]))
    result = paddle_ocr.read_image(orb())
    assert result.text == "300"
    assert result.number == Decimal("300")


def test_a_currency_prefix_is_not_part_of_the_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paddle reads the orb as ``$50``; the figure is 50."""
    install(monkeypatch, FakePaddle(page(("$50", 0.9997))))
    assert paddle_ocr.read_image(orb()).number == Decimal("50")


def test_no_text_reads_as_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An orb with no figure on it: no words, no confidence, no number."""
    install(monkeypatch, FakePaddle([]))
    result = paddle_ocr.read_image(orb())
    assert result.words == ()
    assert result.confidence is None
    assert result.numbers == ()


def test_confidence_is_the_best_word(monkeypatch: pytest.MonkeyPatch) -> None:
    """Several strings off one orb: the reported confidence is the best of them."""
    install(monkeypatch, FakePaddle(page(("50", 0.99), ("C", 0.42))))
    assert paddle_ocr.read_image(orb()).confidence == pytest.approx(0.99)


def test_malformed_lines_cost_a_word_not_the_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A line Paddle returned in a shape we do not know is skipped, and the
    readable ones still come back."""
    install(
        monkeypatch,
        FakePaddle([["nonsense", [[[0, 0]], ("240", 0.95)]]]),
    )
    assert paddle_ocr.read_image(orb()).number == Decimal("240")


def test_a_failed_read_is_an_ocr_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Paddle raises bare ``Exception``; callers get the same ``OcrError`` the
    Tesseract reader raises, so one pair of excepts covers both engines."""
    fake = FakePaddle()
    fake.error = Exception("the detector fell over")
    install(monkeypatch, fake)
    with pytest.raises(ocr.OcrError, match="the detector fell over"):
        paddle_ocr.read_image(orb())


def test_an_engine_without_predict_is_an_ocr_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build offering neither entry point is a clear failure, not an
    ``AttributeError`` from inside the reader."""
    install(monkeypatch, FakePaddle())
    monkeypatch.delattr(FakePaddle, "predict")
    with pytest.raises(ocr.OcrError, match="neither"):
        paddle_ocr.read_image(orb())


# --- the digit and confidence gates ---------------------------------------


def test_read_number_takes_a_confident_figure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install(monkeypatch, FakePaddle(page(("600", 0.999))))
    value, result = paddle_ocr.read_number(orb())
    assert value == Decimal("600")
    assert result.text == "600"


def test_a_single_digit_is_not_a_prize(monkeypatch: pytest.MonkeyPatch) -> None:
    """A prize is never one digit, so a stray digit off the artwork is refused --
    the same rule as ``app.services.ocr.TILE_MIN_DIGITS``."""
    install(monkeypatch, FakePaddle(page(("7", 0.99))))
    value, result = paddle_ocr.read_number(orb())
    assert value is None
    # Still visible as something read and refused, rather than as silence.
    assert result.text == "7"


def test_a_low_confidence_reading_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Below ``min_confidence`` a string is noise off the orb, not a figure."""
    install(monkeypatch, FakePaddle(page(("150", 0.2))))
    value, _ = paddle_ocr.read_number(orb())
    assert value is None


def test_the_first_believable_figure_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """A confident letter beside the number does not stop the number being read."""
    install(monkeypatch, FakePaddle(page(("C", 0.88), ("240", 0.99))))
    value, _ = paddle_ocr.read_number(orb())
    assert value == Decimal("240")


def test_an_orb_with_no_figure_reads_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary answer for a feature scatter: no value, and no error."""
    install(monkeypatch, FakePaddle([]))
    value, result = paddle_ocr.read_number(orb())
    assert value is None
    assert result.text == ""


# --- a jackpot tier instead of a figure -----------------------------------
#
# An orb carries either a prize figure or a tier name, and both are readings.
# **No list of tier names is hardcoded anywhere**, so these use invented words
# as well as real ones: the rule is "digits are a figure, letters are a label".


@pytest.mark.parametrize("tier", ["MAJOR", "MINI", "MINOR", "GRAND", "SUPER MEGA"])
def test_a_word_on_an_orb_reads_as_a_label(
    monkeypatch: pytest.MonkeyPatch, tier: str
) -> None:
    """Whatever word the game prints comes back, including one this project has
    never seen -- there is no tier list to fall off."""
    install(monkeypatch, FakePaddle(page((tier, 0.999))))
    value, label, result = paddle_ocr.read_prize(orb())
    assert value is None
    assert label == tier
    assert result.text == tier


def test_a_label_is_reported_uppercase(monkeypatch: pytest.MonkeyPatch) -> None:
    """The orb is drawn in caps; a lowercase read is the recogniser's doing."""
    install(monkeypatch, FakePaddle(page(("Major", 0.98))))
    _, label, _ = paddle_ocr.read_prize(orb())
    assert label == "MAJOR"


def test_decoration_is_stripped_from_a_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Punctuation Paddle picks out of the filigree is not part of the tier."""
    install(monkeypatch, FakePaddle(page(("|MINI.", 0.97))))
    _, label, _ = paddle_ocr.read_prize(orb())
    assert label == "MINI"


def test_a_figure_beats_a_tier_printed_beside_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``MINOR 669`` is an orb paying 669; the tier is the label it sits under.
    Numbers are looked for across every word before any word becomes a label."""
    install(monkeypatch, FakePaddle(page(("MINOR", 0.99), ("669", 0.99))))
    value, label, _ = paddle_ocr.read_prize(orb())
    assert value == Decimal("669")
    assert label is None


def test_a_single_letter_is_not_a_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stray letter off the artwork is noise, not a jackpot tier."""
    install(monkeypatch, FakePaddle(page(("L", 0.99))))
    value, label, _ = paddle_ocr.read_prize(orb())
    assert value is None
    assert label is None


def test_a_low_confidence_word_is_not_a_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The confidence floor applies to a label exactly as to a figure."""
    install(monkeypatch, FakePaddle(page(("MAJOR", 0.2))))
    _, label, _ = paddle_ocr.read_prize(orb())
    assert label is None


def test_an_empty_orb_has_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    """A feature scatter carries no figure and no tier, and that is not an error."""
    install(monkeypatch, FakePaddle([]))
    value, label, _ = paddle_ocr.read_prize(orb())
    assert value is None
    assert label is None


def test_read_number_still_answers_only_with_figures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`read_number` is kept for callers that want a number or nothing, so a tier
    must not start arriving through it as a surprise."""
    install(monkeypatch, FakePaddle(page(("MAJOR", 0.999))))
    value, _ = paddle_ocr.read_number(orb())
    assert value is None


def test_read_tile_carries_a_tier_through_the_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam reports the tier on `label`, leaves `text` null because it is not
    a figure, and still carries the confidence it was read at."""
    monkeypatch.setattr(settings, "OCR_ORB_PADDLE_ENABLED", True)
    install(monkeypatch, FakePaddle(page(("MAJOR", 0.9996))))

    def unreachable(*args: object, **kwargs: object) -> None:
        raise AssertionError("a tier is a reading, so Tesseract is not asked")

    monkeypatch.setattr(ocr, "read_image", unreachable)
    reading = ocr_service.read_tile(
        orb(), executable=Path("tesseract"), options=tile_options()
    )
    assert reading.label == "MAJOR"
    assert reading.text is None
    assert reading.confidence == pytest.approx(99.96)


# --- when paddle is not installed -----------------------------------------


def test_missing_paddle_is_unavailable_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not installed is a state with an actionable message, and the message says
    what to install and why 3.14 will not do."""

    def absent() -> tuple[Any, str]:
        raise ocr.OcrUnavailableError("PaddleOCR is not installed")

    monkeypatch.setattr(paddle_ocr, "_import_paddleocr", absent)
    assert paddle_ocr.available() is False
    with pytest.raises(ocr.OcrUnavailableError):
        paddle_ocr.read_image(orb())


def test_the_install_message_names_the_command() -> None:
    """The error a host without Paddle sees should be enough to fix it."""
    import sys

    real = sys.modules.pop("paddleocr", None)
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setitem(sys.modules, "paddleocr", None)
        with pytest.raises(ocr.OcrUnavailableError) as caught:
            paddle_ocr._import_paddleocr()
    finally:
        monkeypatch.undo()
        if real is not None:
            sys.modules["paddleocr"] = real
    message = str(caught.value)
    assert "pip install paddlepaddle" in message
    assert "paddleocr" in message


# --- the service seam -----------------------------------------------------
#
# `read_tile` is the one door an orb is read through, so these check that the
# engine actually swapped -- and that turning it off puts Tesseract back.


def tile_options() -> ocr.OcrOptions:
    """The Tesseract options an orb would otherwise be read with."""
    config = load_game_config(Path("app/config/game_config/games/FortuneOx.json"))
    return ocr_service.read_tile_options(config)


def test_read_tile_uses_paddle_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With Paddle on, the orb is read by Paddle and Tesseract is never run."""
    monkeypatch.setattr(settings, "OCR_ORB_PADDLE_ENABLED", True)
    install(monkeypatch, FakePaddle(page(("160", 0.9998))))

    def unreachable(*args: object, **kwargs: object) -> None:
        raise AssertionError("Tesseract should not be run for an orb")

    monkeypatch.setattr(ocr, "read_image", unreachable)
    reading = ocr_service.read_tile(
        orb(), executable=Path("tesseract"), options=tile_options()
    )
    assert reading.text == "160"
    # Rescaled to Tesseract's 0-100, so `confidence` means one thing either way.
    assert reading.confidence == pytest.approx(99.98)


def test_read_tile_reports_an_orb_with_no_figure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paddle running and finding nothing is a reading of nothing -- it must not
    fall through to Tesseract, whose ``psm 8`` would invent a digit."""
    monkeypatch.setattr(settings, "OCR_ORB_PADDLE_ENABLED", True)
    install(monkeypatch, FakePaddle([]))

    def unreachable(*args: object, **kwargs: object) -> None:
        raise AssertionError("a numberless orb must not reach Tesseract")

    monkeypatch.setattr(ocr, "read_image", unreachable)
    reading = ocr_service.read_tile(
        orb(), executable=Path("tesseract"), options=tile_options()
    )
    assert reading.text is None


def test_read_tile_falls_back_when_paddle_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Paddle on the host: the Tesseract voting reader still answers."""
    monkeypatch.setattr(settings, "OCR_ORB_PADDLE_ENABLED", True)

    def absent(*args: object, **kwargs: object) -> None:
        raise ocr.OcrUnavailableError("PaddleOCR is not installed")

    monkeypatch.setattr(paddle_ocr, "read_number", absent)
    calls: list[object] = []

    def tesseract(image: Image.Image, **kwargs: object) -> ocr.OcrResult:
        calls.append(image)
        return ocr.OcrResult(text="50", words=(), confidence=80.0, size=image.size)

    monkeypatch.setattr(ocr, "read_image", tesseract)
    reading = ocr_service.read_tile(
        orb(), executable=Path("tesseract"), options=tile_options()
    )
    assert calls, "Tesseract should have been asked instead"
    assert reading.text == "50"


def test_read_tile_falls_back_when_paddle_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paddle installed but broken is also Tesseract's turn, not a failed spin."""
    monkeypatch.setattr(settings, "OCR_ORB_PADDLE_ENABLED", True)

    def broken(*args: object, **kwargs: object) -> None:
        raise ocr.OcrError("the detector fell over")

    monkeypatch.setattr(paddle_ocr, "read_number", broken)

    def tesseract(image: Image.Image, **kwargs: object) -> ocr.OcrResult:
        return ocr.OcrResult(text="300", words=(), confidence=75.0, size=image.size)

    monkeypatch.setattr(ocr, "read_image", tesseract)
    reading = ocr_service.read_tile(
        orb(), executable=Path("tesseract"), options=tile_options()
    )
    assert reading.text == "300"


def test_read_tile_uses_tesseract_when_paddle_is_switched_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``OCR_ORB_PADDLE_ENABLED=false`` restores the previous behaviour exactly,
    without Paddle being consulted at all."""
    monkeypatch.setattr(settings, "OCR_ORB_PADDLE_ENABLED", False)

    def unreachable(*args: object, **kwargs: object) -> None:
        raise AssertionError("Paddle should not be consulted when switched off")

    monkeypatch.setattr(paddle_ocr, "read_number", unreachable)

    def tesseract(image: Image.Image, **kwargs: object) -> ocr.OcrResult:
        return ocr.OcrResult(text="600", words=(), confidence=70.0, size=image.size)

    monkeypatch.setattr(ocr, "read_image", tesseract)
    reading = ocr_service.read_tile(
        orb(), executable=Path("tesseract"), options=tile_options()
    )
    assert reading.text == "600"


def test_only_the_orb_moved_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    """A named screen region is still Tesseract's, Paddle enabled or not -- the
    whole scope of this change is the orb."""
    monkeypatch.setattr(settings, "OCR_ORB_PADDLE_ENABLED", True)

    def unreachable(*args: object, **kwargs: object) -> None:
        raise AssertionError("a meter must not be read with Paddle")

    monkeypatch.setattr(paddle_ocr, "read_number", unreachable)
    monkeypatch.setattr(paddle_ocr, "read_image", unreachable)

    def tesseract(image: Image.Image, **kwargs: object) -> ocr.OcrResult:
        return ocr.OcrResult(
            text="1,234.56", words=(), confidence=90.0, size=image.size
        )

    monkeypatch.setattr(ocr, "read_image", tesseract)
    result = ocr_service.read_crop(
        orb(), executable=Path("tesseract"), options=ocr.OcrOptions()
    )
    assert result.text == "1,234.56"


# --- against the real engine ----------------------------------------------

requires_paddle = pytest.mark.skipif(
    not paddle_ocr.available(),
    reason="PaddleOCR is not installed (needs Python 3.13 or lower)",
)
requires_tiles = pytest.mark.skipif(
    not ORB_WITH_160.is_file(), reason="the written orb tiles are not present"
)


@requires_paddle
@requires_tiles
@pytest.mark.parametrize(
    ("path", "expected"),
    [(ORB_WITH_50, Decimal("50")), (ORB_WITH_160, Decimal("160"))],
)
def test_the_real_engine_reads_a_written_orb(path: Path, expected: Decimal) -> None:
    """The figures on this project's own committed orbs, read by the real engine."""
    with Image.open(path) as image:
        image.load()
        value, result = paddle_ocr.read_number(image.copy())
    assert value == expected, f"read {result.text!r} off {path}"
    assert result.confidence is not None and result.confidence > 0.9


@requires_paddle
@requires_tiles
def test_the_real_engine_reads_no_figure_off_a_feature_scatter() -> None:
    """A feature scatter carries no prize, and Paddle says so rather than
    inventing a digit off the artwork."""
    if not ORB_WITH_NOTHING.is_file():
        pytest.skip("the feature-scatter tile is not present")
    with Image.open(ORB_WITH_NOTHING) as image:
        image.load()
        value, _ = paddle_ocr.read_number(image.copy())
    assert value is None


@requires_paddle
@requires_tiles
def test_the_real_engine_reads_an_orb_tesseract_cannot() -> None:
    """The measurement this dependency exists for: Tesseract's own tile options
    read nothing off r1c4, and Paddle reads 160. Skipped without Tesseract, since
    the comparison needs both."""
    executable = discover_executable()
    if executable is None:
        pytest.skip("Tesseract is not installed, so there is nothing to compare")
    with Image.open(ORB_WITH_160) as image:
        image.load()
        tile = image.copy()

    options = ocr.OcrOptions(
        psm=8,
        char_whitelist="0123456789",
        upscale=4.0,
        grayscale=True,
        autocontrast=True,
    )
    plain = ocr.read_image(tile, executable=executable, options=options)
    value, _ = paddle_ocr.read_number(tile)

    assert value == Decimal("160")
    assert plain.number != Decimal("160")
