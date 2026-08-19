"""Game-window input runtime settings.

The game-input service also consumes per-game data from
:mod:`app.config.game_config` -- the aim points come from that game's
``button_targets`` block, and the log to confirm a click against comes from its
``log`` key. This module contains only deployment-specific settings: which
window to find, and the click/verification behavior.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings

__all__ = ["GameInputSettings"]


class GameInputSettings(BaseSettings):
    """Runtime options for clicking the game's own window."""

    # The simulator is a single top-level Unity window. Its title is the game's
    # own name -- "FortuneOx", matching the active game config -- so the title
    # is normally left blank here and taken from the game config instead. Set it
    # to pin the search to something else.
    GAME_INPUT_WINDOW_TITLE: str = ""
    # Every Unity standalone player uses this class. Pinning it stops a
    # same-named Explorer window or editor tab from being driven by mistake.
    GAME_INPUT_WINDOW_CLASS: str = "UnityWndClass"

    # Hold between button-down and button-up. The game's UI reacts on the *up*,
    # so this only has to be long enough for both to be pumped in order.
    GAME_INPUT_CLICK_HOLD_SECONDS: float = 0.08
    # With verification off, a click reports success as soon as it is posted.
    # Leave it on: a coordinate that has drifted lands on nothing, and silence
    # is the only symptom.
    GAME_INPUT_VERIFY_CLICKS: bool = True
    # The game publishes its decision in the same millisecond it takes the
    # touch, so this is headroom for log flushing rather than for the game.
    GAME_INPUT_VERIFY_TIMEOUT_SECONDS: float = 2.0
    # A minimized window has no client area to aim at, so it is restored first
    # -- without activating it, so focus is left alone.
    GAME_INPUT_RESTORE_IF_MINIMIZED: bool = True
    # A window that has never been focused can swallow the first posted click.
    # When one goes unconfirmed, foreground the game and try once more. Still no
    # cursor movement.
    GAME_INPUT_FOCUS_ON_RETRY: bool = True
