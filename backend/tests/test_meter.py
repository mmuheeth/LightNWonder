"""Reading the five values off a cash-meter strip.

Most of this runs without Tesseract installed. ``FakeEngine`` replaces the one
function in :mod:`app.utils.ocr` that touches a subprocess and answers with real
TSV, keyed by how wide the crop it was handed is -- which is enough to exercise
the parts that are actually this project's: locating the numbers, fitting the row
band, filing each number under a field, and refusing to guess when one belongs to
none.

The geometry tests use no engine at all. Where a number *is* on a strip is a
question about pixels, and answering it with a fake reader would prove only that
the fake was consulted.

One group of tests uses the real engine over the project's own saved frames, and
skips when there is neither. Those are the only ones that can catch the thing a
fake cannot: that the values are *right*. They crop the frame themselves rather
than reading a saved crop, so the region and the content box it resolves against
are under test too -- a strip handed in finished hides both.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.config.game_config import GameConfigError, load_game_config
from app.config.ocr import EXECUTABLE_NAME, discover_executable
from app.core.config import settings
from app.schemas.meter import MeterMode
from app.services import meter as meter_service
from app.services import roi as roi_service
from app.utils import image_roi, meter, ocr

TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext"
)


def tsv(*words: tuple[str, float]) -> bytes:
    """The TSV Tesseract writes for one line of words."""
    rows = [
        TSV_HEADER,
        "1\t1\t0\t0\t0\t0\t0\t0\t600\t90\t-1\t",
        "2\t1\t1\t0\t0\t0\t30\t12\t540\t30\t-1\t",
        "3\t1\t1\t1\t0\t0\t30\t12\t540\t30\t-1\t",
        "4\t1\t1\t1\t1\t0\t30\t12\t540\t30\t-1\t",
    ]
    for index, (text, confidence) in enumerate(words, start=1):
        rows.append(f"5\t1\t1\t1\t1\t{index}\t30\t12\t60\t30\t{confidence}\t{text}")
    return ("\n".join(rows) + "\n").encode("utf-8")


VERSION_STDOUT = b"tesseract v5.5.3.20260724\n leptonica-1.87.0\n"


class FakeEngine:
    """Answers every read from a table, so a test can say what each cell says.

    Keyed by the *width* of the PNG it is handed, because that is the one thing a
    caller can control from the outside: a strip is built with its cells at known
    widths and each one then answers for itself. Unknown widths read as nothing,
    which is how an empty WIN cell is expressed.
    """

    def __init__(self) -> None:
        self.by_width: dict[int, bytes] = {}
        self.default: bytes = tsv()
        self.reads = 0
        self.widths: list[int] = []
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []

    def __call__(
        self,
        executable: Path | str,
        args: Sequence[str],
        *,
        payload: bytes | None = None,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append([str(executable), *args])
        self.timeouts.append(timeout)
        if "--version" in args:
            return self._done(VERSION_STDOUT)
        if "--list-langs" in args:
            return self._done(b"List of available languages (1):\neng\n")
        self.reads += 1
        width = 0
        if payload is not None:
            import io

            with Image.open(io.BytesIO(payload)) as handed:
                width = handed.width
        self.widths.append(width)
        return self._done(self.by_width.get(width, self.default))

    @staticmethod
    def _done(stdout: bytes) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=["tesseract"], returncode=0, stdout=stdout, stderr=b""
        )


def strip(
    *,
    width: int = 600,
    height: int = 20,
    cells: Sequence[tuple[float, float]] = (),
    band: tuple[int, int] = (4, 16),
) -> Image.Image:
    """A synthetic strip: a dark bar with a bright block per cell.

    ``cells`` are ``(start, end)`` as fractions of the width, so a test can put a
    number where a field expects one -- or deliberately where none does.
    """
    image = Image.new("RGB", (width, height), (10, 10, 12))
    draw = ImageDraw.Draw(image)
    for start, end in cells:
        draw.rectangle(
            [round(start * width), band[0], round(end * width) - 1, band[1] - 1],
            fill=(255, 255, 255),
        )
    return image


# Fractions that land inside each field's default window. Deliberately three
# different widths, because the fake engine answers by the width of the crop it is
# handed -- that is how a test says "the cash cell reads $12.34 and the bet cell
# reads $0.50" without reaching inside the extractor.
CASH_CELL = (0.28, 0.35)
WIN_CELL = (0.49, 0.53)
BET_CELL = (0.68, 0.735)


@pytest.fixture
def engine_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin the engine to a file that exists but is never run."""
    executable = tmp_path / EXECUTABLE_NAME
    executable.write_bytes(b"")
    monkeypatch.setattr(settings, "OCR_TESSERACT_CMD", executable)
    return executable


@pytest.fixture
def fake_engine(
    engine_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FakeEngine]:
    fake = FakeEngine()
    monkeypatch.setattr(ocr, "_run", fake)
    yield fake


# --- geometry, with no engine at all ----------------------------------------


def test_ink_groups_finds_one_group_per_cell() -> None:
    image = strip(cells=[CASH_CELL, WIN_CELL, BET_CELL])
    groups = meter.ink_groups(image, (4, 16))
    assert len(groups) == 3
    centres = [(left + right) / 2 / image.width for left, right in groups]
    assert centres == sorted(centres), "groups must come back in reading order"


def test_ink_groups_ignores_specks_narrower_than_a_digit() -> None:
    """A cell corner is bright too, and must not be offered as a value."""
    image = strip(cells=[CASH_CELL, (0.70, 0.702)])
    assert len(meter.ink_groups(image, (4, 16))) == 1


def test_row_bands_separates_values_from_labels_below_them() -> None:
    """The whole point of the strict threshold: two lines, two candidates.

    A skin that prints labels under its cells must not offer only a band covering
    both -- reading them together is what turned 1.76 into 76.
    """
    image = strip(height=30, cells=[CASH_CELL], band=(4, 14))
    draw = ImageDraw.Draw(image)
    draw.rectangle([210, 20, 250, 26], fill=(255, 255, 255))  # the "label"

    bands = meter.row_bands(image)
    assert len(bands) >= 3, bands
    # A candidate covering the values without reaching into the label rows.
    assert any(top <= 4 and 14 <= bottom <= 19 for top, bottom in bands), bands
    # And the whole strip, offered last as the fallback.
    assert bands[-1] == (0, 30)


def test_row_bands_offers_the_whole_strip_when_nothing_is_lit() -> None:
    assert meter.row_bands(strip()) == ((0, 20),)


def test_grey_rejects_a_strip_with_no_area() -> None:
    with pytest.raises(meter.MeterError):
        meter.row_bands(Image.new("RGB", (0, 0)))


# --- mapping ----------------------------------------------------------------


def test_each_value_is_filed_under_the_field_whose_window_it_falls_in(
    fake_engine: FakeEngine, engine_path: Path
) -> None:
    image = strip(cells=[CASH_CELL, WIN_CELL, BET_CELL])
    widths = {
        round((end - start) * image.width) + 2 * meter.CLUSTER_PAD
        for start, end in (CASH_CELL, WIN_CELL, BET_CELL)
    }
    assert len(widths) == 3, "the three cells must be distinguishable by width"

    cash_w, win_w, bet_w = (
        round((end - start) * image.width) + 2 * meter.CLUSTER_PAD
        for start, end in (CASH_CELL, WIN_CELL, BET_CELL)
    )
    scale = 8
    fake_engine.by_width = {
        cash_w * scale: tsv(("$12.34", 96.0)),
        win_w * scale: tsv(("$1.00", 95.0)),
        bet_w * scale: tsv(("$0.50", 94.0)),
    }

    scan = meter.extract(image, executable=engine_path, band=(4, 16))
    assert str(scan.fields["cash"].value) == "12.34"
    assert str(scan.fields["win"].value) == "1.00"
    assert str(scan.fields["bet"].value) == "0.50"
    assert scan.unmapped == ()


def test_a_value_in_no_window_is_reported_unmapped_not_nudged_into_a_field(
    fake_engine: FakeEngine, engine_path: Path
) -> None:
    """The failure this design exists to make visible.

    A skin that orders its cells differently reads perfectly and means something
    else. Filing the number under the nearest field would look like success.
    """
    stray = (0.78, 0.85)
    image = strip(cells=[stray])
    fake_engine.default = tsv(("777", 96.0))

    scan = meter.extract(image, executable=engine_path, band=(4, 16))
    assert all(field.value is None for field in scan.fields.values())
    assert len(scan.unmapped) == 1
    assert str(scan.unmapped[0].value) == "777"
    assert 0.78 <= scan.unmapped[0].centre <= 0.86


def test_an_empty_win_cell_leaves_win_null_without_shifting_bet(
    fake_engine: FakeEngine, engine_path: Path
) -> None:
    """Eleven of the twenty saved strips look like this.

    Assigning left to right would call the bet a win. The windows are what stop
    that, so it is worth a test of its own.
    """
    image = strip(cells=[CASH_CELL, BET_CELL])
    cash_w = (round((CASH_CELL[1] - CASH_CELL[0]) * image.width) + 4) * 8
    bet_w = (round((BET_CELL[1] - BET_CELL[0]) * image.width) + 4) * 8
    fake_engine.by_width = {
        cash_w: tsv(("$50.00", 96.0)),
        bet_w: tsv(("$2.00", 96.0)),
    }

    scan = meter.extract(image, executable=engine_path, band=(4, 16))
    assert str(scan.fields["cash"].value) == "50.00"
    assert scan.fields["win"].value is None
    assert str(scan.fields["bet"].value) == "2.00"
    assert scan.unmapped == ()


def test_a_reading_below_the_floor_is_discarded_rather_than_reported(
    fake_engine: FakeEngine, engine_path: Path
) -> None:
    image = strip(cells=[CASH_CELL])
    fake_engine.default = tsv(("$9.99", meter.FLOOR - 1))
    scan = meter.extract(image, executable=engine_path, band=(4, 16))
    assert scan.fields["cash"].value is None


def test_reading_escalates_only_while_it_is_unconvincing(
    fake_engine: FakeEngine, engine_path: Path
) -> None:
    """A confident first answer must not cost eight more engine calls."""
    image = strip(cells=[CASH_CELL])
    fake_engine.default = tsv(("$12.34", 99.0))
    meter.extract(image, executable=engine_path, band=(4, 16))
    assert fake_engine.reads == 1

    fake_engine.reads = 0
    fake_engine.default = tsv(("$12.34", meter.CONFIDENT - 20))
    meter.extract(image, executable=engine_path, band=(4, 16))
    assert fake_engine.reads == sum(len(rung) for rung in meter.LADDER)


def test_a_reading_the_engine_declined_to_score_is_kept_not_discarded(
    fake_engine: FakeEngine, engine_path: Path
) -> None:
    """Tesseract scores a whitelisted word 0 however well it read it.

    The regression this pins: ranking on confidence alone starts at 0 and `0 > 0`
    is false, so the only transcription there was got thrown away and the field
    came back empty. On the real strips it was the *balance* that hit this --
    `$2,959.44`, verbatim, from six of the seven rungs, every one scored 0.
    """
    image = strip(cells=[CASH_CELL])
    fake_engine.default = tsv(("$2,959.44", meter.UNMEASURED))

    scan = meter.extract(image, executable=engine_path, band=(4, 16))
    assert str(scan.fields["cash"].value) == "2959.44"
    assert not scan.fields["cash"].measured


def test_a_scored_reading_outranks_an_unscored_one_for_the_same_cell(
    fake_engine: FakeEngine, engine_path: Path
) -> None:
    """Unscored is a fallback, never a preference: the ladder's later rungs must
    still be able to overrule a rung that read something the engine would not
    vouch for."""
    cash_width = round((CASH_CELL[1] - CASH_CELL[0]) * 600) + 2 * meter.CLUSTER_PAD
    fake_engine.by_width = {
        cash_width * 8: tsv(("$11.11", meter.UNMEASURED)),
        cash_width * 6: tsv(("$22.22", 80.0)),
    }
    fake_engine.default = tsv()

    scan = meter.extract(strip(cells=[CASH_CELL]), executable=engine_path, band=(4, 16))
    assert str(scan.fields["cash"].value) == "22.22"


def test_an_unscored_stray_is_not_reported_as_an_unmapped_value(
    fake_engine: FakeEngine, engine_path: Path
) -> None:
    """The exemption is for a reading with a field behind it. Both shipped skins
    carry digits on the strip that are not meter values -- the "CREDIT GAME
    ACTIVE" line and the change-denom button -- and reporting every unscored one
    would warn on every read."""
    image = strip(cells=[(0.78, 0.85)])
    fake_engine.default = tsv(("$7.00", meter.UNMEASURED))

    scan = meter.extract(image, executable=engine_path, band=(4, 16))
    assert scan.unmapped == ()


def _striped(columns: tuple[int, int], rows: tuple[int, int]) -> Image.Image:
    """A strip whose one cell is drawn as strokes rather than a solid block, so
    its rows are lit in part -- which is what tells a line of text from a rule."""
    image = Image.new("RGB", (600, 30), (10, 10, 12))
    draw = ImageDraw.Draw(image)
    for column in range(columns[0], columns[1], 3):
        draw.rectangle([column, rows[0], column, rows[1] - 1], fill=(255, 255, 255))
    return image


def test_a_band_that_cuts_through_its_glyphs_is_grown_back_over_the_strip() -> None:
    """One band serves three cells of different heights, so the tallest arrives
    clipped -- and a clipped glyph cannot be repaired by padding, only by going
    back to the strip for the rows left out."""
    columns = (100, 160)
    image = _striped(columns, (8, 24))

    # A band inside the glyphs, so they run into both of its edges. It reaches
    # out by the margin its own height earns, on each edge.
    reach = meter._margin(20 - 12)
    assert meter._grown(image, columns, (12, 20)) == (12 - reach, 20 + reach)

    # One the glyphs sit clear of is left exactly alone.
    assert meter._grown(image, columns, (4, 28)) == (4, 28)


def test_growth_stops_at_a_border_rather_than_running_into_the_label() -> None:
    """A row lit right across is the cell's own rule; the rows past it are the
    border and then the label, which is what the band exists to exclude."""
    columns = (100, 160)
    image = _striped(columns, (10, 20))
    draw = ImageDraw.Draw(image)
    draw.rectangle([columns[0], 8, columns[1] - 1, 9], fill=(255, 255, 255))

    top, _ = meter._grown(image, columns, (10, 20))
    assert top == 10, "the rule above the glyphs must not be taken in"


def test_a_glyph_flush_against_the_crop_edge_is_given_a_margin() -> None:
    """Tesseract reads a stroke touching the edge as one with an extra stroke;
    `105` came back `4105`. Padded only when the clearance is missing, since a
    fitted band already has room and padding one that does costs a digit."""
    flush = strip(width=40, height=12, cells=[(0.1, 0.9)], band=(0, 12))
    assert meter._padded(flush).size == (40, 12), "no background to pad with"

    tight = strip(width=40, height=14, cells=[(0.1, 0.9)], band=(0, 13))
    assert meter._padded(tight).height == 14 + 2 * meter._margin(14)

    roomy = strip(width=40, height=30, cells=[(0.1, 0.9)], band=(10, 20))
    assert meter._padded(roomy).size == (40, 30), "already clear of both edges"


def test_the_glyph_margin_scales_with_the_band_not_the_pixel() -> None:
    """The regression this pins is the one that caused the whole bug, in
    miniature: a clearance measured in pixels is ample on a small capture and
    thin on a large one. Four fixed rows let a 1.5x canvas weld a leading `1`
    onto `$1,250.00` and drag a correct reading below the floor."""
    assert meter._margin(60) > meter._margin(29) > meter._margin(11)
    # Same band as a share of two strips 2x apart in size: twice the clearance.
    assert meter._margin(58) == pytest.approx(2 * meter._margin(29), abs=1)
    # And never zero, however little there is to work with.
    assert meter._margin(1) >= meter.MIN_GLYPH_MARGIN


def test_the_longest_token_wins_so_a_cell_border_does_not_become_a_digit() -> None:
    """The engine welds a border onto a value as a leading 1 or comma."""
    assert str(meter._token("1$1,001.40")[0]) == "1001.40"
    assert meter._token("1$1,001.40")[1] == "$"
    assert str(meter._token(",$999.12")[0]) == "999.12"
    assert meter._token("")[0] is None


# --- the service ------------------------------------------------------------


def test_mode_is_credits_for_a_bare_integer_and_cash_for_an_amount(
    fake_engine: FakeEngine, engine_path: Path
) -> None:
    image = strip(cells=[CASH_CELL])

    fake_engine.default = tsv(("49883", 96.0))
    credits = meter_service.read(image, game="Fake")
    assert credits.mode is MeterMode.CREDITS
    assert credits.credits == 49883
    assert credits.cash is None
    assert credits.currency is None

    meter_service.reset()
    fake_engine.default = tsv(("$999.64", 96.0))
    cash = meter_service.read(image, game="Fake")
    assert cash.mode is MeterMode.CASH
    assert cash.cash == 999.64
    assert cash.credits is None
    assert cash.currency == "$"


def test_an_amount_with_an_unreadable_symbol_reports_the_currency_as_unknown(
    fake_engine: FakeEngine, engine_path: Path
) -> None:
    """The yen glyph these games draw reads as nothing at every mode and scale.

    Money with no symbol the engine would name is still money, and saying "no
    currency" would be a different and wrong claim.
    """
    image = strip(cells=[CASH_CELL])
    fake_engine.default = tsv(("999.19", 96.0))
    values = meter_service.read(image, game="Fake")
    assert values.mode is MeterMode.CASH
    assert values.currency == "?"


def test_the_fitted_band_is_cached_per_game_and_size(
    fake_engine: FakeEngine, engine_path: Path
) -> None:
    """Fitting costs extra scans, so it must happen once per skin, not per frame."""
    image = strip(cells=[CASH_CELL])
    fake_engine.default = tsv(("$5.00", 96.0))

    meter_service.read(image, game="Fake")
    first = fake_engine.reads
    fake_engine.reads = 0
    meter_service.read(image, game="Fake")
    assert fake_engine.reads <= first

    # A different size is a different band, so it is fitted again.
    fake_engine.reads = 0
    meter_service.read(strip(width=400, cells=[CASH_CELL]), game="Fake")
    assert fake_engine.reads > 0


def test_reset_drops_the_cached_bands(fake_engine: FakeEngine) -> None:
    image = strip(cells=[CASH_CELL])
    fake_engine.default = tsv(("$5.00", 96.0))
    meter_service.read(image, game="Fake")
    assert meter_service._bands
    meter_service.reset()
    assert not meter_service._bands


def test_a_missing_engine_is_reported_on_the_reading_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host with no Tesseract still gets its crop out of the ROI panel."""
    monkeypatch.setattr(
        settings, "OCR_TESSERACT_CMD", tmp_path / "nowhere" / EXECUTABLE_NAME
    )
    values = meter_service.read(strip(cells=[CASH_CELL]), game="Fake")
    assert values.error is not None
    assert "Tesseract" in values.error
    assert values.mode is MeterMode.UNKNOWN
    assert values.cash is None


def test_ocr_disabled_is_reported_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OCR_ENABLED", False)
    values = meter_service.read(strip(cells=[CASH_CELL]), game="Fake")
    assert values.error is not None
    assert "disabled" in values.error


def test_a_strip_with_no_area_is_reported_not_raised(
    fake_engine: FakeEngine,
) -> None:
    values = meter_service.read(Image.new("RGB", (0, 0)), game="Fake")
    assert values.error is not None
    assert values.mode is MeterMode.UNKNOWN


# --- against the real engine, over the project's own crops ------------------

SAVED_FRAMES = Path("obs-captured-files") / "screenshots"

GAME_CONFIGS = Path("app/config/game_config/games")


def _frame(name: str) -> Path:
    return SAVED_FRAMES / name


real_engine = pytest.mark.skipif(
    discover_executable() is None, reason="no Tesseract installed"
)

# Verified by eye, both skins, both modes, and a WIN cell that is empty as well as
# ones that are not. Named by *frame*, not by crop: a saved crop is only as good
# as the region semantics in force when it was written, and asserting against one
# would pin the strip's geometry to whenever it was last extracted.
#
# Two OBS canvases, deliberately. Every case below was a 1280x720 landscape canvas
# with the portrait game letterboxed into ~460 of it, until the canvas was set to
# the game's own 1080x1920 -- which put the same meter row on 1080x75 pixels
# instead of 460x29, and left every field of every frame unread with this suite
# still green. Nothing in a *fraction* changes with capture size, which is the
# whole appeal of them, so nothing here failed; what changed is that constants
# measured in pixels around those fractions no longer matched the pixels, and
# that a band overshooting into the labels by 3 rows went from illegible to
# legible. One resolution is not a measurement -- keep both.
REAL_STRIPS = [
    # 1080x1920 canvas: the game's own resolution, what the cabinet runs now.
    (
        "spin-2026-08-31_13-52-26-1-initial.png",
        "FortuneOx",
        "cash",
        "$",
        2509.44,
        None,
        100.00,
    ),
    # $1,250.00 read as `$4.7250.00` while its glyphs sat flush against the band.
    (
        "spin-2026-08-31_13-59-15-2-outcome.png",
        "FortuneOx",
        "cash",
        "$",
        2959.44,
        1250.00,
        100.00,
    ),
    (
        "spin-2026-08-31_11-45-31-3-collected.png",
        "FortuneOx",
        "cash",
        "$",
        871.44,
        4.00,
        20.00,
    ),
    # CASH here is the case the engine transcribes perfectly and scores 0; WIN is
    # one the padding fixed. Both fail without the other's fix.
    (
        "spin-2026-08-31_14-33-33-2-outcome.png",
        "FortuneOx",
        "cash",
        "$",
        2799.44,
        20.00,
        20.00,
    ),
    # `105` read as `4105`, and a six-figure balance the whitelist scores 0.
    (
        "spin-2026-08-31_14-52-17-2-outcome.png",
        "FortuneOx",
        "credits",
        None,
        287873,
        105,
        88,
    ),
    # 1280x720 canvas, the game letterboxed inside it.
    ("screenshot-1787208401603.png", "FortuneOx", "cash", "$", 1001.40, None, 1.76),
    ("screenshot-1787213890262.png", "FortuneOx", "credits", None, 49883, None, 88),
    ("screenshot-1787213930188.png", "FortuneOx", "credits", None, 49531, 10, 88),
    ("screenshot-1787224228160.png", "FortuneOx", "cash", "$", 995.60, 0.75, 0.88),
    ("screenshot-1787224299454.png", "FortuneOx", "credits", None, 99371, 140, 176),
    (
        "screenshot-1787217604256.png",
        "HuffNPuffLink",
        "cash",
        "$",
        999.00,
        0.05,
        1.00,
    ),
    (
        "screenshot-1787223377684.png",
        "HuffNPuffLink",
        "credits",
        None,
        99210,
        450,
        100,
    ),
]

REAL_STRIP_FIELDS = ("name", "game", "mode", "currency", "balance", "win", "bet")


@pytest.mark.parametrize(REAL_STRIP_FIELDS, REAL_STRIPS)
@real_engine
def test_real_strips_read_the_values_that_are_on_them(
    name: str,
    game: str,
    mode: str,
    currency: str | None,
    balance: float,
    win: float | None,
    bet: float | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The only tests that can catch a wrong number rather than a wrong shape.

    Verified by eye. Skipped where the saved frames are not present, since they
    are gitignored capture output rather than fixtures.

    Cropped here the way the ROI service crops -- from the frame, against its
    content box -- rather than read off an already-saved strip. A strip is the
    product of two things that can each be wrong, the region and the box it is
    resolved against, and a test fed the finished crop can see neither. That is
    what let the move to content-box fractions leave every field of both shipped
    skins unread with the suite still green.
    """
    path = _frame(name)
    if not path.is_file():
        pytest.skip(f"{path} is not present")
    monkeypatch.setattr(settings, "OCR_TESSERACT_CMD", None)

    # Through the declared band, which is what the ROI service uses. Fitting one
    # from the frame in hand is the fallback for a game nobody has measured, and
    # is measurably worse -- so it is not what these assertions should ride on.
    config = load_game_config(GAME_CONFIGS / f"{game}.json")
    with Image.open(path) as frame:
        image = frame.convert("RGB")
        roi = image_roi.named_roi(config.roi, "cash_meter")
        box, content = roi_service.resolve_box(roi, image)
        # Both skins put their meter across the full width of the game, so the
        # strip is exactly as wide as the content box. The one-line check that
        # would have caught this regression: a canvas-relative region resolves
        # to a plausible box *inside* the content box, just the wrong one.
        assert box[2] - box[0] == content.width
        values = meter_service.read(image.crop(box), game=game, profile=config.meter)

    assert values.error is None
    assert values.mode.value == mode
    assert values.currency == currency
    assert (values.cash if mode == "cash" else values.credits) == pytest.approx(balance)
    assert values.win == (pytest.approx(win) if win is not None else None)
    assert values.bet == (pytest.approx(bet) if bet is not None else None)
    assert values.unmapped == []


# --- across canvas sizes ----------------------------------------------------

# Where the reader actually works, as a multiple of the 1080x1920 canvas the
# cabinet runs. Every value below is a fraction of the strip, so the *geometry*
# is scale-free and the game's own layout is proportionally identical at every
# size (measured: the value rows sit at 0.276-0.533 of the strip on a 421-wide
# capture and 0.280-0.533 on a 1080-wide one). What limits the range is the
# constants that are still pixels, and how much detail Tesseract is given.
#
# 0.35x is the floor because MIN_GAP stops being able to tell a gap inside a
# value from the gap between two cells; below it the CASH and WIN cells weld and
# both come back null. 2x is simply the largest measured -- nothing is expected
# to break above it now the glyph margin is a share.
CANVAS_SCALES = (0.35, 0.5, 0.8, 1.0, 1.5, 2.0)


@pytest.mark.parametrize("scale", CANVAS_SCALES)
@real_engine
def test_the_meter_reads_across_canvas_sizes(
    scale: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression that started all of this was a canvas change, so the fix
    is only a fix if it survives another one.

    Rescaling a saved frame is not the same as re-capturing at that canvas -- it
    cannot show the game re-laying-out its own UI -- but it is exactly the right
    test for the failure that actually happened: constants measured in pixels
    around fractions that were already correct.
    """
    name, _, mode, _, balance, win, bet = REAL_STRIPS[4]
    path = _frame(name)
    if not path.is_file():
        pytest.skip(f"{path} is not present")
    monkeypatch.setattr(settings, "OCR_TESSERACT_CMD", None)

    config = load_game_config(GAME_CONFIGS / "FortuneOx.json")
    with Image.open(path) as frame:
        image = frame.convert("RGB")
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)),
            Image.LANCZOS,
        )
        roi = image_roi.named_roi(config.roi, "cash_meter")
        box, _ = roi_service.resolve_box(roi, image)
        values = meter_service.read(
            image.crop(box), game="FortuneOx", profile=config.meter
        )

    assert values.error is None
    assert (values.cash if mode == "cash" else values.credits) == pytest.approx(balance)
    assert values.win == pytest.approx(win)
    assert values.bet == pytest.approx(bet)


# --- the declared band ------------------------------------------------------


def test_a_declared_band_is_used_instead_of_fitting_one(
    fake_engine: FakeEngine,
) -> None:
    """A declared band must skip the fit entirely, not merely outrank it."""
    image = strip(height=20, cells=[CASH_CELL])
    fake_engine.default = tsv(("$5.00", 96.0))

    values = meter_service.read(image, game="Fake", profile={"band": (0.2, 0.8)})
    assert values.band == [4, 16]
    assert not meter_service._bands, "a declared band must not be cached as a guess"


def test_declared_windows_move_which_field_a_value_lands_in(
    fake_engine: FakeEngine,
) -> None:
    """The escape hatch for a skin that orders its cells differently."""
    image = strip(cells=[(0.78, 0.85)])
    fake_engine.default = tsv(("$7.00", 96.0))

    # With the defaults that value belongs to no field at all.
    astray = meter_service.read(image, game="Fake", profile={"band": (0.2, 0.8)})
    assert astray.cash is None
    assert len(astray.unmapped) == 1

    meter_service.reset()
    claimed = meter_service.read(
        image,
        game="Fake",
        profile={"band": (0.2, 0.8), "windows": {"cash": (0.74, 0.9)}},
    )
    assert claimed.cash == 7.0
    assert claimed.unmapped == []


def test_the_shipped_games_declare_a_band_and_windows_for_their_skin() -> None:
    """Both skins are measured, so neither depends on a fallback.

    Windows as well as the band: :data:`app.utils.meter.DEFAULT_WINDOWS` is the
    union of the two skins and is deliberately too loose for either -- one of
    HuffNPuffLink's own cells sits inside the default ``bet`` span. A shipped game
    inheriting it would read plausible wrong numbers rather than fail.
    """
    for game in ("FortuneOx", "HuffNPuffLink"):
        config = load_game_config(GAME_CONFIGS / f"{game}.json")
        top, bottom = config.meter["band"]
        assert 0.0 <= top < bottom <= 1.0

        windows = config.meter["windows"]
        assert set(windows) == set(meter.DEFAULT_WINDOWS)
        for low, high in windows.values():
            assert 0.0 <= low < high <= 1.0


@pytest.mark.parametrize(
    "block",
    [
        {"band": [0.7, 0.2]},
        {"band": [0.2]},
        {"band": [0.2, 1.4]},
        {"band": ["a", "b"]},
        {"band": [True, 0.9]},
        {"windows": {"cash": [0.6, 0.3]}},
        {"nonsense": 1},
    ],
)
def test_a_malformed_meter_block_fails_when_the_config_is_read(
    block: dict[str, object], tmp_path: Path
) -> None:
    """Named, at load time -- not as a meter that quietly reads the wrong rows."""
    config = tmp_path / "Broken.json"
    config.write_text(json.dumps({"name": "Broken", "meter": block}), encoding="utf-8")
    with pytest.raises(GameConfigError):
        load_game_config(config)


def test_a_game_with_no_meter_block_is_not_an_error(tmp_path: Path) -> None:
    """Declaring one is the remedy, not the requirement."""
    config = tmp_path / "Plain.json"
    config.write_text(json.dumps({"name": "Plain"}), encoding="utf-8")
    assert load_game_config(config).meter == {}
