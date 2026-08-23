"""Read the ``button_targets`` block of a game config: click points as fractions
of the window client area, same convention as :mod:`app.utils.image_roi`'s
regions, so a resize needs no re-measurement. A target may also carry a
``confirm`` event name (from :mod:`app.utils.game_log`) proving the click landed.
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


# Read off FortuneOx's and HuffNPuffLink's own logs, not assumed: both resolve
# a glass touch to a named message, so it says which button was hit.
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
    """Event proving a click landed, or ``None`` to fall back to a generic touch."""

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
        """Read a target from decoded JSON, in either shorthand or dict form.

        An existing :class:`ClickTarget` passes through unchanged.
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
        """Convert a pixel point (measured off a screenshot) to the
        resolution-independent fraction form the rest of the module uses."""
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
        """Pixel position inside a window of this size, rounded and clamped
        inside the window (a rounded edge case can land one pixel outside)."""
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
    """Look one target up by name (case-insensitively) in a config's
    ``button_targets`` block, which is passed through unvalidated by the loader."""
    wanted = name.strip().casefold()
    key = next((found for found in targets if found.casefold() == wanted), None)
    if key is None:
        known = ", ".join(target_names(targets)) or "none"
        raise ClickTargetError(
            f"no button target named {name!r} is configured "
            f"(configured targets: {known})"
        )
    return ClickTarget.from_value(targets[key], name=key, where=f"button_targets.{key}")
