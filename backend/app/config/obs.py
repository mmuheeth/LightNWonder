"""OBS Studio runtime settings.

The environment variable names intentionally remain flat (``OBS_HOST``,
``OBS_PORT`` and so on). :class:`app.config.runtime.Settings` inherits this
model, so callers keep the existing ``settings.OBS_*`` API while the OBS
defaults and derived paths live with the integration they configure.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings

__all__ = ["ObsSettings"]


class ObsSettings(BaseSettings):
    """Runtime options for the OBS websocket integration."""

    # obs-websocket v5, bundled with OBS Studio 28+. Enable the server under
    # Tools > WebSocket Server Settings. Paths below are resolved on the OBS
    # host, which is the same machine as the backend unless OBS_HOST is remote.
    OBS_HOST: str = "127.0.0.1"
    OBS_PORT: int = 4455
    OBS_PASSWORD: SecretStr = SecretStr("")
    OBS_AUTO_CONNECT: bool = False
    OBS_CONNECT_TIMEOUT_SECONDS: float = 5.0
    OBS_REQUEST_TIMEOUT_SECONDS: float = 10.0

    # Screenshots -- and recordings, when OBS_SET_RECORD_DIRECTORY is on --
    # are written here. Relative paths resolve against the working directory,
    # which for this project is always `backend/`.
    OBS_CAPTURE_DIR: Path = Path("OBS-capture")
    OBS_SET_RECORD_DIRECTORY: bool = True

    @property
    def obs_url(self) -> str:
        """WebSocket URL of the obs-websocket server."""
        return f"ws://{self.OBS_HOST}:{self.OBS_PORT}"

    @property
    def obs_capture_dir(self) -> Path:
        """Absolute capture directory; OBS rejects relative output paths."""
        return self.OBS_CAPTURE_DIR.resolve()
