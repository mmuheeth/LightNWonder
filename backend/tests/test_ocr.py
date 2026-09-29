"""Reading text off a frame: the generic named-region reader and its API.

Most of this runs without PaddleOCR installed. ``FakePaddle`` replaces the built
engine (the same seam :mod:`test_paddle_ocr` and :mod:`test_meter` use), which is
enough to exercise everything above it -- option layering, the per-region config,
the endpoints and every failure path -- on a machine that has no engine at all.

The reads under *Against the real engine* run the installed PaddleOCR over a
drawn region with known text in it, skipped when it is not installed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from PIL import Image, ImageDraw, ImageFont

from app.config.game_config import GameConfigError, load_game_config, save_active_game
from app.config.runtime import settings
from app.schemas.obs import ScreenshotResult
from app.services import obs as obs_service
from app.utils import paddle_ocr
from tests.asserts import assert_failure, assert_success

API = "/api/ocr"

# The cash meter as FortuneOx's shipped config declares it.
CASH_METER = [0.229264, 0.844468, 0.762349, 0.884554]
BET_METER = [0.1, 0.9, 0.4, 0.95]


def page(*words: tuple[str, float]) -> list[dict[str, list[Any]]]:
    """One Paddle 3.x result page: parallel text and score lists, 0-1 scale."""
    return [
        {
            "rec_texts": [text for text, _ in words],
            "rec_scores": [score for _, score in words],
        }
    ]


DEFAULT_PAGE = page(("$842.94", 0.965), ("$1.20", 0.91))


class FakePaddle:
    """Stands in for a built PaddleOCR by replacing the engine cache.

    Answers every read with :attr:`pages`. Set :attr:`error` to make the next
    read fail, or :attr:`fail_after` to let a given number of reads succeed
    first -- which is how "one region of two could not be read" is set up.
    """

    def __init__(self) -> None:
        self.pages: Any = DEFAULT_PAGE
        self.error: Exception | None = None
        self.fail_after: int | None = None
        self.reads = 0
        self.arrays: list[Any] = []

    def predict(self, array: Any) -> Any:
        self.arrays.append(array)
        self.reads += 1
        if self.error is not None and (
            self.fail_after is None or self.reads > self.fail_after
        ):
            raise self.error
        return self.pages


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
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakePaddle]:
    # `ocr.ensure_available()` checks `paddle_ocr.available()` (a real import
    # check) before ever touching the engine, so a faked engine object alone
    # is not enough on a machine where paddleocr is not actually installed.
    monkeypatch.setattr(paddle_ocr, "available", lambda: True)
    fake = FakePaddle()
    monkeypatch.setattr(paddle_ocr, "_engine", fake, raising=False)
    yield fake


@pytest.fixture
def missing_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the engine report itself as not installed."""
    monkeypatch.setattr(paddle_ocr, "available", lambda: False)


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
            "ocr": {"bet_meter": {"min_confidence": 0.8, "upscale": 2.0}},
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


def test_the_defaults_are_light_preprocessing() -> None:
    options = paddle_ocr.PaddleOptions()
    assert options.upscale == 1.0
    assert options.language == "en"


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("upscale", 0, "upscale must be greater than 0"),
        ("upscale", 25, "at most 10"),
        ("min_confidence", -0.1, "between 0 and 1"),
        ("min_confidence", 1.1, "between 0 and 1"),
        ("language", "  ", "must name a PaddleOCR language"),
    ],
)
def test_an_unusable_option_is_rejected_where_it_is_set(
    field: str, value: object, expected: str
) -> None:
    with pytest.raises(paddle_ocr.OcrError, match=expected):
        paddle_ocr.PaddleOptions(**{field: value})


def test_overrides_change_only_what_they_name() -> None:
    merged = paddle_ocr.PaddleOptions().merged({"upscale": 2.0})
    assert merged.upscale == 2.0
    assert merged.min_confidence == paddle_ocr.PaddleOptions().min_confidence
    assert merged.language == "en"


def test_no_overrides_leaves_the_options_alone() -> None:
    options = paddle_ocr.PaddleOptions()
    assert options.merged(None) is options
    assert options.merged({}) is options


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"upscale": True}, "'upscale' must be"),
        ({"upscale": "2"}, "'upscale' must be"),
        ({"language": None}, "'language' must be"),
        ({"min_digits": 3}, "is not an OCR option"),
        ({"psn": 11}, "is not an OCR option"),
        ("upscale=2", "must be a JSON object"),
    ],
)
def test_a_bad_override_names_the_region_it_came_from(
    overrides: object, expected: str
) -> None:
    with pytest.raises(paddle_ocr.OcrOptionsError, match=expected):
        paddle_ocr.parse_overrides(overrides, where="ocr.cash_meter")
    with pytest.raises(paddle_ocr.OcrOptionsError, match=r"ocr\.cash_meter"):
        paddle_ocr.parse_overrides(overrides, where="ocr.cash_meter")


def test_an_out_of_range_override_is_rejected_when_it_is_merged() -> None:
    with pytest.raises(
        paddle_ocr.OcrOptionsError, match=r"ocr\.cash_meter.*at most 10"
    ):
        paddle_ocr.PaddleOptions().merged({"upscale": 99}, where="ocr.cash_meter")


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
    assert [str(number) for number in paddle_ocr.parse_numbers(text)] == expected


def test_the_first_number_is_the_one_a_meter_wants() -> None:
    assert str(paddle_ocr.parse_number("CASH $842.94 BET $1.20")) == "842.94"
    assert paddle_ocr.parse_number("nothing") is None


def test_two_values_side_by_side_are_not_read_as_one() -> None:
    # A meter region often holds several values, and joining them across the gap
    # would invent a number that is on no screen anywhere.
    assert [str(n) for n in paddle_ocr.parse_numbers("$1.20 176")] == ["1.20", "176"]


# --- Per-game OCR configuration --------------------------------------------


def test_a_game_may_override_the_options_for_one_region(tmp_path: Path) -> None:
    path = write_game_config(
        tmp_path,
        {
            "name": "FortuneOx",
            "roi": {"cash_meter": CASH_METER},
            "ocr": {"cash_meter": {"min_confidence": 0.8}},
        },
    )

    config = load_game_config(path)

    assert config.ocr["cash_meter"] == {"min_confidence": 0.8}


def test_a_game_without_an_ocr_block_reads_with_the_defaults(tmp_path: Path) -> None:
    path = write_game_config(
        tmp_path, {"name": "FortuneOx", "roi": {"cash_meter": CASH_METER}}
    )

    assert load_game_config(path).ocr == {}


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ({"cash_meter": {"upscale": 99}}, "at most 10"),
        ({"cash_meter": {"psn": 7}}, "is not an OCR option"),
        ({"cash_meter": "upscale=2"}, "must be a JSON object"),
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
            "ocr": {"cash_meter": {"upscale": 99}},
        },
    )

    with pytest.raises(GameConfigError, match=r"'ocr\.cash_meter' in .*FortuneOx"):
        load_game_config(path)


# --- Status -----------------------------------------------------------------


async def test_status_reports_a_ready_engine(
    client: AsyncClient, fake_engine: FakePaddle, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paddle_ocr, "prepare", lambda _options=None: "3.7.0")

    data = assert_success((await client.get(f"{API}/status")).json())

    assert data["state"] == "ready"
    assert data["version"] == "3.7.0"
    assert data["languages"] == [settings.OCR_REGION_PADDLE_LANGUAGE]
    assert data["detail"] is None


async def test_status_says_how_to_install_when_nothing_is_installed(
    client: AsyncClient, missing_engine: None
) -> None:
    response = await client.get(f"{API}/status")

    # 200: a machine with no engine is a state to report, not a failed request.
    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["state"] == "not_installed"
    assert "pip install paddlepaddle" in data["detail"]
    assert data["version"] is None


async def test_status_reports_the_feature_being_switched_off(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "OCR_ENABLED", False)

    data = assert_success((await client.get(f"{API}/status")).json())

    assert data["state"] == "disabled"
    assert "OCR_ENABLED" in data["detail"]


async def test_status_reports_an_engine_that_will_not_build(
    client: AsyncClient, fake_engine: FakePaddle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Importable but broken is its own state."""

    def broken(_options: Any = None) -> str:
        raise paddle_ocr.OcrError("PaddleOCR could not be started")

    monkeypatch.setattr(paddle_ocr, "prepare", broken)

    data = assert_success((await client.get(f"{API}/status")).json())

    assert data["state"] == "error"
    assert "could not be started" in data["detail"]


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
    assert bet["options"]["min_confidence"] == 0.8
    assert bet["options"]["upscale"] == 2.0
    assert bet["overrides"] == {"min_confidence": 0.8, "upscale": 2.0}
    assert (
        cash["options"]["min_confidence"] == settings.OCR_REGION_PADDLE_MIN_CONFIDENCE
    )
    assert cash["overrides"] == {}


async def test_regions_are_listed_without_an_engine_installed(
    client: AsyncClient, active_game: Path, missing_engine: None
) -> None:
    """Listing what could be read does not need something to read it with."""
    data = assert_success((await client.get(f"{API}/regions")).json())

    assert len(data["regions"]) == 2


# --- Reading through the API ------------------------------------------------


async def test_a_region_is_read_off_a_screenshot_from_a_run(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakePaddle,
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
    assert len(reading["words"]) == 2
    assert reading["error"] is None
    # The region resolved against this frame, in pixels, as image_roi crops it.
    assert reading["crop"] == [293, 608, 976, 637]


async def test_a_region_is_read_against_the_game_not_the_canvas(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakePaddle,
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
    fake_engine: FakePaddle,
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
    fake_engine: FakePaddle,
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
    fake_engine: FakePaddle,
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
    fake_engine: FakePaddle,
    live_frame: Image.Image,
) -> None:
    data = assert_success((await client.post(f"{API}/read", json={})).json())

    bet, cash = data["readings"]
    assert bet["options"]["upscale"] == 2.0
    assert cash["options"]["upscale"] == settings.OCR_REGION_PADDLE_UPSCALE


async def test_a_request_may_override_the_options_for_one_read(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakePaddle,
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
                    "options": {"min_confidence": 0.6, "upscale": 2},
                },
            )
        ).json()
    )

    options = data["readings"][0]["options"]
    assert (options["min_confidence"], options["upscale"]) == (0.6, 2)


async def test_a_request_override_beats_the_game_config(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakePaddle,
    live_frame: Image.Image,
) -> None:
    data = assert_success(
        (
            await client.post(
                f"{API}/read",
                json={"regions": ["bet_meter"], "options": {"upscale": 3}},
            )
        ).json()
    )

    assert data["readings"][0]["options"]["upscale"] == 3


async def test_the_crop_the_engine_saw_can_be_returned_with_the_reading(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakePaddle,
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
    fake_engine: FakePaddle,
    live_frame: Image.Image,
) -> None:
    data = assert_success(
        (await client.post(f"{API}/read", json={"regions": ["cash_meter"]})).json()
    )

    assert data["readings"][0]["crop_image"] is None


async def test_a_low_confidence_word_still_appears_but_does_not_count(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakePaddle,
    live_frame: Image.Image,
) -> None:
    """Rejected words are visible in `words`, but do not corrupt `text`/`value` --
    the same "show what was read, even refused" rule the scatter/prize reader
    follows."""
    fake_engine.pages = page(("$842.94", 0.965), ("noise", 0.05))

    data = assert_success(
        (await client.post(f"{API}/read", json={"regions": ["cash_meter"]})).json()
    )

    reading = data["readings"][0]
    assert reading["text"] == "$842.94"
    assert len(reading["words"]) == 2
    assert any(word["text"] == "noise" for word in reading["words"])


async def test_one_unreadable_region_does_not_lose_the_others(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakePaddle,
    live_frame: Image.Image,
) -> None:
    fake_engine.error = paddle_ocr.OcrError("PaddleOCR failed to read the crop")
    fake_engine.fail_after = 1

    response = await client.post(f"{API}/read", json={})

    assert response.status_code == 200
    data = assert_success(response.json())
    first, second = data["readings"]
    assert first["error"] is None
    assert second["error"] is not None and "failed to read" in second["error"]
    # The failed one still says which region it was and what it was asked.
    assert second["region"] == "cash_meter"


async def test_a_region_the_game_does_not_declare_is_a_404(
    client: AsyncClient, active_game: Path, fake_engine: FakePaddle
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
    fake_engine: FakePaddle,
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
    client: AsyncClient, active_game: Path, missing_engine: None
) -> None:
    response = await client.post(f"{API}/read", json={"regions": ["cash_meter"]})

    # 409 rather than 503: installing PaddleOCR fixes it, retrying does not.
    assert response.status_code == 409
    payload = response.json()
    assert_failure(payload, code="OCR_ENGINE_UNAVAILABLE")
    assert "pip install paddlepaddle" in payload["message"]


async def test_reading_with_the_feature_switched_off_is_refused(
    client: AsyncClient, active_game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "OCR_ENABLED", False)

    response = await client.post(f"{API}/read", json={"regions": ["cash_meter"]})

    assert response.status_code == 409
    assert_failure(response.json(), code="OCR_ENGINE_UNAVAILABLE")


async def test_a_run_without_a_frame_named_is_a_bad_request(
    client: AsyncClient, active_game: Path, fake_engine: FakePaddle
) -> None:
    response = await client.post(f"{API}/read", json={"run_id": "2026-08-19_04-01-02"})

    assert response.status_code == 400
    assert_failure(response.json(), code="BAD_REQUEST")


async def test_a_frame_without_a_run_named_is_a_bad_request(
    client: AsyncClient, active_game: Path, fake_engine: FakePaddle
) -> None:
    response = await client.post(f"{API}/read", json={"file_name": "001.png"})

    assert response.status_code == 400
    assert_failure(response.json(), code="BAD_REQUEST")


async def test_a_screenshot_that_is_not_there_is_a_404(
    client: AsyncClient,
    active_game: Path,
    fake_engine: FakePaddle,
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
    fake_engine: FakePaddle,
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
    client: AsyncClient, active_game: Path, fake_engine: FakePaddle
) -> None:
    response = await client.post(
        f"{API}/read", json={"regions": ["cash_meter"], "options": {"upscale": 99}}
    )

    assert response.status_code == 422
    assert_failure(response.json(), code="VALIDATION_ERROR")
    assert not fake_engine.arrays


async def test_an_option_that_does_not_exist_is_rejected(
    client: AsyncClient, active_game: Path, fake_engine: FakePaddle
) -> None:
    response = await client.post(
        f"{API}/read", json={"regions": ["cash_meter"], "options": {"psn": 7}}
    )

    assert response.status_code == 422
    assert_failure(response.json(), code="VALIDATION_ERROR")


# --- Against the real engine ------------------------------------------------

requires_paddle = pytest.mark.skipif(
    not paddle_ocr.available(),
    reason="PaddleOCR is not installed (needs Python 3.13 or lower)",
)


@requires_paddle
def test_the_installed_engine_reads_text_it_is_given() -> None:
    """The whole reader, end to end, over the engine on this machine."""
    result = paddle_ocr.read_image(written("CASH 1,234.56"))

    assert "1,234.56" in result.text
    assert str(result.number) == "1234.56"
    assert result.confidence is not None and result.confidence > 0.5
    assert all(word.confidence >= 0 for word in result.words)


@requires_paddle
def test_the_installed_engine_reports_its_version() -> None:
    assert paddle_ocr.engine_version()


@requires_paddle
async def test_the_engine_is_reported_as_ready_when_it_is_installed(
    client: AsyncClient,
) -> None:
    data = assert_success((await client.get(f"{API}/status")).json())

    assert data["state"] == "ready"
