/** Runtime game catalog and selection client. */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const GAMES_URL = `${routes.API}/games`;

/**
 * Fetch available game configs and the current selection.
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<{active_game: string, games: Array<{game: string, label: string,
 *   process: string|null}>}>}
 */
export function getGameCatalog({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${GAMES_URL}/`, signal });
}

/**
 * Change the active game for this backend process.
 * @param {string} game
 * @returns {Promise<{game: string, label: string, process: string|null,
 *   obs_window_selected: boolean|null}>}
 */
export function selectGame(game) {
  return apiRequest({
    method: "PUT",
    url: `${GAMES_URL}/active`,
    data: { game },
  });
}
