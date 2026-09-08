"""Reading text off a frame: the Tesseract wrapper, its configuration and its API.

Most of this runs without Tesseract installed. ``FakeTesseract`` replaces the one
function in :mod:`app.utils.ocr` that touches a subprocess and answers with real
TSV, which is enough to exercise everything above it -- preprocessing, the option
layering, the per-region config, the endpoints and every failure path -- on a
machine that has no engine at all.

Two kinds of test deliberately do not use the fake. The failure mapping in
``_run`` is checked against real processes (``sys.executable`` exiting non-zero,
and one that sleeps past its timeout), because that mapping is precisely the part
a fake would assume rather than prove. And the reads under *Against the real
engine* run the installed Tesseract over an image with known text in it, skipped
when there is none to run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from PIL import Image, ImageDraw, ImageFont

from app.config.game_config import GameConfigError, load_game_config, save_active_game
from app.config.ocr import EXECUTABLE_NAME, discover_executable
from app.config.runtime import settings
from app.schemas.obs import ScreenshotResult
from app.services import obs as obs_service
from app.services import ocr as ocr_service
from app.utils import ocr
from tests.asserts import assert_failure, assert_success

API = "/api/ocr"

# The cash meter as FortuneOx's shipped config declares it.
CASH_METER = [0.229264, 0.844468, 0.762349, 0.884554]
BET_METER = [0.1, 0.9, 0.4, 0.95]

TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext"
)


def tsv(*words: tuple[str, float, int], line: int = 1) -> bytes:
    """Build the TSV Tesseract writes for one line of words.

    Each word is ``(text, confidence, left)``; the rest of the box is filled in,
    since nothing here depends on a word's height. The page, block and paragraph
    rows a real run emits are included, because skipping them is part of what the
    parser has to do.
    """
    rows = [
        TSV_HEADER,
        "1\t1\t0\t0\t0\t0\t0\t0\t600\t90\t-1\t",
        "2\t1\t1\t0\t0\t0\t30\t12\t540\t30\t-1\t",
        "3\t1\t1\t1\t0\t0\t30\t12\t540\t30\t-1\t",
        f"4\t1\t1\t1\t{line}\t0\t30\t12\t540\t30\t-1\t",
    ]
    for index, (text, confidence, left) in enumerate(words, start=1):
        rows.append(
            f"5\t1\t1\t1\t{line}\t{index}\t{left}\t12\t60\t30\t{confidence}\t{text}"
        )
    return ("\n".join(rows) + "\n").encode("utf-8")


METER_TSV = tsv(("$842.94", 96.5, 30), ("$1.20", 91.0, 300))

VERSION_STDOUT = b"tesseract v5.5.3.20260724\n leptonica-1.87.0\n"
LANGS_STDOUT = b'List of available languages in "C:/tessdata/" (2):\neng\nosd\n'


class FakeTesseract:
    """Stands in for the engine by replacing :func:`app.utils.ocr._run`.

    Answers ``--version`` and ``--list-langs`` the way a 5.x install does, and
    every read with :attr:`stdout`. Set :attr:`error` to make the next read fail,
    or :attr:`fail_after` to let a given number of reads succeed first -- which is
    how "one region of two could not be read" is set up. :attr:`version_error` is
    the install that is present but broken.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.payloads: list[bytes | None] = []
        self.timeouts: list[float] = []
        self.stdout: bytes = METER_TSV
        self.error: Exception | None = None
        self.version_error: Exception | None = None
        self.fail_after: int | None = None
        self.reads = 0

    def __call__(
        self,
        executable: Path | str,
        args: Sequence[str],
        *,
        payload: bytes | None = None,
        timeout: float,
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append([str(executable), *args])
        self.payloads.append(payload)
        self.timeouts.append(timeout)
        if "--version" in args:
            if self.version_error is not None:
                raise self.version_error
            return self._completed(VERSION_STDOUT)
        if "--list-langs" in args:
            return self._completed(LANGS_STDOUT)

        self.reads += 1
        if self.error is not None and (
            self.fail_after is None or self.reads > self.fail_after
        ):
            raise self.error
        return self._completed(self.stdout)

    @staticmethod
    def _completed(stdout: bytes) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=["tesseract"], returncode=0, stdout=stdout, stderr=b""
        )

    def read_args(self) -> list[str]:
        """The command line of the last read, for asserting what was asked."""
        reads = [call for call in self.calls if "tsv" in call]
        assert reads, "the engine was never asked to read anything"
        return reads[-1]


def solid(width: int, height: int, colour: str = "black") -> Image.Image:
    return Image.new("RGB", (width, height), colour)


def written(text: str, *, size: tuple[int, int] = (460, 90)) -> Image.Image:
    """An image with ``text`` in it, for the reads that use the real engine."""
    image = solid(*size)
    font = ImageFont.load_default(size=44)
    ImageDraw.Draw(image).text((20, 20), text, font=font, fill="white")
    return image


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def engine_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin the configured engine to a file that exists but is never run.

    Always pinned, never discovered: a test that fell back to discovery would
    pass or fail depending on whether the machine running it has Tesseract.
    """
    executable = tmp_path / EXECUTABLE_NAME
    executable.write_bytes(b"")
    monkeypatch.setattr(settings, "OCR_TESSERACT_CMD", executable)
    return executable


@pytest.fixture
def fake_engine(
    engine_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FakeTesseract]:
    """A fake engine installed over the one place the real one is started."""
    fake = FakeTesseract()
    monkeypatch.setattr(ocr, "_run", fake)
    yield fake


@pytest.fixture
def missing_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the configuration at an engine that is not there."""
    absent = tmp_path / "nowhere" / EXECUTABLE_NAME
    monkeypatch.setattr(settings, "OCR_TESSERACT_CMD", absent)
    return absent


def write_game_config(directory: Path, document: dict[str, Any]) -> Path:
    """Write one game config into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{document['name']}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.fixture
def active_game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A game config declaring two regions, one of which overrides its options."""
    directory = tmp_path / "games"
    write_game_config(
        directory,
        {
            "name": "FortuneOx",
            "process": "FortuneOx.exe",
            "roi": {"cash_meter": CASH_METER, "bet_meter": BET_METER},
            "ocr": {"bet_meter": {"psm": 11, "char_whitelist": "0123456789."}},
        },
    )
    selection = tmp_path / "active_game.json"
    save_active_game(selection, "FortuneOx")
    monkeypatch.setattr(
        type(settings), "ideck_game_config_dir", property(lambda _self: directory)
    )
    monkeypatch.setattr(
        type(settings), "ideck_active_game_path", property(lambda _self: selection)
    )
    return directory


@pytest.fixture
def capture_frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """A screenshot sitting in a capture run, as if a run had taken it.

    Returns the run id and the filename, which are what a read request names.
    """
    root = tmp_path / "obs-captured-files"
    monkeypatch.setattr(
        type(settings), "obs_screenshot_dir", property(lambda _self: root)
    )
    run_id = "2026-08-19_04-01-02"
    directory = root / settings.EVENT_CAPTURE_DIR_NAME / run_id
    directory.mkdir(parents=True)
    file_name = "001_spin-started_04-01-07.png"
    solid(1280, 720).save(directory / file_name)
    return run_id, file_name


@pytest.fixture
def live_frame(monkeypatch: pytest.MonkeyPatch) -> Image.Image:
    """Make OBS answer a screenshot request with a frame, without a socket."""
    frame = solid(1920, 1080)

    async def fake_connect() -> Any:
        return None

    async def fake_screenshot(payload: Any) -> ScreenshotResult:
        import base64
        import io

        buffer = io.BytesIO()
        frame.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return ScreenshotResult(
            source_name="Game Capture",
            image_format="png",
            image_data=f"data:image/png;base64,{encoded}",
        )

    monkeypatch.setattr(obs_service, "connect", fake_connect)
    monkeypatch.setattr(obs_service, "take_screenshot", fake_screenshot)
    return frame


# --- Options and overrides --------------------------------------------------


def test_the_defaults_are_the_ones_a_single_line_meter_needs() -> None:
    options = ocr.OcrOptions()
    assert options.psm == 7
    assert options.upscale > 1
    assert options.language == "eng"


def test_options_become_the_flags_the_engine_takes() -> None:
    args = ocr.OcrOptions(
        language="eng", psm=11, oem=1, char_whitelist="0123456789", dpi=300
    ).command_args()
    assert args[:6] == ["-l", "eng", "--oem", "1", "--psm", "11"]
    assert "tessedit_char_whitelist=0123456789" in args
    assert "user_defined_dpi=300" in args


def test_an_empty_whitelist_is_left_off_the_command_line() -> None:
    # Passing an empty whitelist would restrict the engine to nothing at all.
    assert not any(
        "char_whitelist" in argument for argument in ocr.OcrOptions().command_args()
    )


def test_tessdata_is_only_passed_when_it_is_configured() -> None:
    assert "--tessdata-dir" not in ocr.OcrOptions().command_args()
    args = ocr.OcrOptions(tessdata_dir=Path("C:/tessdata")).command_args()
    assert args[args.index("--tessdata-dir") + 1].endswith("tessdata")


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("psm", 14, "psm must be between 0 and 13"),
        ("psm", -1, "psm must be between 0 and 13"),
        ("oem", 4, "oem must be between 0 and 3"),
        ("upscale", 0, "upscale must be greater than 0"),
        ("upscale", 25, "at most 10"),
        ("threshold", 300, "between 0 and 255"),
        ("dpi", 10, "dpi must be between 70 and 2400"),
        ("timeout_seconds", 0, "timeout_seconds must be greater than 0"),
        ("language", "  ", "at least one traineddata"),
    ],
)
def test_an_unusable_option_is_rejected_where_it_is_set(
    field: str, value: object, expected: str
) -> None:
    with pytest.raises(ocr.OcrOptionsError, match=expected):
        ocr.OcrOptions(**{field: value})


def test_overrides_change_only_what_they_name() -> None:
    merged = ocr.OcrOptions().merged({"psm": 11})
    assert merged.psm == 11
    assert merged.upscale == ocr.OcrOptions().upscale
    assert merged.language == "eng"


def test_no_overrides_leaves_the_options_alone() -> None:
    options = ocr.OcrOptions()
    assert options.merged(None) is options
    assert options.merged({}) is options


def test_an_override_may_turn_a_setting_off() -> None:
    # null is "let the engine decide", which is not the same as unmentioned.
    assert ocr.OcrOptions(threshold=128).merged({"threshold": None}).threshold is None


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"psm": True}, "'psm' must be int"),
        ({"psm": "11"}, "'psm' must be int"),
        ({"invert": 1}, "'invert' must be bool"),
        ({"language": None}, "'language' must not be null"),
        ({"timeout_seconds": 1}, "is not an OCR option"),
        ({"psn": 11}, "is not an OCR option"),
        ("psm=11", "must be a JSON object"),
    ],
)
def test_a_bad_override_names_the_region_it_came_from(
    overrides: object, expected: str
) -> None:
    with pytest.raises(ocr.OcrOptionsError, match=expected):
        ocr.parse_overrides(overrides, where="ocr.cash_meter")
    with pytest.raises(ocr.OcrOptionsError, match=r"ocr\.cash_meter"):
        ocr.parse_overrides(overrides, where="ocr.cash_meter")


def test_an_out_of_range_override_is_rejected_when_it_is_merged() -> None:
    with pytest.raises(ocr.OcrOptionsError, match=r"ocr\.cash_meter.*between 0 and 13"):
        ocr.OcrOptions().merged({"psm": 99}, where="ocr.cash_meter")


# --- Preprocessing ----------------------------------------------------------


def test_the_crop_is_enlarged_before_it_is_read() -> None:
    prepared = ocr.preprocess(solid(100, 20), ocr.OcrOptions(upscale=3.0))
    assert prepared.size == (300, 60)


def test_upscaling_by_one_leaves_the_size_alone() -> None:
    prepared = ocr.preprocess(solid(100, 20), ocr.OcrOptions(upscale=1.0))
    assert prepared.size == (100, 20)


def test_colour_is_dropped_when_greyscale_is_asked_for() -> None:
    assert ocr.preprocess(solid(10, 10), ocr.OcrOptions()).mode == "L"


def test_thresholding_forces_a_single_channel_even_without_greyscale() -> None:
    prepared = ocr.preprocess(
        solid(10, 10), ocr.OcrOptions(grayscale=False, threshold=128, upscale=1.0)
    )
    assert prepared.mode == "L"


def test_thresholding_leaves_only_black_and_white() -> None:
    frame = Image.new("L", (2, 1))
    frame.putpixel((0, 0), 100)
    frame.putpixel((1, 0), 200)
    prepared = ocr.preprocess(
        frame, ocr.OcrOptions(threshold=128, upscale=1.0, autocontrast=False)
    )
    assert (prepared.getpixel((0, 0)), prepared.getpixel((1, 0))) == (0, 255)


def test_inverting_flips_light_on_dark() -> None:
    frame = Image.new("L", (1, 1), 20)
    prepared = ocr.preprocess(
        frame, ocr.OcrOptions(invert=True, upscale=1.0, autocontrast=False)
    )
    assert prepared.getpixel((0, 0)) == 235


def test_preprocessing_leaves_the_source_alone() -> None:
    frame = solid(100, 20)
    ocr.preprocess(frame, ocr.OcrOptions(upscale=4.0, invert=True))
    assert frame.size == (100, 20)
    assert frame.mode == "RGB"


# --- Reading ----------------------------------------------------------------


def test_a_reading_carries_the_words_and_the_text_rebuilt_from_them(
    engine_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTesseract()
    monkeypatch.setattr(ocr, "_run", fake)

    result = ocr.read_image(solid(200, 40), executable=engine_path)

    assert result.text == "$842.94 $1.20"
    assert [word.text for word in result.words] == ["$842.94", "$1.20"]
    assert result.confidence == pytest.approx((96.5 + 91.0) / 2)


def test_the_image_is_handed_over_on_stdin_as_png(
    engine_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTesseract()
    monkeypatch.setattr(ocr, "_run", fake)

    ocr.read_image(solid(200, 40), executable=engine_path)

    args = fake.read_args()
    assert args[1:3] == ["-", "stdout"]
    assert args[-1] == "tsv"
    payload = fake.payloads[-1]
    assert payload is not None and payload.startswith(b"\x89PNG")


def test_word_boxes_are_in_the_coordinates_of_the_image_that_was_passed_in(
    engine_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The engine sees an enlarged copy; a caller should not have to know that."""
    monkeypatch.setattr(ocr, "_run", FakeTesseract())

    result = ocr.read_image(
        solid(200, 40), executable=engine_path, options=ocr.OcrOptions(upscale=3.0)
    )

    # left=30 in the TSV, which the engine measured on the 3x copy.
    assert result.words[0].left == 10
    assert result.words[0].box == (10, 4, 30, 14)


def test_lines_become_newlines_in_the_text(
    engine_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTesseract()
    fake.stdout = tsv(("CASH", 90.0, 30), line=1) + tsv(("$8.00", 90.0, 30), line=2)
    monkeypatch.setattr(ocr, "_run", fake)

    assert ocr.read_image(solid(200, 80), executable=engine_path).text == "CASH\n$8.00"


def test_rows_that_are_not_words_are_skipped(
    engine_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTesseract()
    fake.stdout = (
        tsv(("$8.00", 90.0, 30))
        # A word row the engine gave up on, a blank one, and a truncated line.
        + b"5\t1\t1\t1\t1\t9\t30\t12\t60\t30\t-1\t???\n"
        + b"5\t1\t1\t1\t1\t9\t30\t12\t60\t30\t95\t \n"
        + b"5\t1\t1\t1\t1\t9\tnot-a-number\n"
    )
    monkeypatch.setattr(ocr, "_run", fake)

    result = ocr.read_image(solid(200, 40), executable=engine_path)

    assert [word.text for word in result.words] == ["$8.00"]


def test_a_reading_with_nothing_in_it_has_no_confidence(
    engine_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTesseract()
    fake.stdout = tsv()
    monkeypatch.setattr(ocr, "_run", fake)

    result = ocr.read_image(solid(200, 40), executable=engine_path)

    assert result.text == ""
    assert result.words == ()
    assert result.confidence is None
    assert result.number is None


def test_a_file_is_read_and_closed_again(
    engine_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ocr, "_run", FakeTesseract())
    frame = tmp_path / "frame.png"
    solid(120, 40).save(frame)

    assert ocr.read_file(frame, executable=engine_path).text == "$842.94 $1.20"
    # Closed, so nothing on Windows is holding the file open.
    frame.unlink()


def test_something_that_is_not_an_image_says_so(
    engine_path: Path, tmp_path: Path
) -> None:
    not_an_image = tmp_path / "run.json"
    not_an_image.write_text("{}", encoding="utf-8")

    with pytest.raises(ocr.OcrError, match="could not be read as an image"):
        ocr.read_file(not_an_image, executable=engine_path)


def test_the_engine_is_asked_what_it_is(
    engine_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ocr, "_run", FakeTesseract())

    assert ocr.engine_version(engine_path) == "tesseract v5.5.3.20260724"
    assert ocr.engine_languages(engine_path) == ("eng", "osd")


# --- Numbers ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$842.94", ["842.94"]),
        ("CASH $842.94 BET $1.20", ["842.94", "1.20"]),
        ("1,234.56", ["1234.56"]),
        # Whichever separator comes last is the decimal point.
        ("1.234,56", ["1234.56"]),
        # A lone separator with three digits after it is grouping.
        ("$1,234", ["1234"]),
        ("-45.5", ["-45.5"]),
        ("0", ["0"]),
        # OCR noise around a number does not stop it being read.
        ("~~ $8.00 J ae)", ["8.00"]),
        ("no digits here", []),
        ("...", []),
        ("", []),
    ],
)
def test_the_numbers_in_a_reading_are_found(text: str, expected: list[str]) -> None:
    assert [str(number) for number in ocr.parse_numbers(text)] == expected


def test_the_first_number_is_the_one_a_meter_wants() -> None:
    assert str(ocr.parse_number("CASH $842.94 BET $1.20")) == "842.94"
    assert ocr.parse_number("nothing") is None


def test_two_values_side_by_side_are_not_read_as_one() -> None:
    # A meter region often holds several values, and joining them across the gap
    # would invent a number that is on no screen anywhere.
    assert [str(number) for number in ocr.parse_numbers("$1.20 176")] == ["1.20", "176"]


# --- Failures ---------------------------------------------------------------


def test_no_engine_where_one_was_configured_says_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(ocr.OcrUnavailableError, match="OCR_TESSERACT_CMD"):
        ocr.read_image(solid(20, 20), executable=tmp_path / "nowhere" / "tesseract.exe")


def test_an_engine_that_exits_non_zero_reports_its_last_words() -> None:
    """Checked against a real process: this mapping is the point of ``_run``."""
    with pytest.raises(ocr.OcrError, match="exited with 3: boom"):
        ocr._run(
            sys.executable,
            ["-c", "import sys; print('boom', file=sys.stderr); sys.exit(3)"],
            timeout=30,
        )


def test_an_engine_that_hangs_is_killed() -> None:
    with pytest.raises(ocr.OcrError, match="did not finish within"):
        ocr._run(sys.executable, ["-c", "import time; time.sleep(30)"], timeout=0.25)


def test_a_failed_read_is_an_error_and_not_an_empty_reading(
    engine_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTesseract()
    fake.error = ocr.OcrError("Tesseract exited with 1: Error in pixReadStream")
    monkeypatch.setattr(ocr, "_run", fake)

    with pytest.raises(ocr.OcrError, match="pixReadStream"):
        ocr.read_image(solid(20, 20), executable=engine_path)


# --- Finding the engine -----------------------------------------------------


def test_a_configured_executable_is_used_as_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / EXECUTABLE_NAME
    executable.write_bytes(b"")
    monkeypatch.setattr(settings, "OCR_TESSERACT_CMD", executable)

    assert settings.ocr_tesseract_cmd == executable


def test_a_configured_directory_is_the_one_the_installer_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pasting the install folder rather than the exe is the common mistake."""
    monkeypatch.setattr(settings, "OCR_TESSERACT_CMD", tmp_path)

    assert settings.ocr_tesseract_cmd == tmp_path / EXECUTABLE_NAME


def test_with_nothing_configured_the_installers_own_locations_are_searched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows installer does not put Tesseract on PATH, so this is the
    normal case rather than a fallback."""
    from app.config import ocr as ocr_config

    installed = tmp_path / "Tesseract-OCR" / EXECUTABLE_NAME
    installed.parent.mkdir()
    installed.write_bytes(b"")
    monkeypatch.setattr(ocr_config.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        ocr_config, "_WINDOWS_INSTALL_DIRS", (("LOCALAPPDATA", ("Tesseract-OCR",)),)
    )
    monkeypatch.setattr(ocr_config, "_POSIX_INSTALL_DIRS", (str(installed.parent),))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert ocr_config.discover_executable() == installed


def test_path_wins_over_an_installer_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import ocr as ocr_config

    on_path = tmp_path / "shim" / EXECUTABLE_NAME
    on_path.parent.mkdir()
    on_path.write_bytes(b"")
    monkeypatch.setattr(ocr_config.shutil, "which", lambda _name: str(on_path))

    assert next(iter(ocr_config.candidate_executables())) == on_path


def test_no_engine_anywhere_is_reported_rather_than_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import ocr as ocr_config

    monkeypatch.setattr(ocr_config.shutil, "which", lambda _name: None)
    monkeypatch.setattr(ocr_config, "_WINDOWS_INSTALL_DIRS", ())
    monkeypatch.setattr(ocr_config, "_POSIX_INSTALL_DIRS", ())

    assert ocr_config.discover_executable() is None


# --- Per-game OCR configuration --------------------------------------------


def test_a_game_may_override_the_options_for_one_region(tmp_path: Path) -> None:
    path = write_game_config(
        tmp_path,
        {
            "name": "FortuneOx",
            "roi": {"cash_meter": CASH_METER},
            "ocr": {"cash_meter": {"psm": 11, "invert": True}},
        },
    )

    config = load_game_config(path)

    assert config.ocr["cash_meter"] == {"psm": 11, "invert": True}


def test_a_game_without_an_ocr_block_reads_with_the_defaults(tmp_path: Path) -> None:
    path = write_game_config(
        tmp_path, {"name": "FortuneOx", "roi": {"cash_meter": CASH_METER}}
    )

    assert load_game_config(path).ocr == {}


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ({"cash_meter": {"psm": 99}}, "between 0 and 13"),
        ({"cash_meter": {"psn": 7}}, "is not an OCR option"),
        ({"cash_meter": "psm=7"}, "must be a JSON object"),
        ("nothing", "must be a JSON object"),
    ],
)
def test_a_bad_ocr_block_is_rejected_when_the_config_is_read(
    tmp_path: Path, block: object, expected: str
) -> None:
    """Reported at load time, not the first time someone reads that meter."""
    path = write_game_config(
        tmp_path, {"name": "FortuneOx", "roi": {"cash_meter": CASH_METER}, "ocr": block}
    )

    with pytest.raises(GameConfigError, match=expected):
        load_game_config(path)


def test_the_error_names_the_file_and_the_region(tmp_path: Path) -> None:
    path = write_game_config(
        tmp_path,
        {
            "name": "FortuneOx",
            "roi": {"cash_meter": CASH_METER},
            "ocr": {"cash_meter": {"psm": 99}},
        },
    )

    with pytest.raises(GameConfigError, match=r"'ocr\.cash_meter' in .*FortuneOx"):
        load_game_config(path)


# --- Status -----------------------------------------------------------------


async def test_status_reports_a_ready_engine(
    client: AsyncClient, fake_engine: FakeTesseract, engine_path: Path
) -> None:
    data = assert_success((await client.get(f"{API}/status")).json())

    assert data["state"] == "ready"
    assert data["executable"] == str(engine_path)
    assert data["version"] == "tesseract v5.5.3.20260724"
    assert data["languages"] == ["eng", "osd"]
    assert data["detail"] is None
    assert data["options"]["psm"] == settings.OCR_PSM


async def test_status_says_where_it_looked_when_nothing_is_installed(
    client: AsyncClient, missing_engine: Path
) -> None:
    response = await client.get(f"{API}/status")

    # 200: a machine with no engine is a state to report, not a failed request.
    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["state"] == "not_installed"
    assert "OCR_TESSERACT_CMD" in data["detail"]
    assert data["version"] is None


async def test_status_reports_the_feature_being_switched_off(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "OCR_ENABLED", False)

    data = assert_success((await client.get(f"{API}/status")).json())

    assert data["state"] == "disabled"
    assert "OCR_ENABLED" in data["detail"]


async def test_status_reports_an_engine_that_will_not_run(
    client: AsyncClient, engine_path: Path, fake_engine: FakeTesseract
) -> None:
    """An executable that is there and does not work is its own state."""
    fake_engine.version_error = ocr.OcrError("Tesseract could not be started")

    data = assert_success((await client.get(f"{API}/status")).json())

    assert data["state"] == "error"
    assert data["executable"] == str(engine_path)
    assert "could not be started" in data["detail"]


async def test_the_engine_is_identified_once_and_remembered(
    client: AsyncClient, fake_engine: FakeTesseract
) -> None:
    await client.get(f"{API}/status")
    calls_after_first = len(fake_engine.calls)
    await client.get(f"{API}/status")

    # The status card polls; discovery and two subprocesses per poll would be a
    # poor trade for a version string that cannot change while we run.
    assert len(fake_engine.calls) == calls_after_first


# --- Regions ----------------------------------------------------------------


async def test_the_regions_of_the_active_game_are_listed_with_their_options(
    client: AsyncClient, active_game: Path
) -> None:
    data = assert_success((await client.get(f"{API}/regions")).json())

    assert data["game"] == "FortuneOx"
    assert [region["region"] for region in data["regions"]] == [
        "bet_meter",
        "cash_meter",
    ]
    bet, cash = data["regions"]
    assert bet["roi"] == BET_METER
    # The effective options are what will be used: defaults, then the config's.
    assert bet["options"]["psm"] == 11
    assert bet["options"]["char_whitelist"] == "0123456789."
    assert bet["overrides"] == {"psm": 11, "char_whitelist": "0123456789."}
    assert cash["options"]["psm"] == settings.OCR_PSM
    assert cash["overrides"] == {}


async def test_regions_are_listed_without_an_engine_installed(
    client: AsyncClient, active_game: Path, missing_engine: Path
) -> None:
    """Listing what could be read does not need something to read it with."""
    data = assert_success((await client.get(f"{API}/regions")).json())

    assert len(data["regions"]) == 2


# --- Reading through the API ------------------------------------------------


async def test_a_region_is_read_off_a_screenshot_from_a_run(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakeTesseract,
    capture_frame: tuple[str, str],
) -> None:
    run_id, file_name = capture_frame

    data = assert_success(
        (
            await client.post(
                f"{API}/read",
                json={
                    "run_id": run_id,
                    "file_name": file_name,
                    "regions": ["cash_meter"],
                },
            )
        ).json()
    )

    assert data["source"] == "run"
    assert (data["frame_width"], data["frame_height"]) == (1280, 720)
    reading = data["readings"][0]
    assert reading["region"] == "cash_meter"
    assert reading["text"] == "$842.94 $1.20"
    assert reading["value"] == pytest.approx(842.94)
    assert reading["values"] == pytest.approx([842.94, 1.20])
    assert reading["confidence"] == pytest.approx(93.75)
    assert reading["error"] is None
    # The region resolved against this frame, in pixels, as image_roi crops it.
    assert reading["crop"] == [293, 608, 976, 637]


async def test_a_region_is_read_against_the_game_not_the_canvas(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakeTesseract,
    capture_frame: tuple[str, str],
) -> None:
    """A letterboxed capture puts the meter somewhere the canvas fractions miss.

    OBS writes every frame at its canvas size and fits the game window inside
    it, so the same four fractions have to be resolved against the part of the
    frame the game filled -- otherwise resizing the simulator un-aims every
    region at once.
    """
    run_id, file_name = capture_frame
    directory = settings.obs_screenshot_dir / settings.EVENT_CAPTURE_DIR_NAME / run_id
    # The real shape: a 632x1080 simulator lands as a 421-wide strip of a
    # 1280x720 canvas, black either side of it.
    frame = solid(1280, 720)
    frame.paste(Image.new("RGB", (421, 720), (200, 180, 40)), (429, 0))
    frame.save(directory / file_name)

    data = assert_success(
        (
            await client.post(
                f"{API}/read",
                json={
                    "run_id": run_id,
                    "file_name": file_name,
                    "regions": ["cash_meter", "bet_meter"],
                },
            )
        ).json()
    )

    assert data["content_box"] == [429, 0, 850, 720]
    assert data["letterboxed"] is True
    # 0.229264..0.762349 of the 421-wide content box, offset back to the frame.
    assert data["readings"][0]["crop"] == [526, 608, 750, 637]
    # Reported once for the frame, not once per region: every region of one shot
    # is measured against the same rectangle.
    assert data["readings"][1]["crop"] != data["readings"][0]["crop"]


async def test_a_frame_with_no_letterbox_is_read_against_the_whole_frame(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakeTesseract,
    capture_frame: tuple[str, str],
) -> None:
    run_id, file_name = capture_frame

    data = assert_success(
        (
            await client.post(
                f"{API}/read",
                json={
                    "run_id": run_id,
                    "file_name": file_name,
                    "regions": ["cash_meter"],
                },
            )
        ).json()
    )

    assert data["content_box"] == [0, 0, 1280, 720]
    assert data["letterboxed"] is False
    assert data["readings"][0]["crop"] == [293, 608, 976, 637]


async def test_a_live_read_takes_a_frame_from_obs(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakeTesseract,
    live_frame: Image.Image,
) -> None:
    data = assert_success(
        (await client.post(f"{API}/read", json={"regions": ["cash_meter"]})).json()
    )

    assert data["source"] == "live"
    assert data["run_id"] is None
    assert (data["frame_width"], data["frame_height"]) == live_frame.size


async def test_omitting_the_regions_reads_every_one_the_game_declares(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakeTesseract,
    live_frame: Image.Image,
) -> None:
    data = assert_success((await client.post(f"{API}/read", json={})).json())

    assert [reading["region"] for reading in data["readings"]] == [
        "bet_meter",
        "cash_meter",
    ]


async def test_each_region_is_read_with_its_own_options(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakeTesseract,
    live_frame: Image.Image,
) -> None:
    data = assert_success((await client.post(f"{API}/read", json={})).json())

    bet, cash = data["readings"]
    assert bet["options"]["psm"] == 11
    assert cash["options"]["psm"] == settings.OCR_PSM
    # ...and the engine was actually told so.
    psm_flags = [
        call[call.index("--psm") + 1] for call in fake_engine.calls if "--psm" in call
    ]
    assert psm_flags == ["11", str(settings.OCR_PSM)]


async def test_a_request_may_override_the_options_for_one_read(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakeTesseract,
    capture_frame: tuple[str, str],
) -> None:
    """Sweeping a setting against a frame on disk is how a region gets tuned."""
    run_id, file_name = capture_frame

    data = assert_success(
        (
            await client.post(
                f"{API}/read",
                json={
                    "run_id": run_id,
                    "file_name": file_name,
                    "regions": ["cash_meter"],
                    "options": {"psm": 6, "invert": True, "upscale": 2},
                },
            )
        ).json()
    )

    options = data["readings"][0]["options"]
    assert (options["psm"], options["invert"], options["upscale"]) == (6, True, 2)
    assert "--psm" in fake_engine.read_args()
    assert fake_engine.read_args()[fake_engine.read_args().index("--psm") + 1] == "6"


async def test_a_request_override_beats_the_game_config(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakeTesseract,
    live_frame: Image.Image,
) -> None:
    data = assert_success(
        (
            await client.post(
                f"{API}/read",
                json={"regions": ["bet_meter"], "options": {"psm": 3}},
            )
        ).json()
    )

    assert data["readings"][0]["options"]["psm"] == 3


async def test_the_crop_the_engine_saw_can_be_returned_with_the_reading(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakeTesseract,
    capture_frame: tuple[str, str],
) -> None:
    run_id, file_name = capture_frame

    data = assert_success(
        (
            await client.post(
                f"{API}/read",
                json={
                    "run_id": run_id,
                    "file_name": file_name,
                    "regions": ["cash_meter"],
                    "include_crop": True,
                },
            )
        ).json()
    )

    assert data["readings"][0]["crop_image"].startswith("data:image/png;base64,")


async def test_the_crop_is_left_out_unless_it_was_asked_for(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakeTesseract,
    live_frame: Image.Image,
) -> None:
    data = assert_success(
        (await client.post(f"{API}/read", json={"regions": ["cash_meter"]})).json()
    )

    assert data["readings"][0]["crop_image"] is None


async def test_one_unreadable_region_does_not_lose_the_others(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakeTesseract,
    live_frame: Image.Image,
) -> None:
    fake_engine.error = ocr.OcrError("Tesseract exited with 1: Image too small")
    fake_engine.fail_after = 1

    response = await client.post(f"{API}/read", json={})

    assert response.status_code == 200
    data = assert_success(response.json())
    first, second = data["readings"]
    assert first["error"] is None
    assert second["error"] is not None and "too small" in second["error"]
    # The failed one still says which region it was and what it was asked.
    assert second["region"] == "cash_meter"
    assert second["options"]["psm"] == settings.OCR_PSM


async def test_a_region_the_game_does_not_declare_is_a_404(
    client: AsyncClient, active_game: Path, fake_engine: FakeTesseract
) -> None:
    response = await client.post(f"{API}/read", json={"regions": ["win_meter"]})

    assert response.status_code == 404
    payload = response.json()
    assert_failure(payload, code="OCR_REGION_NOT_FOUND")
    # The message lists what is configured, so the typo is obvious.
    assert "cash_meter" in payload["message"]


async def test_a_game_with_no_regions_says_so(
    client: AsyncClient,
    tmp_path: Path,
    fake_engine: FakeTesseract,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "games"
    write_game_config(directory, {"name": "Bare", "process": "Bare.exe"})
    selection = tmp_path / "active_game.json"
    save_active_game(selection, "Bare")
    monkeypatch.setattr(
        type(settings), "ideck_game_config_dir", property(lambda _self: directory)
    )
    monkeypatch.setattr(
        type(settings), "ideck_active_game_path", property(lambda _self: selection)
    )

    response = await client.post(f"{API}/read", json={})

    assert response.status_code == 404
    assert_failure(response.json(), code="OCR_REGION_NOT_FOUND")


async def test_reading_without_an_engine_says_how_to_get_one(
    client: AsyncClient, active_game: Path, missing_engine: Path
) -> None:
    response = await client.post(f"{API}/read", json={"regions": ["cash_meter"]})

    # 409 rather than 503: installing Tesseract fixes it, retrying does not.
    assert response.status_code == 409
    payload = response.json()
    assert_failure(payload, code="OCR_ENGINE_UNAVAILABLE")
    assert "OCR_TESSERACT_CMD" in payload["message"]


async def test_reading_with_the_feature_switched_off_is_refused(
    client: AsyncClient, active_game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "OCR_ENABLED", False)

    response = await client.post(f"{API}/read", json={"regions": ["cash_meter"]})

    assert response.status_code == 409
    assert_failure(response.json(), code="OCR_ENGINE_UNAVAILABLE")


async def test_a_run_without_a_frame_named_is_a_bad_request(
    client: AsyncClient, active_game: Path, fake_engine: FakeTesseract
) -> None:
    response = await client.post(f"{API}/read", json={"run_id": "2026-08-19_04-01-02"})

    assert response.status_code == 400
    assert_failure(response.json(), code="BAD_REQUEST")


async def test_a_frame_without_a_run_named_is_a_bad_request(
    client: AsyncClient, active_game: Path, fake_engine: FakeTesseract
) -> None:
    response = await client.post(f"{API}/read", json={"file_name": "001.png"})

    assert response.status_code == 400
    assert_failure(response.json(), code="BAD_REQUEST")


async def test_a_screenshot_that_is_not_there_is_a_404(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakeTesseract,
    capture_frame: tuple[str, str],
) -> None:
    run_id, _ = capture_frame

    response = await client.post(
        f"{API}/read", json={"run_id": run_id, "file_name": "999_nothing.png"}
    )

    assert response.status_code == 404
    assert_failure(response.json(), code="EVENT_CAPTURE_RUN_NOT_FOUND")


async def test_a_screenshot_name_cannot_escape_its_run(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakeTesseract,
    capture_frame: tuple[str, str],
) -> None:
    """Both halves come off the wire, so both go through the path guards."""
    run_id, _ = capture_frame

    response = await client.post(
        f"{API}/read",
        json={"run_id": run_id, "file_name": "../../../../Windows/win.ini"},
    )

    assert response.status_code == 404
    assert_failure(response.json(), code="EVENT_CAPTURE_RUN_NOT_FOUND")


async def test_an_out_of_range_option_is_rejected_before_a_frame_is_taken(
    client: AsyncClient, active_game: Path, fake_engine: FakeTesseract
) -> None:
    response = await client.post(
        f"{API}/read", json={"regions": ["cash_meter"], "options": {"psm": 99}}
    )

    assert response.status_code == 422
    assert_failure(response.json(), code="VALIDATION_ERROR")
    assert not fake_engine.calls


async def test_an_option_that_does_not_exist_is_rejected(
    client: AsyncClient, active_game: Path, fake_engine: FakeTesseract
) -> None:
    response = await client.post(
        f"{API}/read", json={"regions": ["cash_meter"], "options": {"psn": 7}}
    )

    assert response.status_code == 422
    assert_failure(response.json(), code="VALIDATION_ERROR")


# --- Against the real engine ------------------------------------------------

ENGINE = discover_executable()
requires_engine = pytest.mark.skipif(
    ENGINE is None, reason="Tesseract OCR is not installed on this machine"
)


@requires_engine
def test_the_installed_engine_reads_text_it_is_given() -> None:
    """The whole wrapper, end to end, over the engine on this machine."""
    assert ENGINE is not None
    result = ocr.read_image(written("CASH 1,234.56"), executable=ENGINE)

    assert "1,234.56" in result.text
    assert str(result.number) == "1234.56"
    assert result.confidence is not None and result.confidence > 50
    assert all(word.confidence >= 0 for word in result.words)


@requires_engine
def test_the_installed_engine_reports_its_version_and_languages() -> None:
    assert ENGINE is not None
    assert "tesseract" in ocr.engine_version(ENGINE).lower()
    # An install with no traineddata could not read anything at all.
    assert ocr.engine_languages(ENGINE)


@requires_engine
def test_a_region_of_a_frame_is_read_through_the_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A meter drawn into the bottom of a frame, read back by its region name."""
    assert ENGINE is not None
    frame = solid(1280, 720)
    frame.paste(written("8,472.10", size=(480, 90)), (300, 600))

    from app.utils import image_roi

    region = image_roi.Roi.from_pixels(
        [300, 600, 780, 690], width=1280, height=720, where="roi.cash_meter"
    )
    result = ocr.read_image(image_roi.crop(frame, region), executable=ENGINE)

    assert str(result.number) == "8472.10"


@requires_engine
async def test_the_engine_is_reported_as_ready_when_it_is_installed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "OCR_TESSERACT_CMD", ENGINE)
    ocr_service.reset()

    data = assert_success((await client.get(f"{API}/status")).json())

    assert data["state"] == "ready"
    assert "eng" in data["languages"]
