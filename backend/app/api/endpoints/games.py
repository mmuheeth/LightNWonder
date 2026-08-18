"""Game catalog and runtime-selection endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.logging import get_logger
from app.exceptions.base import AppException, ObsNotConnectedError
from app.schemas.games import ActiveGame, GameCatalog, SelectGameRequest
from app.schemas.response import ApiResponse
from app.services import games as games_service
from app.services import obs as obs_service

logger = get_logger("games-api")

router = APIRouter()


@router.get(
    "/",
    response_model=ApiResponse[GameCatalog],
    summary="List available games",
)
async def list_games() -> ApiResponse[GameCatalog]:
    """Return the game configs available to the dashboard selector."""
    data = games_service.catalog()
    return ApiResponse[GameCatalog].ok(
        data=data, message=f"{len(data.games)} game configs available"
    )


@router.put(
    "/active",
    response_model=ApiResponse[ActiveGame],
    summary="Select the active game",
)
async def select_active_game(
    payload: SelectGameRequest,
) -> ApiResponse[ActiveGame]:
    """Switch the active game and retarget OBS when it is connected."""
    data = games_service.select(payload.game)
    obs_selected: bool | None = None

    try:
        await obs_service.select_current_game_window()
    except ObsNotConnectedError:
        # The next OBS connection will select the new game's process.
        pass
    except AppException as exc:
        # A game switch remains valid even when OBS has no usable source yet;
        # the OBS panel exposes the retry action once the scene is ready.
        obs_selected = False
        logger.warning(
            "Game changed but OBS could not retarget its window: %s", exc.message
        )
    else:
        obs_selected = True

    data = data.model_copy(update={"obs_window_selected": obs_selected})
    if obs_selected is False:
        message = "Game selected; OBS window source needs attention"
    elif obs_selected is None:
        message = "Game selected; OBS will retarget when it connects"
    else:
        message = "Game selected and OBS window source updated"
    return ApiResponse[ActiveGame].ok(data=data, message=message)
