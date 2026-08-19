"""The reusable log cursor.

:class:`LogTail` is already exercised through the presses that depend on it in
``test_ideck.py``, and :class:`LogFollower` through the runs in
``test_event_capture.py``. What is here is the follower's own contract, tested
directly because two features now build on it: where it starts, what counts as
new, and that a rotation does not end the follow.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from app.utils.log_tail import LogFollower


def append(log: Path, *lines: str) -> None:
    with log.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(f"{line}\n")


async def wait_until(ready: Callable[[], bool], *, timeout: float = 2.0) -> None:
    """Poll rather than sleep, so the test is neither slow nor flaky."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not ready():
        assert asyncio.get_running_loop().time() < deadline, (
            "the follower never caught up"
        )
        await asyncio.sleep(0.01)


def test_a_follower_starts_at_the_end_of_the_file(tmp_path: Path) -> None:
    """History is not news. A run should not open with every line since launch."""
    log = tmp_path / "game.log"
    append(log, "old one", "old two")

    follower = LogFollower(log)

    assert follower.new_lines() == []


def test_only_what_arrived_since_the_last_look_is_returned(tmp_path: Path) -> None:
    log = tmp_path / "game.log"
    log.touch()
    follower = LogFollower(log)

    append(log, "first")
    assert follower.new_lines() == ["first"]

    append(log, "second", "third")
    assert follower.new_lines() == ["second", "third"]
    assert follower.new_lines() == []


def test_the_cursor_can_be_wound_back_to_take_the_whole_file(tmp_path: Path) -> None:
    """The one documented reason to touch the cursor: reading from the start."""
    log = tmp_path / "game.log"
    append(log, "already there")

    follower = LogFollower(log)
    follower.cursor = 0

    assert follower.new_lines() == ["already there"]


def test_a_rotated_log_is_followed_into_its_replacement(tmp_path: Path) -> None:
    """A file smaller than the cursor has started over, not ended."""
    log = tmp_path / "game.log"
    log.touch()
    follower = LogFollower(log)

    append(log, "before the rotation")
    assert follower.new_lines() == ["before the rotation"]

    log.write_text("", encoding="utf-8")
    append(log, "after the rotation")

    assert follower.new_lines() == ["after the rotation"]


def test_a_log_that_is_not_there_yields_nothing_rather_than_raising(
    tmp_path: Path,
) -> None:
    """Reads never raise -- whether that is a failure is the caller's question."""
    follower = LogFollower(tmp_path / "never-written.log")

    assert follower.new_lines() == []


async def test_follow_hands_over_every_line_until_it_is_cancelled(
    tmp_path: Path,
) -> None:
    log = tmp_path / "game.log"
    log.touch()
    follower = LogFollower(log, poll_seconds=0.01)

    seen: list[str] = []

    async def handle(line: str) -> None:
        seen.append(line)

    task = asyncio.create_task(follower.follow(handle))
    try:
        append(log, "one", "two")
        await wait_until(lambda: len(seen) == 2)

        append(log, "three")
        await wait_until(lambda: len(seen) == 3)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert seen == ["one", "two", "three"]
