/**
 * OBS Studio client. The browser never talks to OBS directly — host, port and
 * password live in the backend's environment, so nothing sensitive reaches the bundle.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const OBS_URL = `${routes.API}/obs`;

/**
 * Fetch the OBS connection status.
 *
 * Resolves even while OBS is closed — check `state` rather than catching.
 *
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<{state: "connected"|"disconnected", url: string,
 *   obs_version: string|null, obs_websocket_version: string|null,
 *   platform: string|null, current_scene: string|null,
 *   recording: {active: boolean, paused: boolean, timecode: string|null,
 *     duration_ms: number, bytes_written: number, output_path: string|null}|null}>}
 */
export function getObsStatus({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${OBS_URL}/status`, signal });
}

/** Open a session with OBS. Idempotent: a live session is reused. */
export function connectObs() {
  return apiRequest({ method: "POST", url: `${OBS_URL}/connect` });
}

/** Close the OBS session. Idempotent, and never fails. */
export function disconnectObs() {
  return apiRequest({ method: "POST", url: `${OBS_URL}/disconnect` });
}

/**
 * Point the active OBS window-capture source at the process in the active game config.
 *
 * Fails with 502 when OBS lists no window for that process, which is what a game
 * that is not running looks like.
 *
 * @returns {Promise<{game: string, process: string, scene: string,
 *   source_name: string, window_title: string|null}>}
 */
export function selectGameWindow() {
  return apiRequest({ method: "POST", url: `${OBS_URL}/select-game-window` });
}

/**
 * Capture a screenshot.
 *
 * Always returns `image_data` as a base64 data URI, ready for an `<img>` src.
 * Pass a bare filename to also have OBS write it into the configured
 * screenshot directory and return `file_path` alongside it. `output_dir` can
 * select a relative use-case subdirectory below that root.
 *
 * @param {{source_name?: string, image_format?: string, width?: number,
 *   height?: number, quality?: number, file_name?: string,
 *   output_dir?: string}} [payload]
 * @returns {Promise<{source_name: string, image_format: string,
 *   image_data: string|null, file_path: string|null}>}
 */
export function takeScreenshot(payload = {}) {
  return apiRequest({
    method: "POST",
    url: `${OBS_URL}/screenshot`,
    data: payload,
  });
}

/** Fetch the recording status on its own. */
export function getRecordStatus({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${OBS_URL}/recording`, signal });
}

/**
 * Start recording. Resolves once OBS reports the output actually running.
 *
 * @param {{output_dir?: string}} [payload] relative use-case subdirectory
 */
export function startRecording(payload) {
  return apiRequest({
    method: "POST",
    url: `${OBS_URL}/recording/start`,
    data: payload,
  });
}

/** Stop recording. The resolved value carries `output_path` when OBS reports it. */
export function stopRecording() {
  return apiRequest({ method: "POST", url: `${OBS_URL}/recording/stop` });
}

/** Pause the running recording. Not every OBS recording format can pause. */
export function pauseRecording() {
  return apiRequest({ method: "POST", url: `${OBS_URL}/recording/pause` });
}

/** Resume a paused recording. */
export function resumeRecording() {
  return apiRequest({ method: "POST", url: `${OBS_URL}/recording/resume` });
}
