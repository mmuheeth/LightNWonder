/**
 * ROI extraction client.
 *
 * Which regions exist is the backend's answer, read out of the active game's
 * config -- the browser never names a rectangle, only picks one of the names it
 * was given. These call our own `/api/roi/*` endpoints, which return the
 * standard envelope.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const ROI_URL = `${routes.API}/roi`;

/**
 * Fetch the active game's regions and the frame they would be cut out of.
 *
 * `latest_frame` is null until a screenshot has been taken, which is the empty
 * state the panel renders rather than an error.
 *
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<{game: string, regions: Array<{region: string, label: string,
 *   roi: number[], error: string|null}>,
 *   latest_frame: {file_name: string, captured_at: string, width: number,
 *     height: number}|null}>}
 */
export function getRoiRegions({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${ROI_URL}/regions`, signal });
}

/**
 * Extract one region and get the crop back as a data URI.
 *
 * Omit `file_name` to use the newest screenshot, which is the usual case.
 *
 * `meter` carries the numbers read off the crop and is populated for the cash
 * meter region only -- null for every other region. It has its own `error`
 * rather than failing the request, because a reading that could not be taken
 * still leaves a crop worth looking at.
 *
 * @param {{region: string, file_name?: string}} payload
 * @returns {Promise<{game: string, region: string, roi: number[],
 *   source: {file_name: string, captured_at: string, width: number, height: number},
 *   box: number[], width: number, height: number, image_data: string,
 *   meter: {mode: string, currency: string|null, cash: number|null,
 *     credits: number|null, win: number|null, bet: number|null,
 *     fields: Record<string, {value: number|null, text: string, confidence: number,
 *       box: number[]}>,
 *     unmapped: Array<{value: number, centre: number, confidence: number, text: string}>,
 *     band: number[], engine_calls: number, duration_ms: number,
 *     error: string|null}|null}>}
 */
export function extractRoi(payload) {
  return apiRequest({
    method: "POST",
    url: `${ROI_URL}/extract`,
    data: payload,
  });
}
