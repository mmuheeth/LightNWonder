/**
 * Event Based Capture client.
 *
 * Tracking runs in the backend, not the browser: it follows the game's log and
 * drives OBS, neither of which the page can reach. So there is no local timer or
 * accumulated state here — the card starts a run, stops it, and polls status.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const CAPTURE_URL = `${routes.API}/event-capture`;

/**
 * Fetch the state of the run in progress.
 *
 * Resolves whether or not tracking is on — check `active` rather than catching.
 *
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<{active: boolean, run_id: string|null, game: string|null,
 *   started_at: string|null, duration_ms: number, event_count: number,
 *   recent_events: Array<object>, errors: string[]}>}
 */
export function getCaptureStatus({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${CAPTURE_URL}/status`, signal });
}

/**
 * Start tracking the active game's log.
 *
 * Connects to OBS first, so this rejects with `OBS_CONNECTION_FAILED` when OBS
 * is closed and `EVENT_CAPTURE_LOG_UNAVAILABLE` when the game is not running.
 */
export function startCapture() {
  return apiRequest({ method: "POST", url: `${CAPTURE_URL}/start` });
}

/**
 * Stop tracking. The resolved value is the finished record, events included.
 *
 * @returns {Promise<{run_id: string, game: string, status: string,
 *   started_at: string, stopped_at: string|null, event_count: number,
 *   log_path: string, events: Array<object>, errors: string[]}>}
 */
export function stopCapture() {
  return apiRequest({ method: "POST", url: `${CAPTURE_URL}/stop` });
}

/** Every run on disk, newest first. */
export function listCaptureRuns({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${CAPTURE_URL}/runs`, signal });
}

/** One run and all of its events. */
export function getCaptureRun(runId, { signal } = {}) {
  return apiRequest({
    method: "GET",
    url: `${CAPTURE_URL}/runs/${encodeURIComponent(runId)}`,
    signal,
  });
}

/**
 * URL of one captured screenshot, for an `<img>` src.
 *
 * The only backend route that returns a file rather than the envelope, so it is
 * built here rather than fetched through `apiRequest`. It stays under `/api` so
 * the Vite dev proxy forwards it like everything else.
 */
export function captureImageUrl(runId, fileName) {
  return `${CAPTURE_URL}/runs/${encodeURIComponent(runId)}/screenshots/${encodeURIComponent(fileName)}`;
}
