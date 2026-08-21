"""Domain exception hierarchy.

Business code raises these; :mod:`app.exceptions.handlers` turns them into the
standard response envelope. Nothing outside the handlers should build an HTTP
response for an error.

Add a new error type by subclassing :class:`AppException` and setting the three
class attributes::

    class PaymentRequiredError(AppException):
        status_code = 402
        error_code = "PAYMENT_REQUIRED"
        message = "Payment is required to continue"
"""

from __future__ import annotations

from http import HTTPStatus

from app.schemas.response import ErrorDetail


class AppException(Exception):
    """Base class for every expected, client-facing failure.

    Attributes:
        status_code: HTTP status to respond with.
        error_code: Stable code clients can branch on.
        message: Default human-readable message.
    """

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
# 409 rather than 503 for "not connected": the client fixes it by connecting,
# not by retrying. The frontend only offers a Retry button for 5xx, so this
# split gives each failure the right affordance.


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
# Same split as OBS above: 409 when the caller has to go do something (launch
# the panel, name a real button) and 502 when the panel is there but the press
# could not be proven. Only the 5xx offers a Retry button in the frontend, which
# is the right affordance for each.


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
# Kept apart from the i-deck errors above rather than reusing them: those name
# the Virtual OLED panel in both their code and their message, and the frontend
# branches on the code. The same 409/404/502 split applies -- 409 when the caller
# has to go do something (launch the game), 404 when they named a target that is
# not configured, 502 when the window is there but the click could not be proven.


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


# --- Event Based Capture --------------------------------------------------
# Same 409-vs-404 split as above: a 409 means the caller has to go do something
# (stop the run that is already going, pick a game whose config names a log,
# open OBS), while a 404 means they asked for a run that is not there.


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
# The same split once more. A missing Tesseract install is a 409: the caller fixes
# it by installing the engine or pointing OCR_TESSERACT_CMD at it, and a Retry
# button would be a lie. A 404 is a region the active game does not declare, which
# is a typo rather than an unreadable meter -- a region that is configured but
# could not be read comes back as a reading with an error on it, not as a failed
# request. A 502 is for the frame: the engine is there, and there was nothing to
# give it.


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
# The same split once more, one door further along. A 404 is a region the active
# game does not declare, or a frame that is not on disk -- both of them a wrong
# name rather than a broken crop. A 502 is a file that is there and is not a
# readable image, which is the one failure the caller can do nothing about
# except take another screenshot.


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
# Only two of its own, because the grid splitter reads the same frame off the
# same directory as ROI does and raises ROI's own errors for it -- a missing
# screenshot is a missing screenshot, and duplicating the code for it would mean
# two messages to keep pointing at the OBS panel. What is new here is a game
# that does not describe a reel grid at all, and a split that could not be
# written to disk.


class GridNotConfiguredError(AppException):
    status_code = HTTPStatus.NOT_FOUND
    error_code = "GRID_NOT_CONFIGURED"
    message = "The active game does not describe a reel grid"


class GridSplitFailedError(AppException):
    status_code = HTTPStatus.BAD_GATEWAY
    error_code = "GRID_SPLIT_FAILED"
    message = "The reel grid could not be split out of the frame"
