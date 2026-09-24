"""Reading the messages back out of a finished cyclic-messages clip.

No real engine and no real recording is involved: whichever reader is under
test is scripted frame by frame, because what this module decides is how a
sequence of per-frame readings becomes a list of messages -- and the only way
to test that is to say exactly what each frame read. The grouping tests script
Tesseract and the engine tests script Paddle; the collapse is the same code
either way.

The clip itself is real but tiny, written by the same codec probing
``utils/tile_video.py`` uses. It exists so the frame *count* is genuine; what
is on those frames is irrelevant, since the reader is stubbed.
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from httpx import AsyncClient
from PIL import Image, ImageDraw, ImageFont

from app.config.runtime import settings
from app.exceptions.base import OcrEngineUnavailableError
from app.services import cyclic_messages as cyclic_service
from app.services import cyclic_text as text_service
from app.services import ocr as ocr_service
from app.utils import image_roi, ocr, paddle_ocr, tile_video
from app.utils.log_tail import LogFollower
from tests.asserts import assert_failure, assert_success

API = "/api/cyclic-messages"

# Big enough that the caption band is a real crop rather than a row of pixels.
# `roi.cyclic_message` below is 1.9% of the frame's height, so at the 64x48 this
# used to be the band was 11x1 -- and the reader now *looks* at a band before
# spending a recognition on it (`cyclic_text._plan`), which a single row of
# pixels cannot answer either way.
SIZE = (320, 240)
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
    """A real clip of :data:`FRAMES` frames, so the sampling arithmetic is real.

    Each frame carries a different high-contrast block inside the caption band,
    which used to be beside the point -- the reader is scripted, so what is on
    the frames was irrelevant. It no longer is: the reader decides which frames
    to spend a recognition on by looking at the band (see `cyclic_text._plan`),
    so a clip of flat grey frames is one where *no* band is drawing anything
    and nothing is read at all. The pattern only has to be present and to
    differ frame to frame; what it looks like still does not matter.
    """
    writer = tile_video._open(path, codec(), FPS, SIZE)
    width, height = SIZE
    for index in range(FRAMES):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        # Inside `roi.cyclic_message` (the top 18% of the width, 85% down), and
        # moving, so no two consecutive frames are the same picture.
        left = (index * 7) % 40
        frame[196:212, left : left + 12] = 255
        writer.write(frame)
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
def tesseract_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read with Tesseract, and let it resolve without Tesseract installed.

    Paddle is the default engine, so this is what the tests below about
    *grouping* select: they are engine-agnostic and were written against the
    Tesseract reader, and scripting one reader is enough to say how a sequence
    of readings becomes a list of messages.
    """
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_TEXT_ENGINE", "tesseract")

    async def engine() -> Path:
        return Path("tesseract.exe")

    monkeypatch.setattr(ocr_service, "engine_for_reading", engine)


@pytest.fixture
def paddle_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PaddleOCR that resolves without one being built.

    Stubbed rather than real, and not only for speed: under
    ``filterwarnings = error`` constructing a real PaddleOCR raises, because
    paddle warns about a missing ccache while importing.
    """
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_TEXT_ENGINE", "paddle")
    monkeypatch.setattr(paddle_ocr, "prepare_line", lambda _options: "test-rec")


def script_paddle(
    monkeypatch: pytest.MonkeyPatch, readings: list[tuple[str, float]]
) -> None:
    """Make Paddle return these ``(text, confidence)`` pairs in order, with the
    confidence on **Paddle's own 0-1 scale** -- the point being that what comes
    out the other end is on Tesseract's 0-100.

    A recognise-only read returns the whole crop as one "word", which is what
    this imitates.
    """
    calls = iter(readings)
    last = readings[-1]

    def read_line(_image: Any, **_kwargs: Any) -> paddle_ocr.PaddleResult:
        nonlocal last
        with contextlib.suppress(StopIteration):
            last = next(calls)
        text, confidence = last
        words = (
            (paddle_ocr.PaddleWord(text=text, confidence=confidence),) if text else ()
        )
        return paddle_ocr.PaddleResult(
            text=text,
            words=words,
            confidence=confidence or None,
            size=SIZE,
        )

    monkeypatch.setattr(paddle_ocr, "read_line", read_line)


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
    tesseract_engine: None,
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
    tesseract_engine: None,
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
    tesseract_engine: None,
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


async def test_one_caption_read_with_different_spacing_is_one_message(
    capture_root: Path,
    active_game: Path,
    tesseract_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The engine varies its spacing and punctuation between frames of one
    caption. Holding out for an exact match splits that caption into a row per
    frame, which is what makes a steady strip look like it is changing every
    second."""
    run_id = make_run(capture_root)
    script(
        monkeypatch,
        [
            ("Line 4 Pays 250", 90.0),
            ("Line 4Pays 250", 93.0),
            ("line 7 pays 250", 91.0),
            ("Line 7 Pays 250 +", 95.0),
            ("Line 7  PAYS  250", 88.0),
            ("Line 9 Pays 250", 92.0),
        ],
    )

    reading = await text_service.read_run(run_id)

    assert [message.frames for message in reading.messages] == [2, 3, 1]
    # Every frame still reported its own reading, verbatim.
    assert len(reading.frames) == 6


async def test_a_grouped_message_shows_the_best_scoring_reading(
    capture_root: Path,
    active_game: Path,
    tesseract_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once spacing is tolerated the frames no longer agree character for
    character, so one variant has to be shown -- and the engine's own best
    guess beats whichever happened to come first."""
    run_id = make_run(capture_root)
    script(
        monkeypatch,
        [("Line 6  Pays250", 71.0), ("Line 6 Pays 250", 96.0), ("", 0.0)],
    )

    reading = await text_service.read_run(run_id)

    message = reading.messages[0]
    assert message.text == "Line 6 Pays 250"
    assert message.confidence == 96.0
    assert message.first_seen == 0.0


async def test_a_differing_character_is_never_grouped_away(
    capture_root: Path,
    active_game: Path,
    tesseract_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The invariant that stops grouping from being made cleverer: two real
    captions differ by exactly one character, so any tolerance wide enough to
    absorb a misread would silently merge two different lines -- and a merge
    loses a message rather than duplicating one."""
    run_id = make_run(capture_root)
    script(
        monkeypatch,
        [("Line 1 Pays 250", 95.0), ("Line 4 Pays 250", 95.0), ("", 0.0)],
    )

    reading = await text_service.read_run(run_id)

    assert [message.text for message in reading.messages] == [
        "Line 1 Pays 250",
        "Line 4 Pays 250",
    ]


async def test_a_letter_where_the_strip_draws_a_digit_is_repaired(
    capture_root: Path,
    active_game: Path,
    tesseract_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The strip's alphabet is three words and the digits, so a letter after
    "Line" is a misread by definition. This font's 3 reads as 'a' and its 8 as
    'B' through every model tried, which is what makes the repair worth having
    rather than a workaround."""
    run_id = make_run(capture_root)
    script(
        monkeypatch,
        [("Line a Pays 25", 95.0), ("Line B Pays 25", 99.0), ("Lie 7 Pays 25", 96.0)],
    )

    reading = await text_service.read_run(run_id)

    assert [caption.text for caption in reading.captions] == [
        "Line 3 Pays 25",
        "Line 8 Pays 25",
        "Line 7 Pays 25",
    ]
    # The raw reading survives beside it: a repair can be confidently wrong
    # about *which* digit, and both are needed to notice.
    assert reading.frames[0].text == "Line a Pays 25"
    assert reading.frames[0].repaired == "Line 3 Pays 25"


async def test_no_vocabulary_means_no_repair(
    capture_root: Path,
    active_game: Path,
    tesseract_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A game whose strip nobody has described gets its readings untouched --
    guessing at an unknown grammar is how a repair becomes a corruption."""
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_TEXT_VOCABULARY", "")
    run_id = make_run(capture_root)
    script(monkeypatch, [("Line a Pays 25", 95.0)])

    reading = await text_service.read_run(run_id)

    assert reading.captions[0].text == "Line a Pays 25"


async def test_the_repeats_of_a_looping_strip_are_counted_not_listed(
    capture_root: Path,
    active_game: Path,
    tesseract_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A win presentation walks its lines and starts again, so the same
    caption comes up once per time round. That is true of the strip and a poor
    way to read one."""
    run_id = make_run(capture_root)
    script(
        monkeypatch,
        [
            ("Line 1 Pays 25", 90.0),
            ("Line 2 Pays 25", 91.0),
            ("", 0.0),
            ("Line 1 Pays 25", 97.0),
            ("Line 2 Pays 25", 88.0),
            ("Line 3 Pays 25", 92.0),
        ],
    )

    reading = await text_service.read_run(run_id)

    # Five showings of three captions.
    assert len(reading.messages) == 5
    assert [(c.text, c.showings) for c in reading.captions] == [
        ("Line 1 Pays 25", 2),
        ("Line 2 Pays 25", 2),
        ("Line 3 Pays 25", 1),
    ]
    # Ordered by when the strip first said each, not by how often.
    assert [c.first_seen for c in reading.captions] == [0.0, 0.5, 2.5]
    # And the best reading of the repeats wins, as within one showing.
    assert reading.captions[0].confidence == 97.0


# --- the confidence floor -------------------------------------------------


async def test_an_unreadable_frame_is_kept_counted_and_marked(
    capture_root: Path,
    active_game: Path,
    tesseract_engine: None,
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
    tesseract_engine: None,
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
    tesseract_engine: None,
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
    tesseract_engine: None,
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
    capture_root: Path, active_game: Path, tesseract_engine: None
) -> None:
    from app.exceptions.base import CyclicMessagesRunNotFoundError

    run_id = make_run(capture_root, videos=[])
    with pytest.raises(CyclicMessagesRunNotFoundError, match="no clip to read"):
        await text_service.read_run(run_id)


async def test_a_run_with_several_clips_refuses_to_guess(
    capture_root: Path,
    active_game: Path,
    tesseract_engine: None,
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
    capture_root: Path, active_game: Path, tesseract_engine: None
) -> None:
    from app.exceptions.base import CyclicMessagesRunNotFoundError

    run_id = make_run(capture_root)
    with pytest.raises(CyclicMessagesRunNotFoundError, match="it has: 2"):
        await text_service.read_run(run_id, cycle=9)


async def test_a_clip_that_failed_to_file_is_not_offered(
    capture_root: Path, active_game: Path, tesseract_engine: None
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
    tesseract_engine: None,
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
    tesseract_engine: None,
    half_second: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = make_run(capture_root)
    script(monkeypatch, [("Line 1 Pays 25", 95.0)])

    reading = await text_service.read_run(run_id)

    # The first configured line: the clip reader reads the top one, while
    # the live path reads every line the strip draws.
    assert (
        reading.region == settings.cyclic_messages_text_regions[0] == "cyclic_message"
    )


# --- the interval ---------------------------------------------------------


async def test_the_default_is_one_frame_a_second(
    capture_root: Path,
    active_game: Path,
    tesseract_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One frame a second, so the three-second clip gives three of its thirty
    frames -- and the offsets are a second apart rather than the file's own
    tenth-of-a-second rate."""
    run_id = make_run(capture_root)
    script(monkeypatch, [("Line 1 Pays 25", 95.0)])

    reading = await text_service.read_run(run_id)

    assert settings.CYCLIC_MESSAGES_TEXT_INTERVAL_SECONDS == 1.0
    assert reading.interval_seconds == 1.0
    assert reading.frames_sampled == 3
    assert [frame.frame_index for frame in reading.frames] == [0, 10, 20]
    assert [round(frame.at_seconds, 2) for frame in reading.frames] == [0.0, 1.0, 2.0]


async def test_the_interval_can_be_overridden_per_request(
    capture_root: Path,
    active_game: Path,
    tesseract_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A game whose strip moves faster than FortuneOx's is sampled harder
    without an edit: 0.25s at the clip's 10fps rounds to every second frame,
    so 15 of its 30."""
    run_id = make_run(capture_root)
    script(monkeypatch, [("Line 1 Pays 25", 95.0)])

    reading = await text_service.read_run(run_id, interval_seconds=0.25)

    assert reading.interval_seconds == 0.25
    assert reading.frames_sampled == 15


# --- which engine reads ---------------------------------------------------


async def test_paddle_reads_by_default_and_the_reading_says_so(
    capture_root: Path,
    active_game: Path,
    paddle_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two readings of one clip are only comparable if each says who made it,
    and which engine is a per-host setting."""
    run_id = make_run(capture_root)
    script_paddle(monkeypatch, [("Line 1 Pays 25", 0.97)])

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Tesseract was asked to read on the Paddle path")

    monkeypatch.setattr(ocr, "read_image", refuse)
    # Detection too: a caption crop is already the line, so the reader that
    # goes looking for one inside it is the slow path this must not take.
    monkeypatch.setattr(paddle_ocr, "read_image", refuse)

    reading = await text_service.read_run(run_id)

    assert reading.engine == "paddle"
    assert [message.text for message in reading.messages] == ["Line 1 Pays 25"]


async def test_tesseract_stays_selectable_and_names_itself(
    capture_root: Path,
    active_game: Path,
    tesseract_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = make_run(capture_root)
    script(monkeypatch, [("Line 1 Pays 25", 95.0)])

    reading = await text_service.read_run(run_id)

    assert reading.engine == "tesseract"


async def test_paddles_confidence_is_scaled_onto_tesseracts_range(
    capture_root: Path,
    active_game: Path,
    paddle_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paddle scores 0-1 and the floor is 0-100. Unscaled, every frame Paddle
    ever read would fall under it and the whole clip would report as damage."""
    run_id = make_run(capture_root)
    script_paddle(
        monkeypatch,
        [("Line 1 Pays 25", 0.97), ("Liss 32 Pups 29", 0.48), ("Line 3 Pays 40", 0.91)],
    )

    reading = await text_service.read_run(run_id)

    assert [round(frame.confidence, 1) for frame in reading.frames] == [
        97.0,
        48.0,
        91.0,
    ]
    assert [frame.reliable for frame in reading.frames] == [True, False, True]
    assert reading.frames_unreadable == 1


async def test_paddles_confidence_is_the_worst_region_not_the_best(
    capture_root: Path,
    active_game: Path,
    paddle_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same rule as the Tesseract path: the piece that read least well is what
    makes the caption wrong. Paddle's own ``confidence`` is the *best* region,
    which is exactly the figure that would hide a mangled one."""
    run_id = make_run(capture_root)

    def read_line(_image: Any, **_kwargs: Any) -> paddle_ocr.PaddleResult:
        return paddle_ocr.PaddleResult(
            text="Line 3 Pays 25",
            words=(
                paddle_ocr.PaddleWord(text="Line 3", confidence=0.99),
                paddle_ocr.PaddleWord(text="Pays 25", confidence=0.41),
            ),
            confidence=0.99,  # the best, which would have cleared the floor
            size=SIZE,
        )

    monkeypatch.setattr(paddle_ocr, "read_line", read_line)

    reading = await text_service.read_run(run_id)

    assert round(reading.frames[0].confidence, 1) == 41.0
    assert reading.frames[0].reliable is False


def test_the_live_reader_does_not_recognise_an_empty_band(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Between spins band 1 is empty for the whole window, and recognising it
    anyway was half of that window's reading time for an answer the character
    floor then threw away. It still reports the band -- one reading per band,
    blank ones included, is what the card relies on -- just without asking."""
    frame = Image.new("RGB", (400, 200), (90, 20, 60))
    draw = ImageDraw.Draw(frame)
    for x in range(10, 390, 12):  # something caption-like on the top band only
        draw.rectangle((x, 30, x + 5, 70), fill=(240, 240, 240))
    recognised: list[tuple[int, int]] = []

    def read_line(image: Any, **_kwargs: Any) -> paddle_ocr.PaddleResult:
        recognised.append(image.size)
        return paddle_ocr.PaddleResult(
            text="Line 9 Pays 15",
            words=(paddle_ocr.PaddleWord(text="Line 9 Pays 15", confidence=0.99),),
            confidence=0.99,
            size=image.size,
        )

    monkeypatch.setattr(paddle_ocr, "read_line", read_line)
    reader = text_service.LiveReader(
        game="FortuneOx",
        regions={
            "top": image_roi.Roi(0.0, 0.0, 1.0, 0.5),
            "bottom": image_roi.Roi(0.0, 0.5, 1.0, 1.0),
        },
        model="stub",
        options=paddle_ocr.PaddleLineOptions(model_name="stub"),
    )

    reads = reader.read_image(frame, ("top", "bottom"), beside=tmp_path / "f.jpeg")

    assert len(recognised) == 1, "the empty band was handed to the recogniser"
    assert [(read.region, read.repaired) for read in reads] == [
        ("top", "Line 9 Pays 15"),
        ("bottom", ""),
    ]


async def test_paddle_frames_are_read_one_at_a_time(
    capture_root: Path,
    active_game: Path,
    paddle_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`utils/paddle_ocr` keeps one model for the process and Paddle's are not
    documented as thread-safe, so the pool that pays for Tesseract subprocesses
    is a correctness question here rather than a throughput one."""
    run_id = make_run(capture_root)
    script_paddle(monkeypatch, [("Line 1 Pays 25", 0.97)])

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Paddle captions were handed to a thread pool")

    monkeypatch.setattr(text_service, "ThreadPoolExecutor", refuse)

    reading = await text_service.read_run(run_id)

    assert reading.frames_sampled == 3


async def test_a_paddle_that_will_not_start_fails_the_request(
    client: AsyncClient,
    capture_root: Path,
    active_game: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-flight is the whole reason the per-frame loop may swallow an
    engine error: without it a broken install is ninety blank frames, which
    reads as a strip that showed nothing."""
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_TEXT_ENGINE", "paddle")

    def unavailable(_options: Any) -> str:
        raise ocr.OcrUnavailableError("PaddleOCR is not installed")

    monkeypatch.setattr(paddle_ocr, "prepare_line", unavailable)
    run_id = make_run(capture_root)

    response = await client.get(f"{API}/runs/{run_id}/text")

    assert response.status_code == 409
    assert_failure(response.json(), code="OCR_ENGINE_UNAVAILABLE")


# --- the endpoint ---------------------------------------------------------


async def test_the_endpoint_returns_the_reading_in_the_envelope(
    client: AsyncClient,
    capture_root: Path,
    active_game: Path,
    tesseract_engine: None,
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


async def test_no_tesseract_fails_the_request_rather_than_reading_blanks(
    client: AsyncClient,
    capture_root: Path,
    active_game: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reading with no OCR behind it is a list of empty frames, which looks
    like a strip that showed nothing."""
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_TEXT_ENGINE", "tesseract")

    async def unavailable() -> Path:
        raise OcrEngineUnavailableError("No Tesseract executable was found")

    monkeypatch.setattr(ocr_service, "engine_for_reading", unavailable)
    run_id = make_run(capture_root)

    response = await client.get(f"{API}/runs/{run_id}/text")
    assert response.status_code == 409
    assert_failure(response.json(), code="OCR_ENGINE_UNAVAILABLE")


# --- which bands a clip is read against -----------------------------------
# A run holds two kinds of clip and they are recordings of different strips: a
# win presentation draws one line, and the between-spins strip two. Keyed on
# the kind because two callers ask -- this module's own "Read the messages" and
# `cyclic_messages._recover`, which reads a clip back while the run is going --
# and a caption read one way and the same caption read the other must not come
# off different bands.


def test_a_win_clip_is_read_against_the_win_presentations_own_band() -> None:
    assert (
        text_service.clip_regions(cyclic_service.WIN_VIDEO)
        == settings.cyclic_messages_text_regions
    )


def test_an_idle_clip_is_read_against_both_of_the_between_spins_bands() -> None:
    """The one that stops a losing spin's clip coming back empty: the band a
    win presentation uses is the band this strip leaves blank."""
    assert (
        text_service.clip_regions(cyclic_service.IDLE_VIDEO)
        == settings.cyclic_messages_idle_regions
    )
    assert len(settings.cyclic_messages_idle_regions) == 2


def test_an_unknown_kind_falls_back_to_the_win_presentations_band() -> None:
    """A manifest written before clips said which strip they were of carries no
    kind; reading it against one band is what it was recorded as."""
    assert text_service.clip_regions("") == settings.cyclic_messages_text_regions


# --- what a request actually spends a recognition on ----------------------
# Reading every decoded frame is what stopped "Read the messages" returning: a
# clip is sampled every second, a caption stays up for several, and at ~1.5s a
# recognition a 90s two-band clip is ~180 of them against the client's 290s.
# So `_plan` decides per frame -- read it, share an earlier reading, or take it
# as blank -- and these are the three answers. What is *reported* is unchanged:
# every decoded frame is still a row on `frames`.


def write_still_clip(path: Path) -> None:
    """A clip whose every frame is the same picture, which is what a caption
    held on the strip for several seconds decodes back as."""
    writer = tile_video._open(path, codec(), FPS, SIZE)
    width, height = SIZE
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[196:212, 8:20] = 255
    for _ in range(FRAMES):
        writer.write(frame.copy())
    writer.release()


def write_dark_clip(path: Path) -> None:
    """A clip whose caption band draws nothing, which is the gap between two
    messages and most of a between-spins pass's top band."""
    writer = tile_video._open(path, codec(), FPS, SIZE)
    width, height = SIZE
    for _ in range(FRAMES):
        writer.write(np.zeros((height, width, 3), dtype=np.uint8))
    writer.release()


async def test_a_caption_held_on_the_strip_is_recognised_once(
    client: AsyncClient,
    capture_root: Path,
    active_game: Path,
    tesseract_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repeats share the first frame's reading rather than paying for
    their own.

    Not an approximation: sharing is judged on the *strict* cut, the one that
    separates a frame the encoder repeated outright (0.000) from two
    consecutive line messages differing by a single glyph (0.01-0.03) -- so the
    frames really are the same picture and a second recognition could only
    return the same answer.
    """
    # One entry, and the script would repeat it anyway -- so the assertion is
    # the *count* of calls rather than what came back.
    calls = 0

    def read_image(_image: Any, **_kwargs: Any) -> ocr.OcrResult:
        nonlocal calls
        calls += 1
        return ocr.OcrResult(
            text="Line 1 Pays 250",
            words=(
                ocr.OcrWord(
                    text="Line 1 Pays 250",
                    confidence=96.0,
                    left=0,
                    top=0,
                    width=1,
                    height=1,
                ),
            ),
            confidence=96.0,
            size=SIZE,
        )

    monkeypatch.setattr(ocr, "read_image", read_image)
    run_id = make_run(capture_root)
    write_still_clip(capture_root / run_id / clip_named("cycle-002_win-video_22-13-10"))

    response = await client.get(f"{API}/runs/{run_id}/text?interval_seconds=0.5")
    data = assert_success(response.json())

    assert data["frames_sampled"] > 1
    assert data["frames_read"] == 1
    assert calls == 1
    # Every frame is still reported, and the caption is still one message that
    # was on screen for the whole clip rather than one per frame.
    assert len(data["frames"]) == data["frames_sampled"]
    assert [caption["text"] for caption in data["captions"]] == ["Line 1 Pays 250"]
    assert data["messages"][0]["frames"] == data["frames_sampled"]


async def test_a_band_drawing_nothing_is_not_handed_to_the_engine(
    client: AsyncClient,
    capture_root: Path,
    active_game: Path,
    tesseract_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The strip goes blank between messages, and a recogniser handed an empty
    crop answers with a character it thinks it found in the artwork -- which the
    character floor threw away anyway, so the recognition was pure cost."""
    calls = 0

    def read_image(_image: Any, **_kwargs: Any) -> ocr.OcrResult:
        nonlocal calls
        calls += 1
        raise AssertionError("a blank band should never reach the engine")

    monkeypatch.setattr(ocr, "read_image", read_image)
    run_id = make_run(capture_root)
    write_dark_clip(capture_root / run_id / clip_named("cycle-002_win-video_22-13-10"))

    response = await client.get(f"{API}/runs/{run_id}/text?interval_seconds=0.5")
    data = assert_success(response.json())

    assert calls == 0
    assert data["frames_read"] == 0
    assert data["captions"] == []
    # Reported as frames that read nothing rather than as no frames at all: a
    # strip that showed nothing is a different fact from a clip that would not
    # decode, and only one of them is worth investigating.
    assert data["frames_sampled"] > 0
    assert all(frame["text"] == "" for frame in data["frames"])


async def test_every_decoded_frame_is_still_reported(
    client: AsyncClient,
    capture_root: Path,
    active_game: Path,
    tesseract_engine: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A moving caption is read frame by frame, so nothing is shared and the
    two counts agree -- which is what the grouping tests above rely on."""
    script(monkeypatch, [("Line 1 Pays 250", 96.0)])
    run_id = make_run(capture_root)

    response = await client.get(f"{API}/runs/{run_id}/text?interval_seconds=0.5")
    data = assert_success(response.json())

    assert data["frames_read"] == data["frames_sampled"]


# --- the caption signature, and the two ways it decides two frames differ ---
#
# Every figure asserted here was measured off a real FortuneOx run whose
# between-spins window closed with "GAME PAYS 1000" still to show. The bands
# below are drawn rather than captured, but they land in the same place: a pair
# of consecutive line messages scores 0.014 here against 0.005 on the real
# frames, and the dilution below halves a difference exactly as it did there.


BAND = (240, 32)
"""A caption band, roughly the shape `roi.cyclic_message` crops."""


def caption_band(text: str) -> Image.Image:
    """One band of the strip, drawn in the game's own colours.

    Real ink rather than an abstract pattern, because what is under test is a
    threshold calibrated against glyphs: two of the strip's line messages
    differ by a single digit, and any stand-in that differs more than that
    would pass a cut that the real captions fail.
    """
    image = Image.new("RGB", BAND, (18, 4, 30))
    if text:
        ImageDraw.Draw(image).text((4, 2), text, fill=(255, 225, 130), font=_font())
    return image


def _font() -> Any:
    try:
        return ImageFont.truetype("arialbd.ttf", 22)
    except OSError:  # pragma: no cover - Windows ships this; a host may not
        pytest.skip("Arial Bold is needed to draw a realistic caption band")


def signature_of(*texts: str) -> text_service.Signature:
    """The signature of a frame whose bands say ``texts``, top band first."""
    signature, _showing = text_service.crop_signature(
        tuple(caption_band(text) for text in texts)
    )
    return signature


def test_a_band_that_changed_is_not_diluted_by_one_that_did_not() -> None:
    """The regression that lost a message, and the reason a signature keeps its
    bands apart.

    The strip's bands cycle independently, so most frames change one and leave
    the other alone. Averaged into a single number across both, the band that
    changed is halved by the band that did not -- and on the real run that took
    "Line 35 Pays 25" -> "Line 37 Pays 25" from 0.0298 to 0.0149, under the cut
    where the honest figure was over it.
    """
    before = signature_of("Line 35 Pays 25", "")
    after = signature_of("Line 37 Pays 25", "")

    changed, unchanged = before._scores(after)
    assert changed > text_service._SIGNATURE_SAME
    assert unchanged == 0.0

    # The dilution itself, as the arithmetic it is: one mean across both bands
    # reports half of what the band that moved actually did. On the real frames
    # that was 0.0298 becoming 0.0149, which crossed the 0.05 cut of the day and
    # closed a window early. These drawn bands differ more than the real ones,
    # so halving them no longer crosses the *re-measured* cut -- the two fixes
    # are independent, and this one is that the halving happens at all.
    assert (changed + unchanged) / 2 == pytest.approx(changed / 2)

    # So the frames are neither the same caption nor the same picture.
    assert not before.alike(after)
    assert before.changed(after)


def test_a_change_on_either_band_alone_is_seen() -> None:
    """`alike` is every band and `changed` is any band, so it does not matter
    which of them the strip moved."""
    base = signature_of("Line 35 Pays 25", "Game Over")

    for moved in (
        signature_of("Line 36 Pays 25", "Game Over"),  # top band only
        signature_of("Line 35 Pays 25", "Play 880 Credits"),  # bottom band only
    ):
        assert not base.alike(moved)
        assert base.changed(moved)


def test_two_consecutive_line_messages_are_not_the_same_caption() -> None:
    """What `_SIGNATURE_SAME` is really up against.

    The strip's tightest pair differs by one digit. The cut this was first set
    at (0.05) called 1350 of 2245 genuinely-different pairs of a real run the
    same caption, which is what let a lap close early.
    """
    assert not signature_of("Line 36 Pays 25").alike(signature_of("Line 38 Pays 25"))
    assert not signature_of("Line 1 Pays 25").alike(signature_of("Line 2 Pays 25"))
    # And the same caption twice still is the same caption -- a cut tight enough
    # to be useless would fail here.
    assert signature_of("Line 36 Pays 25").alike(signature_of("Line 36 Pays 25"))


def test_comparing_a_different_number_of_bands_is_a_bug_not_a_skip() -> None:
    """Two frames of one run always have the same bands, so a mismatch is worth
    raising over rather than comparing whatever lines up."""
    with pytest.raises(ValueError, match="cannot compare"):
        signature_of("Game Over").alike(signature_of("Game Over", "Play 880 Credits"))


# --- the lap, and which band decides it ------------------------------------


def write_strip_frame(path: Path, top: str, bottom: str) -> None:
    """A frame with the two caption bands stacked in its lower half, where
    `strip_regions` below says they are."""
    frame = Image.new("RGB", (BAND[0], BAND[1] * 4), (10, 2, 18))
    frame.paste(caption_band(top), (0, BAND[1]))
    frame.paste(caption_band(bottom), (0, BAND[1] * 2))
    frame.save(path)


def strip_regions(*names: str) -> tuple[tuple[str, image_roi.Roi], ...]:
    """The two bands `write_strip_frame` draws, by name."""
    boxes = {
        "cyclic_message": image_roi.Roi(0.0, 0.25, 1.0, 0.5),
        "cyclic_message_2": image_roi.Roi(0.0, 0.5, 1.0, 0.75),
    }
    return tuple((name, boxes[name]) for name in names)


# One pass of the between-spins strip, as a real run captured it: the bottom
# band walks its three messages while the top band keeps stepping through the
# win's line messages, and the bottom band comes round on the last frame.
IDLE_PASS = (
    ("Line 35 Pays 25", "Play 880 Credits"),
    ("Line 36 Pays 25", "Game Over"),
    ("Line 37 Pays 25", "Game Pays 1000"),
    ("Line 38 Pays 25", "Play 880 Credits"),
)


def run_lap(tmp_path: Path, *names: str) -> list[bool]:
    """`LoopWatch.saw` over :data:`IDLE_PASS`, watching ``names``."""
    watch = text_service.LoopWatch(regions=strip_regions(*names))
    answers = []
    for index, (top, bottom) in enumerate(IDLE_PASS):
        path = tmp_path / f"frame-{index}.png"
        write_strip_frame(path, top, bottom)
        answers.append(watch.saw(path))
    return answers


def test_the_lap_closes_when_the_watched_band_comes_round(tmp_path: Path) -> None:
    """Watching the band whose cycle this window is *for*, the lap closes on
    the frame it returns to its first message -- and not before, so everything
    it had to show was captured."""
    assert run_lap(tmp_path, "cyclic_message_2") == [False, False, False, True]


def test_the_lap_does_not_close_while_the_watched_band_has_more_to_show(
    tmp_path: Path,
) -> None:
    """The bug, from the other end: `Game Pays 1000` is the third frame, so a
    window that closed on the second or third never captured it."""
    closed = run_lap(tmp_path, "cyclic_message_2")

    assert not any(closed[:3])
    # And the message was still on screen when the window was open.
    assert IDLE_PASS[2][1] == "Game Pays 1000"


def test_watching_both_bands_cannot_close_the_lap(tmp_path: Path) -> None:
    """Why the watch is one band and not every band.

    The top band walks forty line messages while the bottom band walks three,
    so a lap of "both" is really a lap of the longer one: it does not close
    here at all, and the window would run to
    CYCLIC_MESSAGES_IDLE_MAX_SECONDS. Safe, but a minute of frames and OCR
    spent re-reading line messages the win presentation already captured.
    """
    assert run_lap(tmp_path, "cyclic_message", "cyclic_message_2") == [False] * 4


def test_a_blank_band_is_passed_over_rather_than_counted(tmp_path: Path) -> None:
    """The strip goes blank between two messages, and a gap is not a caption.

    Counted as one it would both invent a message and make the next real frame
    look like a return -- closing the lap before the strip had been round.
    """
    watch = text_service.LoopWatch(regions=strip_regions("cyclic_message_2"))
    for index, bottom in enumerate(("Play 880 Credits", "", "Game Over", "")):
        path = tmp_path / f"gap-{index}.png"
        write_strip_frame(path, "Line 35 Pays 25", bottom)
        assert watch.saw(path) is False

    assert watch.blank == 2
    assert len(watch.seen) == 2


def test_the_between_spins_watch_uses_the_lap_regions_setting(
    active_game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_loop_watch` asks for the lap bands, not every band the strip has."""
    (active_game / "FortuneOx.json").write_text(
        json.dumps(
            {
                "name": "FortuneOx",
                "process": "FortuneOx.exe",
                "roi": {
                    "cyclic_message": [0.0, 0.848, 0.18, 0.867],
                    "cyclic_message_2": [0.0, 0.870, 0.18, 0.889],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        settings, "CYCLIC_MESSAGES_IDLE_LOOP_REGIONS", "cyclic_message_2"
    )
    log_path = active_game / "game.log"
    log_path.write_text("", encoding="utf-8")
    run = cyclic_service._ActiveRun(
        run_id="r",
        game="FortuneOx",
        directory=active_game,
        output_dir="cyclic-messages/r",
        log=LogFollower(log_path),
        started_at=datetime(2026, 9, 18, 15, 2, 23),
    )

    watch = cyclic_service._loop_watch(run)

    assert watch is not None
    # Band 1 is configured and still captured -- it simply has no vote here.
    assert [name for name, _roi in watch.regions] == ["cyclic_message_2"]


def test_naming_no_lap_region_falls_back_to_every_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emptying the setting is not the same as watching nothing."""
    monkeypatch.setattr(settings, "CYCLIC_MESSAGES_IDLE_LOOP_REGIONS", "")

    assert (
        settings.cyclic_messages_idle_loop_regions
        == settings.cyclic_messages_idle_regions
    )


# --- the frame a losing spin takes with no window open ---------------------


async def test_a_frame_captured_outside_a_window_is_still_read(
    tmp_path: Path, active_game: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cyclic-game-over` is a caption, and it used to be thrown away.

    It is captured 400ms after ``GameOverMsg`` while ``cyclic-idle-strip-started``
    is the *next* line, so on a losing spin the one picture the log takes is
    written before any window opens. Gated on `pass_open`, its caption was never
    read -- measured on a real losing spin, that frame reads "Game Over" at 97
    and nothing else in the run ever looked at it.
    """
    directory = tmp_path / "run"
    directory.mkdir()
    log_path = tmp_path / "game.log"
    log_path.write_text("", encoding="utf-8")
    run = cyclic_service._ActiveRun(
        run_id="r",
        game="FortuneOx",
        directory=directory,
        output_dir="cyclic-messages/r",
        log=LogFollower(log_path),
        started_at=datetime(2026, 9, 18, 15, 42, 59),
    )
    # The window has not opened yet, and a *previous* win presentation left its
    # own bands behind -- which is why the regions cannot come from there.
    run.pass_open = False
    run.pass_regions = ("cyclic_message",)

    frame = directory / "003_cyclic-game-over_15-42-59.jpeg"
    Image.new("RGB", (64, 64), (10, 2, 18)).save(frame)

    cyclic_service._queue_read(run, sequence=3, saved=frame)

    assert [item.sequence for item in run.pending_reads] == [3]
    # The between-spins bands, not the win presentation's leftover one -- the
    # strip that is on screen with no window open draws two lines.
    assert run.pending_reads[0].regions == settings.cyclic_messages_idle_regions


async def test_a_frame_inside_a_window_is_read_as_that_window(
    tmp_path: Path, active_game: Path
) -> None:
    """The other half: a frame taken inside a pass keeps that pass's own bands,
    so a win presentation's frame is still read as a win presentation's."""
    directory = tmp_path / "run"
    directory.mkdir()
    log_path = tmp_path / "game.log"
    log_path.write_text("", encoding="utf-8")
    run = cyclic_service._ActiveRun(
        run_id="r",
        game="FortuneOx",
        directory=directory,
        output_dir="cyclic-messages/r",
        log=LogFollower(log_path),
        started_at=datetime(2026, 9, 18, 15, 42, 59),
    )
    run.pass_open = True
    run.pass_regions = settings.cyclic_messages_text_regions

    frame = directory / "004_cyclic-line-pays-shown_15-43-01.jpeg"
    Image.new("RGB", (64, 64), (10, 2, 18)).save(frame)

    cyclic_service._queue_read(run, sequence=4, saved=frame)

    assert run.pending_reads[0].regions == settings.cyclic_messages_text_regions


def _bare_run(tmp_path: Path) -> Any:
    """An `_ActiveRun` with nothing running, for the bookkeeping tests."""
    directory = tmp_path / "run"
    directory.mkdir(exist_ok=True)
    log_path = tmp_path / "game.log"
    log_path.write_text("", encoding="utf-8")
    return cyclic_service._ActiveRun(
        run_id="r",
        game="FortuneOx",
        directory=directory,
        output_dir="cyclic-messages/r",
        log=LogFollower(log_path),
        started_at=datetime(2026, 9, 18, 16, 42, 19),
    )


def _frame_event(sequence: int, event: str, cycle: int) -> Any:
    from app.schemas.cyclic_messages import CyclicEvent

    return CyclicEvent(
        sequence=sequence,
        event=event,
        summary=event,
        cycle=cycle,
        captured=True,
        screenshot=f"{sequence:03d}_{event}.jpeg",
        log_line="x",
    )


async def test_the_frame_taken_before_a_window_joins_that_window(
    tmp_path: Path, active_game: Path
) -> None:
    """The between-spins strip's first message, which was captured and read and
    still missing from the card.

    ``GameOverMsg`` is what puts "GAME OVER" on the strip and its frame is
    taken 400ms later, but the line that opens the window -- and increments the
    cycle -- is the *next* one. Filed under the outgoing cycle, `live()` scopes
    to one cycle and filtered it straight out. Measured on a real losing spin:
    the frame read "Game Over" at 97 and the card showed two messages of three.
    """
    run = _bare_run(tmp_path)
    run.events.append(_frame_event(3, "cyclic-game-over", cycle=1))
    run.index[3] = 0
    run.unfiled.append(3)

    # The between-spins window opens, as `cyclic-idle-strip-started` does it.
    run.cycle = 2
    cyclic_service._adopt(run)

    assert run.events[0].cycle == 2
    assert run.unfiled == []


async def test_adopting_leaves_the_frames_of_a_finished_window_alone(
    tmp_path: Path, active_game: Path
) -> None:
    """Only the frames with no cycle of their own move. A win presentation's
    own frames stay in the sequence that presented the win, which is the whole
    reason `live_cycle` exists."""
    run = _bare_run(tmp_path)
    run.events.append(_frame_event(4, "cyclic-line-pays-shown", cycle=1))
    run.index[4] = 0

    run.cycle = 2
    cyclic_service._adopt(run)

    assert run.events[0].cycle == 1


async def test_a_frame_inside_a_window_is_never_unfiled(
    tmp_path: Path, active_game: Path
) -> None:
    """`pass_open` is what decides it, so a sampled frame already has a cycle
    and must not be re-filed into the next window."""
    run = _bare_run(tmp_path)
    run.pass_open = True
    run.pass_regions = settings.cyclic_messages_idle_regions
    frame = run.directory / "006_cyclic-idle-message-shown.jpeg"
    Image.new("RGB", (64, 64), (10, 2, 18)).save(frame)

    cyclic_service._queue_read(run, sequence=6, saved=frame)

    assert run.unfiled == []
