/**
 * Analyze Spin client. The whole sequence lives in the backend — the browser
 * asks it to start, watches it happen, and reads what it made of the result.
 *
 * Two shapes of the same payload: `getSpinStatus` without images is what a poll
 * or the stream carries, and with images is the report — the meter crops, the
 * annotated reels and the paying lines' own pictures. They are separate asks
 * because the pictures only exist once a run has finished.
 */

import { env, routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const ANALYZE_SPIN_URL = `${routes.API}/analyze-spin`;
const STREAM_PATH = `${ANALYZE_SPIN_URL}/stream`;

/**
 * Absolute `ws://`/`wss://` URL of the progress stream.
 *
 * Built from the API base rather than hardcoded so it follows the same
 * deployment: empty in development, where the request is relative and the Vite
 * proxy (with `ws: true`) forwards the upgrade.
 *
 * @returns {string}
 */
export function spinStreamUrl() {
  const url = new URL(
    env.apiBaseUrl ? `${env.apiBaseUrl}${STREAM_PATH}` : STREAM_PATH,
    globalThis.location.href,
  );
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

/**
 * URL of one screenshot a run took, for an `<img>` src.
 *
 * The one fetch here that bypasses `apiRequest`: an image cannot unwrap the
 * response envelope, so the backend serves this route raw — the same exception
 * a capture run's screenshots make.
 *
 * @param {string} fileName one of `run.frames[].file_name`
 * @returns {string}
 */
export function spinFrameUrl(fileName) {
  return `${env.apiBaseUrl}${ANALYZE_SPIN_URL}/frames/${encodeURIComponent(fileName)}`;
}

/**
 * Fetch the run in progress, or the last one that finished.
 *
 * Always resolves — `active` is what to branch on, and `run` outlives its own
 * run so a reload after a spin still shows the report. `run` is null only
 * before the first spin of the process.
 *
 * @param {{includeImages?: boolean, signal?: AbortSignal}} [options]
 * @returns {Promise<{active: boolean, run: SpinRun|null}>}
 */
export function getSpinStatus({ includeImages = false, signal } = {}) {
  return apiRequest({
    method: "GET",
    url: `${ANALYZE_SPIN_URL}/status`,
    params: includeImages ? { include_images: true } : undefined,
    signal,
  });
}

/**
 * Spin once and validate it. Returns as soon as the run is under way, not when
 * it ends — 409 when one is already going, or when the active game declares no
 * log to follow a spin through.
 *
 * @param {{record?: boolean}} [options] `record` also makes a video of the
 *   spin with OBS; off by default.
 * @returns {Promise<{active: boolean, run: SpinRun|null}>}
 */
export function startSpin({ record = false } = {}) {
  return apiRequest({
    method: "POST",
    url: `${ANALYZE_SPIN_URL}/start`,
    params: { record },
  });
}

/**
 * Ask the run in progress to stop. Cooperative, so this resolves before the run
 * has actually ended — watch `run.state` for `cancelled`.
 *
 * @returns {Promise<{active: boolean, run: SpinRun|null}>}
 */
export function cancelSpin() {
  return apiRequest({ method: "POST", url: `${ANALYZE_SPIN_URL}/cancel` });
}

/**
 * One orchestrated spin and the two validations over it.
 *
 * @typedef {object} SpinRun
 * @property {string} run_id
 * @property {string} game
 * @property {string} label
 * @property {"running"|"completed"|"failed"|"cancelled"} state
 * @property {"win"|"no-win"|"unknown"} outcome
 * @property {string} message
 * @property {string} started_at
 * @property {string|null} finished_at
 * @property {number} duration_ms
 * @property {Array<{key: string, label: string,
 *   state: "pending"|"running"|"completed"|"skipped"|"failed",
 *   detail: string|null, started_at: string|null, finished_at: string|null,
 *   duration_ms: number|null, error: string|null,
 *   error_code: string|null}>} steps
 * @property {Array<{key: string, label: string, file_name: string,
 *   at: string, blank: boolean, attempts: number}>} frames
 * @property {Array<{event: string, summary: string, at: string|null,
 *   log_line: string}>} events
 * @property {{output_path: string|null, duration_ms: number}|null} recording
 * @property {{readings: Array<{frame: string, label: string, file_name: string,
 *     balance: number|null, win: number|null, bet: number|null,
 *     values: object|null, error: string|null, crop_image: string|null}>,
 *   checks: Array<{key: string, label: string,
 *     verdict: "passed"|"failed"|"indeterminate", expected: number|null,
 *     actual: number|null, difference: number|null, detail: string}>,
 *   tolerance: number, verdict: "passed"|"failed"|"indeterminate",
 *   error: string|null}|null} meter
 * @property {{frame: string, split: string|null, paytable_id: string,
 *   paytable_origin: string, payline_set_id: string|null,
 *   resolved_from: string, line_count: number|null, threshold: number,
 *   summary: string, pay_lengths: number[],
 *   lines: Array<{line: string, label: string, positions: string[],
 *     elements: number[][], pays: number, paying: boolean, awarded: boolean,
 *     color: string, break_position: string|null,
 *     steps: Array<{left: string, right: string, similarity: number,
 *       matched: boolean, counted: boolean}>,
 *     candidates: Array<{codes: string[], names: Array<string|null>,
 *       value: number}>,
 *     symbols: Array<string|null>, symbol: string|null,
 *     symbol_name: string|null, run_from_stops: number,
 *     agrees: boolean|null, combo_id: number|null, combo_symbols: string[],
 *     credits: number|null, min_pay_length: number|null,
 *     value_min: number|null, value_max: number|null, exact: boolean,
 *     note: string|null, image_data: string|null}>,
 *   runs_found: number, awarded_lines: number,
 *   stats: {lines: number, paying: number, comparisons: number,
 *     matches: number, best_line: string|null, best_pays: number,
 *     score_min: number|null, score_max: number|null,
 *     matched_min: number|null, rejected_max: number|null}|null,
 *   stops: number[], stops_log_line: string|null, stop_anchor: string|null,
 *   stop_anchor_decided: boolean, stop_agreed: number|null,
 *   stop_compared: number|null, symbol_grid: string[][],
 *   stops_error: string|null,
 *   expected: {paying_lines: number, credits_min: number, credits_max: number,
 *     exact: boolean, line_count: number|null, denomination: number|null,
 *     total_bet: number|null, bet_credits: number|null,
 *     credits_per_line: number|null, cash_min: number|null,
 *     cash_max: number|null, observed_win: number|null,
 *     verdict: "passed"|"failed"|"indeterminate", detail: string}|null,
 *   output_dir: string|null, output_file: string|null,
 *   overlay_image: string|null, error: string|null}|null} paylines
 * @property {string[]} errors
 */
