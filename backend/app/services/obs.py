"""OBS Studio control over obs-websocket v5.

One long-lived, module-level session, exactly like the other services here hold
their state in module globals. Every request funnels through :func:`_call`, so
connection handling and error translation live in a single place.

**Reconnect is lazy.** :func:`_ensure_connected` re-runs the handshake when it
finds the socket dropped, so a closed-and-reopened OBS heals on the next
request. There is deliberately no background reconnect task: it would not exist
under the test transport (which never runs the app lifespan), it could outlive a
cancelled request, and pytest runs with ``filterwarnings = error``, where a
stray pending-task warning fails the suite.

Callers use the namespace, not the functions::

    from app.services import obs as obs_service
    await obs_service.start_recording()
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import simpleobsws
from websockets.exceptions import WebSocketException

from app.config.game_config import (
    ActiveGameSelectionError,
    GameConfig,
    GameConfigError,
    load_game_config,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    AppException,
    BadRequestError,
    ObsConnectionError,
    ObsNotConnectedError,
    ObsRequestError,
)
from app.schemas.obs import (
    ObsConnectionState,
    ObsGameWindowSelection,
    ObsRecordStatus,
    ObsStatus,
    ScreenshotRequest,
    ScreenshotResult,
)
from app.schemas.response import ErrorDetail
from app.utils.paths import UnsafeNameError, resolve_subdirectory, resolve_within

logger = get_logger("obs")

_client: simpleobsws.WebSocketClient | None = None
_lock: asyncio.Lock | None = None

# OBS flips the record output asynchronously (measured on OBS 32: ~0.1s to
# start, ~1.2s to stop) -- these bound how long to wait for the real state,
# well inside the frontend's 15s request timeout.
_SETTLE_ATTEMPTS = 30
_SETTLE_DELAY_SECONDS = 0.1

# Never built from the process name alone: an empty window class matches
# nothing and renders 0x0, which is why the value is read from OBS's own list.
_WINDOW_CAPTURE_KIND_PREFIX = "window_capture"
_WINDOW_PRIORITY_EXE = 2
_WINDOW_PROPERTY = "window"
_WINDOW_IDENTIFIER_PARTS = 3


# --- internals ------------------------------------------------------------


def _get_lock() -> asyncio.Lock:
    """Return the module lock, created lazily (not at import time) since each
    test gets a fresh event loop and :func:`reset` clears it between tests."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _as_str(value: object) -> str | None:
    """Narrow an untyped obs-websocket field to ``str | None``."""
    return value if isinstance(value, str) else None


def _new_client() -> simpleobsws.WebSocketClient:
    return simpleobsws.WebSocketClient(
        url=settings.obs_url,
        password=settings.OBS_PASSWORD.get_secret_value(),
    )


async def _identify(client: simpleobsws.WebSocketClient) -> None:
    """Run the connect and identify handshake."""
    try:
        await client.connect()
        # TimeoutError subclasses OSError, so the handler below covers a
        # handshake that never completes.
        identified = await client.wait_until_identified(
            timeout=settings.OBS_CONNECT_TIMEOUT_SECONDS
        )
    except (OSError, WebSocketException) as exc:
        raise ObsConnectionError(
            f"Could not reach OBS at {settings.obs_url} ({type(exc).__name__}). "
            "Is OBS running with its WebSocket server enabled?"
        ) from exc

    if not identified:
        raise ObsConnectionError(
            f"OBS at {settings.obs_url} did not accept the connection. Check that "
            "the WebSocket server is enabled and that OBS_PASSWORD matches."
        )


async def _ensure_connected() -> simpleobsws.WebSocketClient:
    """Return an identified client, re-handshaking once if the socket dropped."""
    client = _client
    if client is None:
        raise ObsNotConnectedError(
            "Not connected to OBS Studio. Connect first via POST /api/obs/connect."
        )
    if not client.is_identified():
        logger.info("OBS session dropped; reconnecting to %s", settings.obs_url)
        await _identify(client)
    return client


async def _call(
    request_type: str, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Send one request to OBS and return its response data. The single
    chokepoint for every OBS interaction; calls serialise on the module lock."""
    async with _get_lock():
        client = await _ensure_connected()
        try:
            response = await client.call(
                simpleobsws.Request(request_type, data),
                timeout=settings.OBS_REQUEST_TIMEOUT_SECONDS,
            )
        except simpleobsws.MessageTimeout as exc:
            raise ObsRequestError(
                f"OBS did not answer {request_type} within "
                f"{settings.OBS_REQUEST_TIMEOUT_SECONDS}s"
            ) from exc
        except simpleobsws.NotIdentifiedError as exc:
            raise ObsConnectionError(
                f"OBS session is not identified; {request_type} was not sent"
            ) from exc
        except (OSError, WebSocketException) as exc:
            raise ObsConnectionError(
                f"OBS connection dropped during {request_type} ({type(exc).__name__})"
            ) from exc

    # Checked outside the lock: the socket is free again either way.
    if not response.ok():
        result = response.requestStatus
        raise ObsRequestError(
            f"OBS rejected {request_type} (code {result.code}): "
            f"{result.comment or 'no detail provided'}"
        )
    return dict(response.responseData or {})


def _record_status(
    data: dict[str, Any], *, output_path: str | None = None
) -> ObsRecordStatus:
    """Translate a ``GetRecordStatus`` response into the API schema."""
    written = data.get("outputBytes", data.get("outputTotalBytes", 0))
    return ObsRecordStatus(
        active=bool(data.get("outputActive", False)),
        paused=bool(data.get("outputPaused", False)),
        timecode=_as_str(data.get("outputTimecode")),
        duration_ms=int(data.get("outputDuration") or 0),
        bytes_written=int(written or 0),
        output_path=output_path,
    )


def _resolve_output_dir(root: Path, output_dir: str | None) -> Path:
    """Resolve an optional use-case directory below a configured output root."""
    if output_dir is None:
        return root.resolve()

    try:
        return resolve_subdirectory(root, output_dir)
    except UnsafeNameError as exc:
        raise BadRequestError(
            "output_dir must be a relative directory inside the configured "
            f"capture root: {exc.reason}",
            details=[
                ErrorDetail(field="body.output_dir", message=exc.reason, type="value")
            ],
        ) from exc


def _resolve_capture_path(
    file_name: str, image_format: str, output_dir: str | None
) -> Path:
    """Resolve a caller-supplied filename inside the screenshot output root —
    a bare filename only, or the endpoint would let a client write anywhere
    the OBS process can reach."""
    root = _resolve_output_dir(settings.obs_screenshot_dir, output_dir)
    try:
        return resolve_within(root, file_name, default_suffix=image_format)
    except UnsafeNameError as exc:
        raise BadRequestError(
            "file_name must be a bare filename inside the output directory: "
            f"{exc.reason}",
            details=[
                ErrorDetail(field="body.file_name", message=exc.reason, type="value")
            ],
        ) from exc


async def _current_scene() -> str:
    """Name of the active program scene -- obs-websocket exposes no dedicated
    "program output" source, so capturing the screen means capturing this by name."""
    data = await _call("GetCurrentProgramScene")
    name = _as_str(data.get("sceneName"))
    if not name:
        raise ObsRequestError("OBS did not report a current program scene")
    return name


def _active_game() -> GameConfig:
    """Load the one game config selected in the game-config directory."""
    try:
        active_game = settings.ideck_active_game
        return load_game_config(settings.ideck_game_config_path_for(active_game))
    except ActiveGameSelectionError as exc:
        raise ObsRequestError(str(exc)) from exc
    except GameConfigError as exc:
        raise ObsRequestError(
            f"Could not load the active game config {active_game!r}: {exc}"
        ) from exc


def _decode_obs_window_component(value: str) -> str:
    """Decode one component of OBS's colon-delimited window identifier.
    ``#3A`` expands before ``#22``, or an encoded literal ``#3A`` would become a separator."""
    return value.replace("#3A", ":").replace("#22", "#")


def _window_identifier_parts(identifier: str) -> tuple[str, str, str] | None:
    """Split an OBS ``title:class:executable`` identifier into decoded parts, or
    ``None`` for anything that isn't a three-component identifier."""
    parts = identifier.split(":")
    if len(parts) != _WINDOW_IDENTIFIER_PARTS:
        return None
    title, window_class, executable = (_decode_obs_window_component(p) for p in parts)
    return title, window_class, executable


def _require_process(game: GameConfig) -> str:
    """Executable name the active game config declares."""
    process = (game.process or "").strip()
    if not process:
        raise ObsRequestError(
            "The active game config must define a non-empty "
            "'process' for OBS window selection"
        )
    return process


def _scene_source_names(data: dict[str, Any]) -> list[str]:
    """Read unique source names from a ``GetSceneItemList`` response."""
    items = data.get("sceneItems")
    if not isinstance(items, list):
        raise ObsRequestError("OBS returned no usable scene-item list")

    names: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _as_str(item.get("sourceName"))
        if name and name not in names:
            names.append(name)
    return names


def _window_source_names(data: dict[str, Any], scene_sources: list[str]) -> list[str]:
    """Return window-capture inputs that are present in the active scene."""
    inputs = data.get("inputs")
    if not isinstance(inputs, list):
        raise ObsRequestError("OBS returned no usable input list")

    scene_source_set = set(scene_sources)
    names: list[str] = []
    for item in inputs:
        if not isinstance(item, dict):
            continue
        name = _as_str(item.get("inputName"))
        kind = _as_str(item.get("inputKind"))
        if (
            name
            and name in scene_source_set
            and kind
            and kind.casefold().startswith(_WINDOW_CAPTURE_KIND_PREFIX)
            and name not in names
        ):
            names.append(name)
    return names


async def _window_source_for_game(scene: str, configured_source: str | None) -> str:
    """Find the active scene's window-capture input for the selected game."""
    scene_data = await _call("GetSceneItemList", {"sceneName": scene})
    scene_sources = _scene_source_names(scene_data)
    input_data = await _call("GetInputList")
    candidates = _window_source_names(input_data, scene_sources)

    if configured_source:
        if configured_source not in candidates:
            found = ", ".join(candidates) or "none"
            raise ObsRequestError(
                f"OBS source {configured_source!r} from the active game config "
                f"is not a window-capture source in scene {scene!r} "
                f"(found: {found})"
            )
        return configured_source

    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ObsRequestError(
            f"OBS scene {scene!r} has no window-capture source. Add one to the "
            "active program scene or set 'obs.window_source' in the game config."
        )
    raise ObsRequestError(
        f"OBS scene {scene!r} has multiple window-capture sources "
        f"({', '.join(candidates)}); set 'obs.window_source' in the active "
        "game config to choose one"
    )


async def _game_window(source_name: str, process: str) -> tuple[str, str]:
    """Find OBS's own identifier for the window owned by ``process``, taken from
    OBS's enumerated window list rather than assembled from the process name --
    a synthesised ``::game.exe`` matches nothing and fails silently much later
    as an empty screenshot. Returns the identifier and the window title."""
    data = await _call(
        "GetInputPropertiesListPropertyItems",
        {"inputName": source_name, "propertyName": _WINDOW_PROPERTY},
    )
    items = data.get("propertyItems")
    if not isinstance(items, list):
        raise ObsRequestError(
            f"OBS returned no capturable-window list for source {source_name!r}"
        )

    wanted = process.casefold()
    matches: list[tuple[str, str]] = []
    seen_executables: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # OBS's placeholder for a stored value with no live window -- never a candidate.
        if item.get("itemEnabled") is False:
            continue
        parts = _window_identifier_parts(_as_str(item.get("itemValue")) or "")
        if parts is None:
            continue
        title, _, executable = parts
        if executable and executable not in seen_executables:
            seen_executables.append(executable)
        if executable.casefold() == wanted:
            matches.append((_as_str(item.get("itemValue")) or "", title))

    if not matches:
        found = ", ".join(sorted(seen_executables)) or "none"
        raise ObsRequestError(
            f"OBS lists no capturable window for {process!r}. Is the game "
            f"running with a visible window? OBS currently sees: {found}"
        )

    # A process can own several top-level windows; a titled one is the game
    # itself rather than a helper window, so it wins.
    matches.sort(key=lambda match: not match[1])
    if len(matches) > 1:
        logger.warning(
            "OBS lists %d windows for %s; capturing %r",
            len(matches),
            process,
            matches[0][1] or matches[0][0],
        )
    return matches[0]


async def _set_record_directory(directory: Path, *, required: bool = False) -> None:
    """Point OBS's recording directory at ``directory``. Connection setup treats
    this as best effort; a recording start requires it."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        if required:
            raise ObsRequestError(
                f"Could not create the OBS recording directory {directory}: {exc}"
            ) from exc
        logger.warning(
            "Could not create the OBS recording directory %s: %s", directory, exc
        )
        return

    try:
        await _call("SetRecordDirectory", {"recordDirectory": str(directory)})
    except AppException as exc:
        if required:
            raise
        logger.warning("Could not set the OBS recording directory: %s", exc.message)
    else:
        logger.info("OBS recording directory set to %s", directory)


async def _settled_record_status(
    *, active: bool | None = None, paused: bool | None = None
) -> ObsRecordStatus:
    """Read the record status once OBS reflects the change requested -- the
    first read would still be stale. Falls back to OBS's last word if the flip
    never lands, rather than hanging."""
    remaining = _SETTLE_ATTEMPTS
    while True:
        current = await record_status()
        settled = (active is None or current.active == active) and (
            paused is None or current.paused == paused
        )
        if settled:
            return current
        if remaining <= 0:
            logger.warning(
                "OBS record output did not reach active=%s paused=%s within %.1fs",
                active,
                paused,
                _SETTLE_ATTEMPTS * _SETTLE_DELAY_SECONDS,
            )
            return current
        remaining -= 1
        await asyncio.sleep(_SETTLE_DELAY_SECONDS)


# --- public API -----------------------------------------------------------


async def connect() -> ObsStatus:
    """Open and identify a session with OBS. Safe to call repeatedly. When
    ``OBS_SET_RECORD_DIRECTORY`` is on, OBS's recording directory is pointed at
    the configured root -- this persists in the user's OBS profile afterward."""
    global _client
    async with _get_lock():
        if _client is not None and _client.is_identified():
            logger.debug("OBS already connected to %s", settings.obs_url)
        else:
            client = _client or _new_client()
            await _identify(client)
            _client = client
            logger.info("Connected to OBS at %s", settings.obs_url)

    # Both of these go through _call(), which takes the lock, so they stay
    # outside it: asyncio.Lock is not reentrant.
    if settings.OBS_SET_RECORD_DIRECTORY:
        await _set_record_directory(settings.obs_recording_dir)

    # Best effort during connection -- the dedicated endpoint below lets the
    # caller retry once the scene is ready.
    try:
        await select_current_game_window()
    except AppException as exc:
        logger.warning("Could not select the active game's OBS window: %s", exc.message)
    return await status()


async def select_current_game_window() -> ObsGameWindowSelection:
    """Point the active scene's window-capture source at the active game's
    process, resolved against the window list OBS enumerates. Safe to repeat."""
    await _ensure_connected()
    game = _active_game()
    process = _require_process(game)
    scene = await _current_scene()
    source_name = await _window_source_for_game(scene, game.obs_window_source)
    window, window_title = await _game_window(source_name, process)

    await _call(
        "SetInputSettings",
        {
            "inputName": source_name,
            "inputSettings": {
                "window": window,
                # Keep matching by executable so a relaunch under a new window
                # title still binds without re-running this.
                "priority": _WINDOW_PRIORITY_EXE,
            },
            "overlay": True,
        },
    )
    logger.info(
        "OBS window source %r now follows %s (%s) in scene %r",
        source_name,
        game.name,
        window,
        scene,
    )
    return ObsGameWindowSelection(
        game=game.name,
        process=process,
        scene=scene,
        source_name=source_name,
        window_title=window_title or None,
    )


async def disconnect() -> ObsStatus:
    """Close the OBS session. Idempotent, and never raises."""
    global _client
    async with _get_lock():
        client, _client = _client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                logger.exception("Error while disconnecting from OBS")
            else:
                logger.info("Disconnected from OBS at %s", settings.obs_url)
    return ObsStatus(state=ObsConnectionState.DISCONNECTED, url=settings.obs_url)


async def status() -> ObsStatus:
    """Report the connection state, plus what OBS says about itself when live.

    Never raises: a closed OBS is a state worth reporting, not a failed request.
    """
    client = _client
    if client is None or not client.is_identified():
        return ObsStatus(state=ObsConnectionState.DISCONNECTED, url=settings.obs_url)

    try:
        version = await _call("GetVersion")
        scene = await _call("GetCurrentProgramScene")
        record = await _call("GetRecordStatus")
    except AppException as exc:
        logger.warning("Could not read OBS status: %s", exc.message)
        return ObsStatus(state=ObsConnectionState.DISCONNECTED, url=settings.obs_url)

    return ObsStatus(
        state=ObsConnectionState.CONNECTED,
        url=settings.obs_url,
        obs_version=_as_str(version.get("obsVersion")),
        obs_websocket_version=_as_str(version.get("obsWebSocketVersion")),
        platform=_as_str(version.get("platform")),
        current_scene=_as_str(scene.get("sceneName")),
        recording=_record_status(record),
    )


async def take_screenshot(payload: ScreenshotRequest) -> ScreenshotResult:
    """Capture a screenshot of a source or scene, always as a base64 data URI.
    Supplying ``file_name`` also has OBS write the file to disk."""
    source_name = payload.source_name or await _current_scene()
    request_data: dict[str, Any] = {
        "sourceName": source_name,
        "imageFormat": payload.image_format,
        "imageCompressionQuality": payload.quality,
    }
    if payload.width is not None:
        request_data["imageWidth"] = payload.width
    if payload.height is not None:
        request_data["imageHeight"] = payload.height

    # Resolved before any OBS call, so a rejected name never reaches OBS.
    target = (
        _resolve_capture_path(
            payload.file_name, payload.image_format, payload.output_dir
        )
        if payload.file_name is not None
        else None
    )

    # Always fetched, so the caller gets an inline preview whether or not the
    # shot is also being saved to disk.
    data = await _call("GetSourceScreenshot", request_data)
    image_data = _as_str(data.get("imageData"))
    if not image_data:
        raise ObsRequestError("OBS returned a screenshot with no image data")

    if target is None:
        return ScreenshotResult(
            source_name=source_name,
            image_format=payload.image_format,
            image_data=image_data,
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    request_data["imageFilePath"] = str(target)
    await _call("SaveSourceScreenshot", request_data)
    logger.info("Saved a screenshot of %r to %s", source_name, target)
    return ScreenshotResult(
        source_name=source_name,
        image_format=payload.image_format,
        image_data=image_data,
        file_path=str(target),
    )


async def record_status() -> ObsRecordStatus:
    """Current state of the recording output."""
    return _record_status(await _call("GetRecordStatus"))


async def start_recording(output_dir: str | None = None) -> ObsRecordStatus:
    """Start recording in the default or requested use-case directory."""
    directory: Path | None = None
    if output_dir is not None or settings.OBS_SET_RECORD_DIRECTORY:
        directory = _resolve_output_dir(settings.obs_recording_dir, output_dir)
        await _set_record_directory(directory, required=True)
    await _call("StartRecord")
    logger.info(
        "OBS recording started%s",
        f" in {directory}"
        if directory is not None
        else " in OBS's configured directory",
    )
    return await _settled_record_status(active=True)


async def stop_recording() -> ObsRecordStatus:
    """Stop recording and report where the file landed. ``outputPath`` is read
    defensively and left null when absent -- obs-websocket documents it on the
    ``RecordStateChanged`` event, not on ``StopRecord``."""
    data = await _call("StopRecord")
    output_path = _as_str(data.get("outputPath"))
    logger.info("OBS recording stopped (output=%s)", output_path or "unreported")
    settled = await _settled_record_status(active=False)
    return settled.model_copy(update={"output_path": output_path})


async def pause_recording() -> ObsRecordStatus:
    """Pause the running recording. Some formats can't pause -- OBS accepts the
    request anyway, so trust the returned ``paused`` flag, not call success."""
    await _call("PauseRecord")
    logger.info("OBS recording paused")
    return await _settled_record_status(paused=True)


async def resume_recording() -> ObsRecordStatus:
    """Resume a paused recording."""
    await _call("ResumeRecord")
    logger.info("OBS recording resumed")
    return await _settled_record_status(paused=False)


def reset() -> None:
    """Drop the client and the lock without touching the network -- the next
    test's lock must bind to its own event loop."""
    global _client, _lock
    _client = None
    _lock = None
