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
    ObsRecordStatus,
    ObsStatus,
    ScreenshotRequest,
    ScreenshotResult,
)
from app.schemas.response import ErrorDetail
from app.utils.paths import UnsafeNameError, resolve_within

logger = get_logger("obs")

_client: simpleobsws.WebSocketClient | None = None
_lock: asyncio.Lock | None = None

# OBS flips the record output asynchronously: a status read taken immediately
# after StartRecord or StopRecord still reports the previous value. Measured on
# OBS 32: starting lands in ~0.1s, stopping in ~1.2s as the file is finalised.
# These bound how long we wait for the real state before answering, well inside
# the frontend's 15s request timeout.
_SETTLE_ATTEMPTS = 30
_SETTLE_DELAY_SECONDS = 0.1


# --- internals ------------------------------------------------------------


def _get_lock() -> asyncio.Lock:
    """Return the module lock, created inside whichever loop is running.

    Built lazily rather than at import time because ``tests/conftest.py`` makes a
    fresh event loop per test, and a lock holding waiters from a dead loop is a
    hazard. :func:`reset` clears it, so each test gets its own.
    """
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
    """Run the connect and identify handshake.

    Raises:
        ObsConnectionError: if OBS is unreachable, closes the socket, or does not
            identify before ``OBS_CONNECT_TIMEOUT_SECONDS`` elapses.
    """
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
    """Return an identified client, re-handshaking once if the socket dropped.

    Raises:
        ObsNotConnectedError: if a connection has never been established.
        ObsConnectionError: if the dropped socket could not be re-identified.
    """
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
    """Send one request to OBS and return its response data.

    The single chokepoint for every OBS interaction. Calls serialise on the
    module lock, so one socket is never multiplexed.

    Raises:
        ObsNotConnectedError: if a connection has never been established.
        ObsConnectionError: if the session is gone and cannot be re-established.
        ObsRequestError: if OBS rejected the request or did not answer in time.
    """
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


def _resolve_capture_path(file_name: str, image_format: str) -> Path:
    """Resolve a caller-supplied filename inside the capture directory.

    Callers pass a bare filename, never a path. Without this the screenshot
    endpoint would let any client write a file anywhere the OBS process can
    reach. The check itself lives in :func:`app.utils.paths.resolve_within`;
    what happens here is turning its refusal into the HTTP contract.

    Raises:
        BadRequestError: if the name carries a path separator or a parent
            reference, or would otherwise land outside the capture directory.
    """
    try:
        return resolve_within(
            settings.obs_capture_dir, file_name, default_suffix=image_format
        )
    except UnsafeNameError as exc:
        raise BadRequestError(
            "file_name must be a bare filename inside the capture directory: "
            f"{exc.reason}",
            details=[
                ErrorDetail(field="body.file_name", message=exc.reason, type="value")
            ],
        ) from exc


async def _current_scene() -> str:
    """Name of the active program scene.

    obs-websocket exposes no dedicated "program output" source, so capturing
    everything currently on screen means capturing the program scene by name.

    Raises:
        ObsRequestError: if OBS reports no current scene.
    """
    data = await _call("GetCurrentProgramScene")
    name = _as_str(data.get("sceneName"))
    if not name:
        raise ObsRequestError("OBS did not report a current program scene")
    return name


async def _set_record_directory() -> None:
    """Point OBS's recording directory at the capture directory.

    Best effort: a failure is logged rather than raised, so an older OBS that
    lacks the request does not block connecting.
    """
    directory = settings.obs_capture_dir
    directory.mkdir(parents=True, exist_ok=True)
    try:
        await _call("SetRecordDirectory", {"recordDirectory": str(directory)})
    except AppException as exc:
        logger.warning("Could not set the OBS recording directory: %s", exc.message)
    else:
        logger.info("OBS recording directory set to %s", directory)


async def _settled_record_status(
    *, active: bool | None = None, paused: bool | None = None
) -> ObsRecordStatus:
    """Read the record status once OBS reflects the change that was requested.

    Returning the first read would report a state we know is stale -- pressing
    Start would answer ``active: false``. Falls back to whatever OBS last said
    if the flip never lands, so a genuinely stuck output is still reported
    rather than hidden behind a hang.
    """
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
    """Open and identify a session with OBS.

    Safe to call repeatedly: an already-identified session is reused. When
    ``OBS_SET_RECORD_DIRECTORY`` is on, OBS's own recording directory is pointed
    at the capture directory -- note that this change persists in the user's OBS
    profile after the app exits.

    Raises:
        ObsConnectionError: if OBS is unreachable or rejects the password.
    """
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
        await _set_record_directory()
    return await status()


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
    """Capture a screenshot of a source or scene.

    With no ``file_name`` the image comes back as a base64 data URI, ready for an
    ``<img>`` tag. With one, OBS writes the file into the capture directory and
    only the path is returned.

    Raises:
        BadRequestError: if ``file_name`` is not a bare filename.
        ObsNotConnectedError: if a connection has never been established.
        ObsConnectionError: if the session is gone and cannot be re-established.
        ObsRequestError: if OBS rejected the request or returned no image.
    """
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

    if payload.file_name is None:
        data = await _call("GetSourceScreenshot", request_data)
        image_data = _as_str(data.get("imageData"))
        if not image_data:
            raise ObsRequestError("OBS returned a screenshot with no image data")
        return ScreenshotResult(
            source_name=source_name,
            image_format=payload.image_format,
            image_data=image_data,
        )

    # Resolved before the request, so a rejected name never reaches OBS.
    target = _resolve_capture_path(payload.file_name, payload.image_format)
    target.parent.mkdir(parents=True, exist_ok=True)
    request_data["imageFilePath"] = str(target)
    await _call("SaveSourceScreenshot", request_data)
    logger.info("Saved a screenshot of %r to %s", source_name, target)
    return ScreenshotResult(
        source_name=source_name,
        image_format=payload.image_format,
        file_path=str(target),
    )


async def record_status() -> ObsRecordStatus:
    """Current state of the recording output."""
    return _record_status(await _call("GetRecordStatus"))


async def start_recording() -> ObsRecordStatus:
    """Start recording.

    Raises:
        ObsRequestError: if a recording is already running.
    """
    await _call("StartRecord")
    logger.info("OBS recording started")
    return await _settled_record_status(active=True)


async def stop_recording() -> ObsRecordStatus:
    """Stop recording and report where the file landed.

    obs-websocket documents ``outputPath`` on the ``RecordStateChanged`` event
    rather than on ``StopRecord``, so it is read defensively and left null when
    absent.

    Raises:
        ObsRequestError: if no recording is running.
    """
    data = await _call("StopRecord")
    output_path = _as_str(data.get("outputPath"))
    logger.info("OBS recording stopped (output=%s)", output_path or "unreported")
    settled = await _settled_record_status(active=False)
    return settled.model_copy(update={"output_path": output_path})


async def pause_recording() -> ObsRecordStatus:
    """Pause the running recording.

    Some OBS recording formats cannot pause. OBS accepts the request anyway and
    simply leaves the output running, so the returned ``paused`` flag is the
    thing to trust, not the fact that the call succeeded.

    Raises:
        ObsRequestError: if no recording is running, or it is already paused.
    """
    await _call("PauseRecord")
    logger.info("OBS recording paused")
    return await _settled_record_status(paused=True)


async def resume_recording() -> ObsRecordStatus:
    """Resume a paused recording.

    Raises:
        ObsRequestError: if the recording is not paused.
    """
    await _call("ResumeRecord")
    logger.info("OBS recording resumed")
    return await _settled_record_status(paused=False)


def reset() -> None:
    """Drop the client and the lock without touching the network.

    Mirrors ``item_service.reset()``: tests call it between cases. Clearing the
    lock matters as much as clearing the client -- the next test builds one bound
    to its own event loop.
    """
    global _client, _lock
    _client = None
    _lock = None
