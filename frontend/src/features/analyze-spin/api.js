/**
 * Analyze Spin client. The whole sequence lives in the backend — the browser asks it to
 * start, watches it happen, and reads what it made of the result.
 */

import { env, routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const ANALYZE_SPIN_URL = `${routes.API}/analyze-spin`;
const STREAM_PATH = `${ANALYZE_SPIN_URL}/stream`;

/**
 * Absolute `ws://`/`wss://` URL of the progress stream.
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
 * @param {string} fileName one of `run.frames[].file_name`
 * @returns {string}
 */
export function spinFrameUrl(fileName) {
  return `${env.apiBaseUrl}${ANALYZE_SPIN_URL}/frames/${encodeURIComponent(fileName)}`;
}

/**
 * URL of one reel position's clip, for a `<video>` src.
 * @param {string} runId `run.run_id`
 * @param {string} fileName one of `run.tile_clips.clips[].file_name`
 * @returns {string}
 */
export function spinClipUrl(runId, fileName) {
  return `${env.apiBaseUrl}${ANALYZE_SPIN_URL}/runs/${encodeURIComponent(runId)}/clips/${encodeURIComponent(fileName)}`;
}

/**
 * Fetch the run in progress, or the last one that finished.
 * @param {{includeImages?: boolean, signal?: AbortSignal}} [options]
 * @returns {Promise<{active: boolean, run: object|null}>}
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
 * Spin once and validate it.
 * @param {{record?: boolean, architecture?: string}} [options] `record` also makes a
 *   video of the spin with OBS; off by default. `architecture` picks which trained
 *   network names the tiles of the reels — omit it to let the backend use its own
 *   default.
 * @returns {Promise<{active: boolean, run: object|null}>}
 */
export function startSpin({ record = false, architecture } = {}) {
  return apiRequest({
    method: "POST",
    url: `${ANALYZE_SPIN_URL}/start`,
    params: architecture ? { record, architecture } : { record },
  });
}

/**
 * Ask the run in progress to stop. Cooperative, so this resolves before the run has
 * actually ended — watch `run.state` for `cancelled`.
 * @returns {Promise<{active: boolean, run: object|null}>}
 */
export function cancelSpin() {
  return apiRequest({ method: "POST", url: `${ANALYZE_SPIN_URL}/cancel` });
}
