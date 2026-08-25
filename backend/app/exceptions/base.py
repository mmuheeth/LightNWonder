"""Domain exception hierarchy. Business code raises these; only
:mod:`app.exceptions.handlers` turns them into the response envelope. Add a new
error by subclassing :class:`AppException` and setting its three class attributes.
"""

from __future__ import annotations

from http import HTTPStatus

from app.schemas.response import ErrorDetail


class AppException(Exception):
    """Base class for every expected, client-facing failure."""

    status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR
    error_code: str = "INTERNAL_SERVER_ERROR"
    message: str = "An unexpected error occurred"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: list[ErrorDetail] | None = None,
        error_code: str | None = None,
        status_code: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or []
        self.error_code = error_code or self.error_code
        self.status_code = status_code or self.status_code
        self.headers = headers
        super().__init__(self.message)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(status_code={self.status_code}, "
            f"error_code={self.error_code!r}, message={self.message!r})"
        )


class BadRequestError(AppException):
    status_code = HTTPStatus.BAD_REQUEST
    error_code = "BAD_REQUEST"
    message = "The request could not be understood"


class UnauthorizedError(AppException):
    status_code = HTTPStatus.UNAUTHORIZED
    error_code = "UNAUTHORIZED"
    message = "Authentication is required"


class ForbiddenError(AppException):
    status_code = HTTPStatus.FORBIDDEN
    error_code = "FORBIDDEN"
    message = "You do not have permission to perform this action"


class NotFoundError(AppException):
    status_code = HTTPStatus.NOT_FOUND
    error_code = "NOT_FOUND"
    message = "The requested resource was not found"


class ConflictError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "CONFLICT"
    message = "The request conflicts with the current state of the resource"


class UnprocessableEntityError(AppException):
    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    error_code = "UNPROCESSABLE_ENTITY"
    message = "The request was well-formed but could not be processed"


class RateLimitError(AppException):
    status_code = HTTPStatus.TOO_MANY_REQUESTS
    error_code = "RATE_LIMIT_EXCEEDED"
    message = "Too many requests; please retry later"


class ServiceUnavailableError(AppException):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    error_code = "SERVICE_UNAVAILABLE"
    message = "The service is temporarily unavailable"


# --- OBS Studio -----------------------------------------------------------
# 409 for "not connected" (fixed by connecting, not retrying); 5xx gets the
# frontend's Retry button.


class ObsNotConnectedError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "OBS_NOT_CONNECTED"
    message = "Not connected to OBS Studio"


class ObsConnectionError(AppException):
    status_code = HTTPStatus.BAD_GATEWAY
    error_code = "OBS_CONNECTION_FAILED"
    message = "Could not establish a connection to OBS Studio"


class ObsRequestError(AppException):
    status_code = HTTPStatus.BAD_GATEWAY
    error_code = "OBS_REQUEST_FAILED"
    message = "OBS Studio rejected the request"


# --- Virtual OLED i-deck --------------------------------------------------
# Same split as OBS: 409 when the caller must act (launch panel, name a real
# button), 502 when the press could not be proven.


class IDeckWindowNotFoundError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "IDECK_WINDOW_NOT_FOUND"
    message = "The Virtual OLED window is not open"


class IDeckAccessDeniedError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "IDECK_ACCESS_DENIED"
    message = "Windows is blocking input to the Virtual OLED window"


class IDeckButtonNotFoundError(AppException):
    status_code = HTTPStatus.NOT_FOUND
    error_code = "IDECK_BUTTON_NOT_FOUND"
    message = "No such i-deck button"


class IDeckPressNotConfirmedError(AppException):
    status_code = HTTPStatus.BAD_GATEWAY
    error_code = "IDECK_PRESS_NOT_CONFIRMED"
    message = "The i-deck press was sent but the panel never registered it"


class IDeckConfigError(AppException):
    status_code = HTTPStatus.INTERNAL_SERVER_ERROR
    error_code = "IDECK_CONFIG_INVALID"
    message = "The i-deck configuration could not be loaded"


# --- Game window input ----------------------------------------------------
# Kept apart from i-deck's errors since the frontend branches on error_code and
# those name the panel. Same 409/404/502 split: launch game / unknown target / unproven click.


class GameWindowNotFoundError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "GAME_WINDOW_NOT_FOUND"
    message = "The game window is not open"


class GameInputAccessDeniedError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "GAME_INPUT_ACCESS_DENIED"
    message = "Windows is blocking input to the game window"


class GameTargetNotFoundError(AppException):
    status_code = HTTPStatus.NOT_FOUND
    error_code = "GAME_TARGET_NOT_FOUND"
    message = "No such button target in the active game's config"


class GameClickNotConfirmedError(AppException):
    status_code = HTTPStatus.BAD_GATEWAY
    error_code = "GAME_CLICK_NOT_CONFIRMED"
    message = "The click was sent but the game never registered it"


class GameInputConfigError(AppException):
    status_code = HTTPStatus.INTERNAL_SERVER_ERROR
    error_code = "GAME_INPUT_CONFIG_INVALID"
    message = "The game input configuration could not be loaded"


# --- Game selection ------------------------------------------------------


class GameNotFoundError(AppException):
    status_code = HTTPStatus.NOT_FOUND
    error_code = "GAME_NOT_FOUND"
    message = "The requested game configuration was not found"


class GameConfigInvalidError(AppException):
    status_code = HTTPStatus.INTERNAL_SERVER_ERROR
    error_code = "GAME_CONFIG_INVALID"
    message = "A game configuration could not be loaded"


# --- Paytable -------------------------------------------------------------
# The maths lives in the game's own install, not in this repo, so the split is
# by who has to fix it: 409 the machine (the game isn't installed, or its config
# names no GameConfig directory), 404 the request or the log (that paytable id
# has no folder), 502 the files themselves (present but unreadable as maths).


class PaytableUnavailableError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "PAYTABLE_UNAVAILABLE"
    message = "The active game declares no installed GameConfig directory"


class PaytableNotFoundError(AppException):
    status_code = HTTPStatus.NOT_FOUND
    error_code = "PAYTABLE_NOT_FOUND"
    message = "No paytable folder matches that id"


class PaytableInvalidError(AppException):
    status_code = HTTPStatus.BAD_GATEWAY
    error_code = "PAYTABLE_INVALID"
    message = "The game's maths files could not be read"


# --- Event Based Capture --------------------------------------------------
# Same 409-vs-404 split: 409 means the caller must act first, 404 means the
# run they asked for isn't there.


class EventCaptureAlreadyRunningError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "EVENT_CAPTURE_ALREADY_RUNNING"
    message = "An event capture run is already in progress"


class EventCaptureNotRunningError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "EVENT_CAPTURE_NOT_RUNNING"
    message = "No event capture run is in progress"


class EventCaptureLogUnavailableError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "EVENT_CAPTURE_LOG_UNAVAILABLE"
    message = "The active game's log is not available to follow"


class EventCaptureRunNotFoundError(AppException):
    status_code = HTTPStatus.NOT_FOUND
    error_code = "EVENT_CAPTURE_RUN_NOT_FOUND"
    message = "The requested capture run was not found"


# --- OCR ------------------------------------------------------------------
# 409: no Tesseract install (a Retry button would be a lie). 404: region not
# declared by the active game (a typo, not an unreadable meter). 502: no frame
# to give the engine.


class OcrEngineUnavailableError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "OCR_ENGINE_UNAVAILABLE"
    message = "No usable Tesseract OCR engine is available"


class OcrRegionNotFoundError(AppException):
    status_code = HTTPStatus.NOT_FOUND
    error_code = "OCR_REGION_NOT_FOUND"
    message = "No such region in the active game's config"


class OcrReadFailedError(AppException):
    status_code = HTTPStatus.BAD_GATEWAY
    error_code = "OCR_READ_FAILED"
    message = "The frame to read could not be obtained"


# --- ROI extraction -------------------------------------------------------
# 404: unknown region or no screenshot on disk (a wrong name). 502: the file is
# there but not a readable image.


class RoiRegionNotFoundError(AppException):
    status_code = HTTPStatus.NOT_FOUND
    error_code = "ROI_REGION_NOT_FOUND"
    message = "No such region in the active game's config"


class RoiFrameNotFoundError(AppException):
    status_code = HTTPStatus.NOT_FOUND
    error_code = "ROI_FRAME_NOT_FOUND"
    message = "No screenshot is available to extract a region from"


class RoiExtractFailedError(AppException):
    status_code = HTTPStatus.BAD_GATEWAY
    error_code = "ROI_EXTRACT_FAILED"
    message = "The region could not be extracted from the frame"


# --- Reel grid ------------------------------------------------------------
# Only two of its own: a missing screenshot reuses ROI's own errors, since the
# grid reads the same frame off the same directory.


class GridNotConfiguredError(AppException):
    status_code = HTTPStatus.NOT_FOUND
    error_code = "GRID_NOT_CONFIGURED"
    message = "The active game does not describe a reel grid"


class GridSplitFailedError(AppException):
    status_code = HTTPStatus.BAD_GATEWAY
    error_code = "GRID_SPLIT_FAILED"
    message = "The reel grid could not be split out of the frame"


# --- Payline check --------------------------------------------------------
# Names which of three inputs is missing: the patterns, the split, or agreement
# between the two. No frame error -- the check reads a split the grid already wrote.


class PaylinesNotConfiguredError(AppException):
    status_code = HTTPStatus.NOT_FOUND
    error_code = "PAYLINES_NOT_CONFIGURED"
    message = "The active game does not describe the paylines to check"


class PaylineSourceNotFoundError(AppException):
    status_code = HTTPStatus.NOT_FOUND
    error_code = "PAYLINE_SOURCE_NOT_FOUND"
    message = "There is no split reel grid to check the paylines against"


class PaylineSourceStaleError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "PAYLINE_SOURCE_STALE"
    message = "The split on disk does not match the grid the game now declares"


class PaylineCheckFailedError(AppException):
    status_code = HTTPStatus.BAD_GATEWAY
    error_code = "PAYLINE_CHECK_FAILED"
    message = "The paylines could not be checked against the split"


# --- Analyze spin ---------------------------------------------------------
# One run exists process-wide, so the only HTTP failures are about that: 409
# when a run is or is not in the state the caller assumed, and 409 again when
# the machine cannot host a run at all. Everything a run does *while* running
# fails on its own step, with the underlying service's own error -- an
# unconfirmed press is still `IDECK_PRESS_NOT_CONFIRMED`, not a spin error.


class SpinAnalysisAlreadyRunningError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "SPIN_ANALYSIS_ALREADY_RUNNING"
    message = "A spin analysis is already in progress"


class SpinAnalysisNotRunningError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "SPIN_ANALYSIS_NOT_RUNNING"
    message = "No spin analysis is in progress"


class SpinAnalysisUnavailableError(AppException):
    status_code = HTTPStatus.CONFLICT
    error_code = "SPIN_ANALYSIS_UNAVAILABLE"
    message = "The active game cannot be analysed on this machine"
