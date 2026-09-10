"""Reading the messages back out of a finished cyclic-messages clip.

Neither Tesseract nor a real recording is involved: ``ocr.read_image`` is
scripted frame by frame, because what this module decides is how a sequence of
per-frame readings becomes a list of messages -- and the only way to test that
is to say exactly what each frame read.

The clip itself is real but tiny, written by the same codec probing
``utils/tile_video.py`` uses. It exists so the frame *count* is genuine; what
is on those frames is irrelevant, since the reader is stubbed.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from httpx import AsyncClient

from app.config.runtime import settings
from app.exceptions.base import OcrEngineUnavailableError
from app.services import cyclic_text as text_service
from app.services import ocr as ocr_service
from app.utils import ocr, tile_video
from tests.asserts import assert_failure, assert_success

API = "/api/cyclic-messages"

SIZE = (64, 48)
FPS = 10.0
FRAMES = 30  # three seconds


# --- fixtures -------------------------------------------------------------


@pytest.fixture
def capture_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "obs-captured-files"
    root.mkdir()
    monkeypatch.setattr(
        type(settings), "obs_screenshot_dir", property(lambda _self: root)
    )
    return root / settings.CYCLIC_MESSAGES_DIR_NAME


@pytest.fixture
def active_game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A game declaring the caption's region, as FortuneOx does."""
    directory = tmp_path / "games"
    directory.mkdir()
    (directory / "FortuneOx.json").write_text(
        json.dumps(
            {
                "name": "FortuneOx",
                "process": "FortuneOx.exe",
                "roi": {"cyclic_message": [0.0, 0.848, 0.18, 0.867]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        type(settings),
        "ideck_game_config_path_for",
        lambda _self, game: directory / f"{game}.json",
    )
    return directory


_CODEC: tile_video.Codec | None = None


def codec() -> tile_video.Codec:
    """Whichever encoder this build of OpenCV will write, probed once. Probing
    costs a throwaway file per candidate, and every clip here is the same size."""
    global _CODEC
    if _CODEC is None:
        _CODEC = tile_video.resolve_codec(FPS, SIZE)
    return _CODEC


def clip_named(stem: str) -> str:
    """A clip's file name, in the container that will actually write -- a
    hardcoded suffix is what makes a video test fail on another machine."""
    return f"{stem}{codec().suffix}"


def write_clip(path: Path) -> None:
    """A real clip of :data:`FRAMES` frames, so the sampling arithmetic is real."""
    writer = tile_video._open(path, codec(), FPS, SIZE)
    for index in range(FRAMES):
        writer.write(np.full((SIZE[1], SIZE[0], 3), index * 8, dtype=np.uint8))
    writer.release()


def make_run(
    capture_root: Path,
    run_id: str = "2026-09-09_22-12-53",
    *,
    videos: list[dict[str, Any]] | None = None,
) -> str:
    """Write a finished run to disk, clips included."""
    directory = capture_root / run_id
    directory.mkdir(parents=True, exist_ok=True)
    if videos is None:
        videos = [{"file_name": clip_named("cycle-002_win-video_22-13-10"), "cycle": 2}]
    for video in videos:
        if video.get("file_name"):
            write_clip(directory / str(video["file_name"]))
    (directory / "run.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "game": "FortuneOx",
                "status": "completed",
                "started_at": "2026-09-09T22:12:53",
                "stopped_at": "2026-09-09T22:15:00",
                "message_count": 5,
                "cycle_count": 1,
                "log_path": "C:/logs/FortuneOx_Client.log",
                "videos": videos,
                "events": [],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    return run_id


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """An engine that resolves without Tesseract being installed."""

    async def engine() -> Path:
        return Path("tesseract.exe")

    monkeypatch.setattr(ocr_service, "engine_for_reading", engine)


def script(monkeypatch: pytest.MonkeyPatch, readings: list[tuple[str, float]]) -> None:
    """Make the engine return these ``(text, confidence)`` pairs in order.

    Short scripts repeat their last entry rather than running out: a test about
    grouping should say only as much as it is testing.
    """
    calls = iter(readings)
    last = readings[-1]

    def read_image(_image: Any, **_kwargs: Any) -> ocr.OcrResult:
        nonlocal last
        with contextlib.suppress(StopIteration):
            last = next(calls)
        text, confidence = last
        words = tuple(
            ocr.OcrWord(
                text=word, confidence=confidence, left=0, top=0, width=1, height=1
            )
            for word in text.split()
        )
        return ocr.OcrResult(text=text, words=words, confidence=confidence, size=SIZE)

    monkeypatch.setattr(ocr, "read_image", read_image)


@pytest.fixture
def half_second(monkeypatch: pytest.MonkeyPatch) -> None:
    """0.5s at the clip's 10fps is every fifth frame: six frames of three seconds."""
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_TEXT_INTERVAL_SECONDS", 0.5)


# --- grouping -------------------------------------------------------------


async def test_consecutive_identical_readings_are_one_message(
    capture_root: Path,
    active_game: Path,
    fake_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The frames are taken faster than the strip changes on purpose, so how
    many frames read alike is how long a message was up -- not how many times
    it appeared."""
    run_id = make_run(capture_root)
    script(
        monkeypatch,
        [
            ("Line 1 Pays 25", 95.0),
            ("Line 1 Pays 25", 95.0),
            ("Line 1 Pays 25", 95.0),
            ("Line 2 Pays 15", 92.0),
            ("Line 2 Pays 15", 92.0),
            ("Line 3 Pays 40", 90.0),
        ],
    )

    reading = await text_service.read_run(run_id)

    assert reading.frames_sampled == 6
    assert [message.text for message in reading.messages] == [
        "Line 1 Pays 25",
        "Line 2 Pays 15",
        "Line 3 Pays 40",
    ]
    first, second, third = reading.messages
    assert (first.first_seen, first.last_seen, first.frames) == (0.0, 1.0, 3)
    assert (second.first_seen, second.last_seen, second.frames) == (1.5, 2.0, 2)
    assert third.frames == 1


async def test_a_blank_frame_ends_a_message_without_starting_one(
    capture_root: Path,
    active_game: Path,
    fake_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The strip really does go empty between presentations, and a gap is not
    a message -- but the same text after a gap is a second showing of it."""
    run_id = make_run(capture_root)
    script(
        monkeypatch,
        [
            ("Game Pays 168", 95.0),
            ("", 0.0),
            ("", 0.0),
            ("Game Pays 168", 95.0),
            ("Game Pays 168", 95.0),
            ("", 0.0),
        ],
    )

    reading = await text_service.read_run(run_id)

    assert [message.text for message in reading.messages] == [
        "Game Pays 168",
        "Game Pays 168",
    ]
    assert [message.frames for message in reading.messages] == [1, 2]
    assert reading.frames_sampled == 6


async def test_the_frame_readings_survive_beside_the_messages(
    capture_root: Path,
    active_game: Path,
    fake_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The messages are the answer; the frames are what the answer was built
    from, and a reader checking a collapse needs both."""
    run_id = make_run(capture_root)
    script(monkeypatch, [("Line 4 Pays 20", 88.0)])

    reading = await text_service.read_run(run_id)

    assert len(reading.frames) == 6
    assert [frame.frame_index for frame in reading.frames] == [0, 5, 10, 15, 20, 25]
    assert [round(frame.at_seconds, 2) for frame in reading.frames] == [
        0.0,
        0.5,
        1.0,
        1.5,
        2.0,
        2.5,
    ]


# --- the confidence floor -------------------------------------------------


async def test_an_unreadable_frame_is_kept_counted_and_marked(
    capture_root: Path,
    active_game: Path,
    fake_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caption the recording smeared comes back as plausible nonsense. It is
    reported rather than dropped -- dropping it leaves a reading that looks
    complete and is missing most of the pass."""
    run_id = make_run(capture_root)
    script(
        monkeypatch,
        [
            ("Line 1 Pays 25", 95.0),
            ("Liss 32 Pups 29", 48.0),
            ("Liss 32 Pups 29", 48.0),
            ("Line 3 Pays 40", 91.0),
            ("Line 3 Pays 40", 91.0),
            ("Line 3 Pays 40", 91.0),
        ],
    )

    reading = await text_service.read_run(run_id)

    assert reading.frames_unreadable == 2
    smeared = next(m for m in reading.messages if m.text == "Liss 32 Pups 29")
    assert smeared.reliable is False
    assert smeared.confidence == 48.0
    assert all(m.reliable for m in reading.messages if m.text.startswith("Line"))
    # Kept, not dropped: the text is the evidence about what failed.
    assert any(not frame.reliable and frame.text for frame in reading.frames)


async def test_a_blank_frame_is_not_counted_as_unreadable(
    capture_root: Path,
    active_game: Path,
    fake_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing on the strip is not the same as something the reader could not
    make out, and conflating them would make an idle stretch look like damage."""
    run_id = make_run(capture_root)
    script(monkeypatch, [("", 0.0)])

    reading = await text_service.read_run(run_id)

    assert reading.frames_sampled == 6
    assert reading.frames_unreadable == 0
    assert reading.messages == []


async def test_confidence_is_the_worst_word_not_the_average(
    capture_root: Path,
    active_game: Path,
    fake_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One mangled word is what makes a caption wrong; a mean hides it behind
    the words that read cleanly."""
    run_id = make_run(capture_root)

    def read_image(_image: Any, **_kwargs: Any) -> ocr.OcrResult:
        return ocr.OcrResult(
            text="Line 3 Pays 25",
            words=(
                ocr.OcrWord(
                    text="Line", confidence=97.0, left=0, top=0, width=1, height=1
                ),
                ocr.OcrWord(
                    text="3", confidence=96.0, left=0, top=0, width=1, height=1
                ),
                ocr.OcrWord(
                    text="Pays", confidence=41.0, left=0, top=0, width=1, height=1
                ),
                ocr.OcrWord(
                    text="25", confidence=95.0, left=0, top=0, width=1, height=1
                ),
            ),
            confidence=82.25,  # the mean, which would have cleared the floor
            size=SIZE,
        )

    monkeypatch.setattr(ocr, "read_image", read_image)

    reading = await text_service.read_run(run_id)

    assert reading.frames[0].confidence == 41.0
    assert reading.frames[0].reliable is False
    assert reading.frames_unreadable == 6


async def test_one_frame_the_engine_refuses_does_not_lose_the_clip(
    capture_root: Path,
    active_game: Path,
    fake_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = make_run(capture_root)
    calls = {"n": 0}

    def read_image(_image: Any, **_kwargs: Any) -> ocr.OcrResult:
        calls["n"] += 1
        if calls["n"] == 2:
            raise ocr.OcrError("the engine fell over on this one")
        return ocr.OcrResult(
            text="Line 8 Pays 30",
            words=(
                ocr.OcrWord(
                    text="Line", confidence=93.0, left=0, top=0, width=1, height=1
                ),
            ),
            confidence=93.0,
            size=SIZE,
        )

    monkeypatch.setattr(ocr, "read_image", read_image)

    reading = await text_service.read_run(run_id)

    assert reading.frames_sampled == 6
    assert any(frame.text == "" for frame in reading.frames)
    assert any(message.text == "Line 8 Pays 30" for message in reading.messages)


# --- choosing the clip ----------------------------------------------------


async def test_a_run_that_recorded_nothing_has_nothing_to_read(
    capture_root: Path, active_game: Path, fake_engine: None
) -> None:
    from app.exceptions.base import CyclicMessagesRunNotFoundError

    run_id = make_run(capture_root, videos=[])
    with pytest.raises(CyclicMessagesRunNotFoundError, match="no clip to read"):
        await text_service.read_run(run_id)


async def test_a_run_with_several_clips_refuses_to_guess(
    capture_root: Path,
    active_game: Path,
    fake_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading the wrong win gives a complete-looking answer about the wrong
    spin, which is worse than a 400."""
    from app.exceptions.base import BadRequestError

    run_id = make_run(
        capture_root,
        videos=[
            {"file_name": clip_named("cycle-001_win-video_a"), "cycle": 1},
            {"file_name": clip_named("cycle-004_win-video_b"), "cycle": 4},
        ],
    )
    script(monkeypatch, [("Line 1 Pays 25", 95.0)])

    with pytest.raises(BadRequestError, match="name one with 'cycle'"):
        await text_service.read_run(run_id)

    reading = await text_service.read_run(run_id, cycle=4)
    assert reading.cycle == 4
    assert reading.file_name == clip_named("cycle-004_win-video_b")


async def test_an_unknown_sequence_names_the_ones_that_exist(
    capture_root: Path, active_game: Path, fake_engine: None
) -> None:
    from app.exceptions.base import CyclicMessagesRunNotFoundError

    run_id = make_run(capture_root)
    with pytest.raises(CyclicMessagesRunNotFoundError, match="it has: 2"):
        await text_service.read_run(run_id, cycle=9)


async def test_a_clip_that_failed_to_file_is_not_offered(
    capture_root: Path, active_game: Path, fake_engine: None
) -> None:
    """A clip with an error and no file is a fact about that win, but there is
    nothing on disk to read."""
    from app.exceptions.base import CyclicMessagesRunNotFoundError

    run_id = make_run(
        capture_root,
        videos=[{"file_name": None, "cycle": 2, "error": "OBS refused to record"}],
    )
    with pytest.raises(CyclicMessagesRunNotFoundError, match="no clip to read"):
        await text_service.read_run(run_id)


# --- the region -----------------------------------------------------------


async def test_a_game_declaring_no_caption_region_says_what_it_does_declare(
    capture_root: Path,
    tmp_path: Path,
    fake_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.exceptions.base import GameConfigInvalidError

    directory = tmp_path / "games"
    directory.mkdir()
    (directory / "FortuneOx.json").write_text(
        json.dumps(
            {
                "name": "FortuneOx",
                "process": "FortuneOx.exe",
                "roi": {"cash_meter": [0.0, 0.84, 1.0, 0.88]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        type(settings),
        "ideck_game_config_path_for",
        lambda _self, game: directory / f"{game}.json",
    )
    run_id = make_run(capture_root)

    with pytest.raises(GameConfigInvalidError, match="cash_meter"):
        await text_service.read_run(run_id)


async def test_the_region_read_is_the_one_the_settings_name(
    capture_root: Path,
    active_game: Path,
    fake_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = make_run(capture_root)
    script(monkeypatch, [("Line 1 Pays 25", 95.0)])

    reading = await text_service.read_run(run_id)

    assert reading.region == settings.CYCLIC_MESSAGES_TEXT_REGION == "cyclic_message"


# --- the interval ---------------------------------------------------------


async def test_the_interval_can_be_overridden_per_request(
    capture_root: Path,
    active_game: Path,
    fake_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1.0s at the clip's 10fps is every tenth frame, so three of thirty."""
    run_id = make_run(capture_root)
    script(monkeypatch, [("Line 1 Pays 25", 95.0)])

    reading = await text_service.read_run(run_id, interval_seconds=1.0)

    assert reading.interval_seconds == 1.0
    assert reading.frames_sampled == 3


# --- the endpoint ---------------------------------------------------------


async def test_the_endpoint_returns_the_reading_in_the_envelope(
    client: AsyncClient,
    capture_root: Path,
    active_game: Path,
    fake_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = make_run(capture_root)
    script(monkeypatch, [("Line 1 Pays 25", 95.0), ("Line 2 Pays 15", 93.0)])

    data = assert_success((await client.get(f"{API}/runs/{run_id}/text")).json())

    assert data["run_id"] == run_id
    assert data["game"] == "FortuneOx"
    assert [message["text"] for message in data["messages"]] == [
        "Line 1 Pays 25",
        "Line 2 Pays 15",
    ]
    assert data["frames_sampled"] == 6


async def test_the_endpoint_refuses_an_interval_of_zero(
    client: AsyncClient, capture_root: Path, active_game: Path
) -> None:
    run_id = make_run(capture_root)
    response = await client.get(f"{API}/runs/{run_id}/text?interval_seconds=0")
    assert response.status_code == 422


async def test_no_engine_fails_the_request_rather_than_reading_blanks(
    client: AsyncClient,
    capture_root: Path,
    active_game: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reading with no OCR behind it is a list of empty frames, which looks
    like a strip that showed nothing."""

    async def unavailable() -> Path:
        raise OcrEngineUnavailableError("No Tesseract executable was found")

    monkeypatch.setattr(ocr_service, "engine_for_reading", unavailable)
    run_id = make_run(capture_root)

    response = await client.get(f"{API}/runs/{run_id}/text")
    assert response.status_code == 409
    assert_failure(response.json(), code="OCR_ENGINE_UNAVAILABLE")
