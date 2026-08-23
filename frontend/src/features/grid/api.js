/**
 * Reel grid client. The grid shape is the backend's answer, read out of the
 * active game's `roi.reels`/`reel_bounds` — the browser only names a screenshot.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const GRID_URL = `${routes.API}/grid`;

/**
 * Fetch the shape of the active game's reel grid and the frame it would use.
 * A game with no grid resolves with `error` set rather than rejecting.
 *
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<{game: string, region: string, roi: number[]|null,
 *   rows: number, columns: number, positions: string[][], inset: number[],
 *   latest_frame: {file_name: string, captured_at: string, width: number,
 *     height: number}|null, error: string|null}>}
 */
export function getGridLayout({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${GRID_URL}/layout`, signal });
}

/**
 * Split the reels of one frame into tiles. Omit `file_name` for the newest
 * screenshot. `inset` overrides the config's border trim for this one split —
 * one number trims every edge, two are [horizontal, vertical], four are
 * [left, top, right, bottom].
 *
 * @param {{file_name?: string, include_images?: boolean,
 *   inset?: number|number[]}} [payload]
 * @returns {Promise<{game: string, region: string, roi: number[],
 *   source: {file_name: string, captured_at: string, width: number, height: number},
 *   box: number[], width: number, height: number, rows: number, columns: number,
 *   inset: number[], tile_width: number, tile_height: number,
 *   output_dir: string, crop_file: string,
 *   crop_image: string|null, positions: string[][],
 *   tiles: Array<{row: number, column: number, name: string, file_name: string,
 *     roi: number[], box: number[], width: number, height: number,
 *     image_data: string|null}>}>}
 */
export function splitGrid(payload = {}) {
  return apiRequest({
    method: "POST",
    url: `${GRID_URL}/split`,
    data: payload,
  });
}
