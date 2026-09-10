/**
 * Cyclic Messages client. Tracking runs in the backend (it follows the game's
 * log, drives OBS and records a clip per win), so this only starts/stops a run
 * and reads what it produced.
 *
 * Same shape as `features/event-capture/api.js`, with one route fewer: a run
 * directory holds screenshots *and* the clips, so both come off one file route
 * rather than an images route and a video one.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const CYCLIC_URL = `${routes.API}/cyclic-messages`;

/**
 * Fetch the state of the run in progress. Resolves either way — check `active`
 * rather than catching.
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<{active: boolean, run_id: string|null, game: string|null,
 *   started_at: string|null, duration_ms: number, message_count: number,
 *   sampled_count: number, cycle_count: number, recording: boolean,
 *   video_count: number, sampling: boolean, recent_events: Array<object>,
 *   errors: string[]}>}
 */
export function getCyclicStatus({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${CYCLIC_URL}/status`, signal });
}

/**
 * Start tracking the active game's cyclic messages. Connects to OBS first, so
 * this rejects with `OBS_CONNECTION_FAILED` or
 * `CYCLIC_MESSAGES_LOG_UNAVAILABLE`.
 */
export function startCyclic() {
  return apiRequest({ method: "POST", url: `${CYCLIC_URL}/start` });
}

/**
 * Stop tracking. The resolved value is the finished record, events and clips
 * included.
 */
export function stopCyclic() {
  return apiRequest({ method: "POST", url: `${CYCLIC_URL}/stop` });
}

/** Every run on disk, newest first. */
export function listCyclicRuns({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${CYCLIC_URL}/runs`, signal });
}

/** One run and all of its events. */
export function getCyclicRun(runId, { signal } = {}) {
  return apiRequest({
    method: "GET",
    url: `${CYCLIC_URL}/runs/${encodeURIComponent(runId)}`,
    signal,
  });
}

/**
 * How long to allow the clip reading, overriding the client's global
 * `VITE_API_TIMEOUT_MS` (15s) for this one call.
 *
 * It is the only request in the app that is *supposed* to take a minute: a 90s
 * clip is ~180 frames and as many Tesseract subprocesses, measured at 46s with
 * six running at once. Four minutes is ~5x that headroom, and deliberately
 * *under* the 300s `requestTimeout` Node gives the Vite dev proxy — past that
 * the proxy cuts the connection first and the reason arrives as a bare network
 * error instead of this timeout. A clip long enough to need more than this
 * wants a bigger `interval_seconds`, not a bigger number here.
 */
const READ_TIMEOUT_MS = 240_000;

/**
 * Read the messages out of one win's clip: the backend cuts it into frames,
 * crops the caption out of each and OCRs them.
 *
 * Slow on purpose — see `READ_TIMEOUT_MS`. That is why it is a button rather
 * than something the run view fetches on mount.
 *
 * @param {string} runId
 * @param {{cycle?: number, intervalSeconds?: number, signal?: AbortSignal}} [options]
 */
export function readCyclicText(runId, { cycle, intervalSeconds, signal } = {}) {
  const params = new URLSearchParams();
  if (cycle != null) params.set("cycle", String(cycle));
  if (intervalSeconds != null) params.set("interval_seconds", String(intervalSeconds));
  const query = params.toString();
  return apiRequest({
    method: "GET",
    url: `${CYCLIC_URL}/runs/${encodeURIComponent(runId)}/text${query ? `?${query}` : ""}`,
    signal,
    timeout: READ_TIMEOUT_MS,
  });
}

/**
 * URL of one file from a run — a screenshot for an `<img>` src or one win's
 * clip for a `<video>` src. The backend serves both from one route because a
 * run directory holds both, and neither element can unwrap the JSON envelope.
 */
export function cyclicFileUrl(runId, fileName) {
  return `${CYCLIC_URL}/runs/${encodeURIComponent(runId)}/files/${encodeURIComponent(fileName)}`;
}
