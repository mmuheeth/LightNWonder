/**
 * GAF automation client. The browser drives the game by asking the backend to
 * call the game's own methods — which game, where its automation service
 * listens and what its controls are called all come from the active game's
 * config, so none of these calls carries a target.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const GAF_URL = `${routes.API}/gaf`;

/**
 * Fetch the automation status. Resolves even with nothing running — check `state`
 * rather than catching. Reading it never opens a session.
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<{state:
 *   "not_configured"|"unreachable"|"files_missing"|"disconnected"|"ready", game: string,
 *   server_url: string, host: string, port: number, game_type: string, gdk_version:
 *   string, connected: boolean, query_files: Array<{path: string, group: string,
 *   present: boolean}>, object_count: number|null, idle_state: string|null, detail:
 *   string}>}
 */
export function getGafStatus({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${GAF_URL}/status`, signal });
}

/** Open a session now, so the cold start is not charged to the first spin. */
export function connectGaf() {
  return apiRequest({ method: "POST", url: `${GAF_URL}/connect` });
}

/** Close the session. One left open blocks the next client of the game. */
export function disconnectGaf() {
  return apiRequest({ method: "POST", url: `${GAF_URL}/disconnect` });
}

/**
 * Spin the reels and wait for the spin to finish. Opens a session if there is none.
 * An `outcome` of `win_offered` is a finished spin with money on the table. A game
 * that is *already* held that way rejects with `GAF_NOT_IDLE` unless `force` is set.
 * @param {{settle?: boolean, force?: boolean, timeout_seconds?: number,
 *   read_meters?: boolean}} [payload]
 * @returns {Promise<{game: string, pressed: boolean, outcome:
 *   "idle"|"win_offered"|"timeout", settled: boolean, idle_state: string|null,
 *   game_state: string|null, win_offered: boolean, meters: {credit: string|null, bet:
 *   string|null, win: string|null}|null, elapsed_ms: number, detail: string}>}
 */
export function spinGaf(payload = {}) {
  return apiRequest({ method: "POST", url: `${GAF_URL}/spin`, data: payload });
}

/**
 * Collect a win. Nothing to collect resolves with `pressed: false` rather than
 * rejecting — a real state, and a different fact from a press that failed.
 * @param {{force?: boolean, settle?: boolean, timeout_seconds?: number,
 *   read_meters?: boolean}} [payload]
 * @returns {Promise<{game: string, button: string, interactable: boolean, pressed:
 *   boolean, settled: boolean, idle_state: string|null, meters: {credit: string|null,
 *   bet: string|null, win: string|null}|null, elapsed_ms: number, detail: string}>}
 */
export function takeWinGaf(payload = {}) {
  return apiRequest({ method: "POST", url: `${GAF_URL}/take-win`, data: payload });
}
