"""How Evaluate Screen (and Scatter Value Validation through it) takes its frame.

Only the capture is covered here. Everything Evaluate Screen *reads* -- the grid
split, the classifier, PaddleOCR on a scatter, the cash meter -- is Analyze
Spin's, tested where it lives; what is this module's own is the one step before
all of them, and it is the step that was wrong.

``obs.connect()`` ends in ``select_current_game_window`` on **every** call, not
only the one that opens the session. Re-pointing a window capture makes OBS
render nothing for a moment and it reports a successful write of that nothing,
so a capture taken straight after connecting is a black frame that OBS calls a
success -- which is not a dark reading but every reading below it silently
meaningless. Hence two things, both Analyze Spin's and both asserted below: wait
for the source to settle, and look at what was written rather than trusting it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.runtime import settings
from app.exceptions.base import ScreenEvaluationFailedError
from app.schemas.obs import ScreenshotResult
from app.services import evaluate_screen as evaluate_screen_service
from app.services import obs as obs_service
from app.services import roi as roi_service


@pytest.fixture
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real settle and retry *ordering* while paying none of its
    seconds -- what is under test is that the waits happen, not how long."""
    monkeypatch.setattr(settings, "ANALYZE_SPIN_SOURCE_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(settings, "ANALYZE_SPIN_BLANK_RETRY_SECONDS", 0.0)


def _stub_obs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, calls: list[str]
) -> None:
    """OBS that records the order it was asked things in and writes a file."""

    async def connect() -> None:
        calls.append("connect")

    async def take_screenshot(payload: object) -> ScreenshotResult:
        calls.append("screenshot")
        written = tmp_path / f"screen-{len(calls)}.png"
        written.touch()
        return ScreenshotResult(
            source_name="Scene", image_format="png", file_path=str(written)
        )

    monkeypatch.setattr(obs_service, "connect", connect)
    monkeypatch.setattr(obs_service, "take_screenshot", take_screenshot)


async def test_the_source_settles_before_the_frame_is_taken(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_waiting: None
) -> None:
    """The wait sits between connecting and capturing, which is the whole point
    of it: connecting is what re-points the window capture."""
    calls: list[str] = []
    _stub_obs(monkeypatch, tmp_path, calls)
    monkeypatch.setattr(roi_service, "is_blank", lambda _path: False)

    async def settle(seconds: float) -> None:
        calls.append("settle")

    monkeypatch.setattr(evaluate_screen_service.asyncio, "sleep", settle)

    await evaluate_screen_service._capture()

    assert calls == ["connect", "settle", "screenshot"]


async def test_a_blank_frame_is_retried_rather_than_returned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_waiting: None
) -> None:
    """OBS calls a frame it rendered nothing into a successful write, so the
    frame is read back -- and an empty one is taken again."""
    calls: list[str] = []
    _stub_obs(monkeypatch, tmp_path, calls)
    monkeypatch.setattr(settings, "ANALYZE_SPIN_BLANK_RETRIES", 2)

    seen: list[Path] = []

    def is_blank(path: Path) -> bool:
        seen.append(path)
        return len(seen) == 1  # the first frame lands inside the re-point

    monkeypatch.setattr(roi_service, "is_blank", is_blank)

    path, blank = await evaluate_screen_service._capture()

    assert blank is False
    assert path == seen[1]
    assert calls.count("screenshot") == 2


async def test_a_frame_that_stays_blank_is_reported_not_retried_forever(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_waiting: None
) -> None:
    """Past the retries the empty frame comes back *as* empty. Reported rather
    than raised: the caller says which reading it invalidated, and a screen
    genuinely showing black is a thing OBS can legitimately capture."""
    calls: list[str] = []
    _stub_obs(monkeypatch, tmp_path, calls)
    monkeypatch.setattr(settings, "ANALYZE_SPIN_BLANK_RETRIES", 2)
    monkeypatch.setattr(roi_service, "is_blank", lambda _path: True)

    _, blank = await evaluate_screen_service._capture()

    assert blank is True
    assert calls.count("screenshot") == 3  # the first, then both retries


async def test_a_screenshot_obs_wrote_nowhere_is_an_error(
    monkeypatch: pytest.MonkeyPatch, _no_waiting: None
) -> None:
    """There is no frame to read back, so this one does raise."""

    async def connect() -> None:
        return None

    async def take_screenshot(payload: object) -> ScreenshotResult:
        return ScreenshotResult(source_name="Scene", image_format="png")

    monkeypatch.setattr(obs_service, "connect", connect)
    monkeypatch.setattr(obs_service, "take_screenshot", take_screenshot)

    with pytest.raises(ScreenEvaluationFailedError):
        await evaluate_screen_service._capture()
