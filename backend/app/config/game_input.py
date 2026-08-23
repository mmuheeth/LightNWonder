"""Game-window input runtime settings: which window to find, and click/verify
behavior. Aim points and confirm log come from the active game's config.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings

__all__ = ["GameInputSettings"]


class GameInputSettings(BaseSettings):
    """Runtime options for clicking the game's own window."""

    # Left blank to take the title from the active game config; set to override.
    GAME_INPUT_WINDOW_TITLE: str = ""
    # Every Unity standalone player uses this class; pins the search away from
    # a same-named Explorer/editor window.
    GAME_INPUT_WINDOW_CLASS: str = "UnityWndClass"

    # Hold between button-down and button-up; the UI reacts on the up.
    GAME_INPUT_CLICK_HOLD_SECONDS: float = 0.08
    # Off reports success as soon as a click is posted; a drifted coordinate
    # would then fail silently.
    GAME_INPUT_VERIFY_CLICKS: bool = True
    # Headroom for log flushing, not for the game (it decides within the ms).
    GAME_INPUT_VERIFY_TIMEOUT_SECONDS: float = 2.0
    # A minimized window has no client area to aim at; restored without
    # activating, so focus is left alone.
    GAME_INPUT_RESTORE_IF_MINIMIZED: bool = True
    # An unfocused window can swallow the first click; on an unconfirmed press,
    # foreground the game and retry once (still no cursor movement).
    GAME_INPUT_FOCUS_ON_RETRY: bool = True
