/**
 * Reel grid client.
 *
 * The shape of the grid is the backend's answer, read out of the active game's
 * `roi.reels` region and its `reel_bounds` block — the browser never names a
 * rectangle or a tile, only which screenshot to split. These call our own
 * `/api/grid/*` endpoints, which return the standard envelope.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const GRID_URL = `${routes.API}/grid`;

/**
 * Fetch the shape of the active game's reel grid and the frame it would use.
 *
 * A game that describes no grid resolves with `error` set rather than
 * rejecting: only some games have reels, and selecting one of the others is a
 * state the panel renders. `latest_frame` is null until a screenshot has been
 * taken, which is the panel's other empty state.
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
 * Split the reels of one frame into tiles.
 *
 * Omit `file_name` to use the newest screenshot, which is the usual case. The
 * crop and every tile are written under `obs-captured-files/grid/<frame stem>/`
 * whatever `include_images` says; it only governs whether they also come back
 * inline for the page to draw.
 *
 * `inset` overrides the game config's border trim for this one split, which is
 * how the right number gets found before it is written into the config. One
 * number trims every tile edge; two are [horizontal, vertical]; four are
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
