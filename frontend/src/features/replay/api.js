/**
 * Replay client. The sequence itself runs entirely in the backend — it drives
 * three windows on the host machine — so there is nothing here but the trigger,
 * the record of what it is doing, and the address of the picture it took.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const REPLAY_URL = `${routes.API}/replay`;

/**
 * Fetch what a replay would find if it ran now, **and the run in progress**.
 *
 * This is the progress endpoint as well as the readiness one: `run` is the
 * live record, filling in step by step, with the screenshot on it from the
 * moment it is taken rather than when the sequence ends.
 *
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<{running: boolean, game: string, windows: Array<{window:
 *   "devtool"|"system-admin"|"game", state: "ready"|"not_found"|"access_denied"|
 *   "unsupported", title: string, hwnd: number|null, client_width: number,
 *   client_height: number, controls: string[]}>, menu: {reachable: boolean,
 *   probed: boolean, cdp_url: string, page_url: string|null, page_title:
 *   string|null, labels: string[], label_count: number, error: string|null},
 *   run: ReplayRun|null, exits_after_screenshot: boolean, game_exit_target:
 *   string, game_exit_configured: boolean, game_spin_target: string,
 *   game_spin_configured: boolean}>}
 */
export function getReplayStatus({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${REPLAY_URL}/status`, signal });
}

/**
 * @typedef {object} ReplayRun
 * @property {string} run_id
 * @property {string} game
 * @property {"running"|"completed"|"failed"} state
 * @property {string} message
 * @property {Array<{key: string, label: string, window: string, state:
 *   "pending"|"running"|"completed"|"skipped"|"failed", target: string|null,
 *   screen_x: number|null, screen_y: number|null, confirmed: boolean, detail:
 *   string|null, duration_ms: number|null, error: string|null, error_code:
 *   string|null}>} steps
 * @property {Array<{sequence: number, at: string, level: "info"|"warning"|"error",
 *   step: string|null, message: string}>} logs
 * @property {Array<{moment: "before-spin"|"after-spin", source_name: string,
 *   file_name: string, file_path: string|null, attempts: number,
 *   blank: boolean}>} screenshots
 * @property {string} started_at
 * @property {string|null} finished_at
 * @property {number} duration_ms
 * @property {string|null} error
 * @property {string|null} error_code
 */

/**
 * Start replaying the latest game play: DevTool, the attendant menu and the
 * game's own window, ending with a screenshot of the replay and the cabinet
 * put back where it was found.
 *
 * Resolves **as soon as the sequence has started**, with every step listed and
 * pending — it takes tens of seconds, most of them waiting on the menu's
 * server. Follow it on the status query. A step that fails never rejects: it
 * lands on that record. Only a refusal to start at all (a run already in
 * progress, or a host with no Windows API) rejects with an `ApiError`.
 *
 * @returns {Promise<ReplayRun>}
 */
export function runReplay() {
  return apiRequest({ method: "POST", url: `${REPLAY_URL}/run` });
}

/**
 * URL of the picture a run took of the replay, for an `<img>` src.
 *
 * A file rather than a data URI on the record, because that record is polled
 * while the run walks on — so this is built here, not fetched.
 */
export function replayImageUrl(fileName) {
  return `${REPLAY_URL}/screenshot/${encodeURIComponent(fileName)}`;
}
