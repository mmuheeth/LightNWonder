/**
 * Payline check client.
 *
 * The patterns are the backend's answer, read out of the active game's
 * `paylines` block, and the tiles are a split the Reel grid panel already wrote
 * — the browser never names a line, a position or a picture, only which split,
 * which set of lines and how strict to be. These call our own
 * `/api/paylines/*` endpoints, which return the standard envelope.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const PAYLINES_URL = `${routes.API}/paylines`;

/**
 * Fetch the bet configurations the active game declares and the split to check.
 *
 * A game with no `paylines` block, a game with no reel grid, and a checkout that
 * has split nothing all resolve with `error` set rather than rejecting: each is
 * a state the panel renders. `latest_split` is null until the Reel grid panel
 * has written one.
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
 * Check one set of paylines against the tiles of one split.
 *
 * Omit `split` to use the newest one, which is the usual case. Each line is read
 * from the left one adjacent pair at a time and stops at the first pair whose
 * tiles are not the same symbol, so `pays` is the length of the leading run — 0
 * when the first two reels differ, otherwise 2 or more.
 *
 * `threshold` is the number worth tuning: cosine similarity does not start at
 * zero for unrelated pictures, so the default calls most pairs a match.
 * `stats.matched_min` and `stats.rejected_max` in the result are the two numbers
 * a working cut sits between.
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
