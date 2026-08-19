"""Read the ``button_targets`` block of a game config.

A game config names the on-screen buttons a person would touch on the cabinet
glass, as fractions of the frame::

    "button_targets": {
      "take_win": [0.0713, 0.9724],
      "gamble":   [0.0694, 0.9383]
    }

Fractions rather than pixels for the same reason :mod:`app.utils.image_roi` uses
them: the two numbers describe the same spot on the glass whether the simulator
window is 518x1033 or twice that, so a resize costs nobody a re-measurement. The
sibling module crops a region *out* of a frame; this one aims a click *into* a
window, and the fractions mean the same thing in both -- a position in the whole
rectangle, letterboxing included.

A target may also say how a click on it can be proven to have landed::

    "gamble": {"point": [0.0694, 0.9383], "confirm": "gamble-accepted"}

``confirm`` names an event in :mod:`app.utils.game_log`'s vocabulary. It is
carried as a bare string here: which events exist, and what their patterns are,
is that module's business, and resolving one is the caller's. The plain
two-number form keeps working and picks up :data:`DEFAULT_CONFIRMATIONS`.

Nothing here knows about windows, logs or game configs. It takes the decoded
block and hands back points; where the block came from is the caller's business.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "DEFAULT_CONFIRMATIONS",
    "ClickTarget",
    "ClickTargetError",
    "named_target",
    "target_names",
]


class ClickTargetError(ValueError):
    """A target is malformed, unusable, or names nothing that is configured."""


# What proves a click on a target landed, for the targets every game shares.
#
# Read off the games' own logs rather than assumed. Both FortuneOx and
# HuffNPuffLink resolve a touch on the glass to a named message before anything
# else happens, so the message says which button was hit and not merely that
# something was::
#
#     msg[double_up_offer_accept]    -> the player chose to gamble
#     msg[double_up_offer_decline]   -> the player took the win
#
# ``gamble-accepted`` and ``gamble-declined`` are the :mod:`app.utils.game_log`
# rules that match those, in both of the shapes the two games write them.
DEFAULT_CONFIRMATIONS: Mapping[str, str] = {
    "take_win": "gamble-declined",
    "gamble": "gamble-accepted",
}


@dataclass(frozen=True)
class ClickTarget:
    """One aim point, as fractions of the window it will be clicked in."""

    x: float
    y: float

    confirm: str | None = None
    """Event name that proves a click here landed, or ``None`` if none is known.

    ``None`` does not make the target unusable: a caller can still fall back to
    proving the game registered *a* touch. It only means the log cannot say
    which button was hit.
    """

    def __post_init__(self) -> None:
        """Reject a point no window could be clicked at."""
        for axis, value in (("x", self.x), ("y", self.y)):
            # Also catches NaN, for which every comparison is false.
            if not 0.0 <= value <= 1.0:
                raise ClickTargetError(
                    f"{axis} must be a fraction between 0 and 1, got {value!r}"
                )

    @classmethod
    def from_value(
        cls, value: Any, *, name: str = "", where: str = "button target"
    ) -> ClickTarget:
        """Read a target from decoded JSON, in either declared form.

        An existing :class:`ClickTarget` passes through unchanged, so a caller
        can take either form without first asking which it was given.

        Args:
            value: The decoded value. Untrusted -- it may be any JSON shape.
            name: The target's key, used to pick a default confirmation. Pass
                nothing when the target is not being read out of a named block.
            where: What is being read, used to make the error locatable.
        """
        if isinstance(value, cls):
            return value

        confirm: str | None = DEFAULT_CONFIRMATIONS.get(name)
        point: Any = value
        if isinstance(value, Mapping):
            if "point" not in value:
                raise ClickTargetError(f"{where} is missing its 'point'")
            point = value["point"]
            declared = value.get("confirm", confirm)
            if declared is None:
                confirm = None
            elif isinstance(declared, str) and declared.strip():
                confirm = declared.strip()
            else:
                raise ClickTargetError(
                    f"{where}: 'confirm' must be a non-empty event name, "
                    f"got {declared!r}"
                )

        x, y = _two_numbers(point, where=where)
        try:
            return cls(x=x, y=y, confirm=confirm)
        except ClickTargetError as exc:
            # The range errors name an axis but not the target it came from.
            raise ClickTargetError(f"{where}: {exc}") from exc

    @classmethod
    def from_pixels(
        cls,
        point: Sequence[float],
        *,
        width: int,
        height: int,
        confirm: str | None = None,
        where: str = "button target",
    ) -> ClickTarget:
        """Convert a pixel point measured on a frame of a known size.

        A target gets measured by opening one screenshot in an image editor and
        reading coordinates off it. This turns that reading into the
        resolution-independent form the rest of the module works in, so the
        conversion happens once instead of at every use.
        """
        if width <= 0 or height <= 0:
            raise ClickTargetError("the frame must have a non-zero width and height")
        x, y = _two_numbers(point, where=where)
        try:
            return cls(x=x / width, y=y / height, confirm=confirm)
        except ClickTargetError as exc:
            raise ClickTargetError(
                f"{where} does not fit a {width}x{height} frame: {exc}"
            ) from exc

    def to_point(self, width: int, height: int) -> tuple[int, int]:
        """Pixel position inside a window of this size.

        Rounded rather than truncated so the point stays proportional as the
        window grows, and clamped inside the window because a rounded edge case
        can land one pixel outside -- and a click must fall inside the window it
        is addressed to.

        Raises:
            ClickTargetError: if the window has no area to aim at.
        """
        if width <= 0 or height <= 0:
            raise ClickTargetError("the window must have a non-zero width and height")
        return (
            min(max(round(self.x * width), 0), width - 1),
            min(max(round(self.y * height), 0), height - 1),
        )


def _two_numbers(value: Any, *, where: str) -> tuple[float, float]:
    """Narrow a decoded JSON value to the two coordinates of a point."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ClickTargetError(f"{where} must be an array of two numbers")
    if len(value) != 2:
        raise ClickTargetError(f"{where} must be [x, y], got {len(value)} numbers")

    point: list[float] = []
    for coordinate in value:
        # `bool` is an `int`, and `True` as a coordinate is a mistake, not a 1.
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise ClickTargetError(
                f"{where} must contain only numbers, got {coordinate!r}"
            )
        point.append(float(coordinate))
    x, y = point
    return x, y


def target_names(targets: Mapping[str, Any]) -> list[str]:
    """Every configured target name, sorted, for listings and error messages."""
    return sorted(targets)


def named_target(targets: Mapping[str, Any], name: str) -> ClickTarget:
    """Look one target up by name in a config's ``button_targets`` block.

    Names are matched case-insensitively, because the block is hand-written and a
    caller should not have to remember whether it said ``take_win`` or
    ``Take_Win``.

    That block is passed through by the config loader unvalidated, so this is
    where a typo in a target name or in its numbers becomes a sentence saying
    which target it was and what was wrong with it.
    """
    wanted = name.strip().casefold()
    key = next((found for found in targets if found.casefold() == wanted), None)
    if key is None:
        known = ", ".join(target_names(targets)) or "none"
        raise ClickTargetError(
            f"no button target named {name!r} is configured "
            f"(configured targets: {known})"
        )
    return ClickTarget.from_value(targets[key], name=key, where=f"button_targets.{key}")
