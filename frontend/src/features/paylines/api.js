/**
 * Payline check client. Lines and tiles are the backend's answer — the browser
 * only picks which split, which set, and how strict to be.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const PAYLINES_URL = `${routes.API}/paylines`;

/**
 * Fetch the bet configurations the active game declares and the split to check.
 * An unconfigured game or nothing split yet resolves with `error` set, not a
 * rejection — `latest_split` is null until the Reel grid panel writes one.
 *
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<{game: string, sets: Array<{name: string, label: string}>,
 *   default_set: string|null, threshold: number, rows: number, columns: number,
 *   latest_split: {split: string, written_at: string, rows: number,
 *     columns: number, tile_width: number, tile_height: number, width: number,
 *     height: number}|null,
 *   error: string|null}>}
 */
export function getPaylineLayout({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${PAYLINES_URL}/layout`, signal });
}

/**
 * Check one set of paylines against the tiles of one split (omit `split` for
 * the newest). `pays` is the length of the leading run of matching adjacent
 * tiles, 0 or 2+. `threshold` is worth tuning — cosine similarity doesn't start
 * at zero for unrelated pictures — against `stats.matched_min`/`rejected_max`.
 *
 * @param {{split?: string, set?: string, threshold?: number,
 *   include_images?: boolean}} [payload]
 * @returns {Promise<{game: string, set: string, threshold: number,
 *   source: {split: string, written_at: string, rows: number, columns: number,
 *     tile_width: number, tile_height: number, width: number, height: number},
 *   summary: string,
 *   lines: Array<{name: string, label: string, positions: string[],
 *     pays: number, paying: boolean, matched_positions: string[], color: string,
 *     steps: Array<{left: string, right: string, similarity: number,
 *       matched: boolean, counted: boolean}>,
 *     break_position: string|null, image_data: string|null}>,
 *   stats: {lines: number, paying: number, comparisons: number, matches: number,
 *     best_line: string|null, best_pays: number, score_min: number|null,
 *     score_max: number|null, matched_min: number|null,
 *     rejected_max: number|null},
 *   output_dir: string, output_file: string, overlay_image: string|null}>}
 */
export function checkPaylines(payload = {}) {
  return apiRequest({
    method: "POST",
    url: `${PAYLINES_URL}/check`,
    data: payload,
  });
}
