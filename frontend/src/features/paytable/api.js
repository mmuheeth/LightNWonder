/**
 * Loaded-paytable client. Which paytable is loaded is the backend's answer —
 * it reads the game's own log for it — and the browser only ever asks for a
 * different one by an id the backend already listed in `available`.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const PAYTABLE_URL = `${routes.API}/paytable`;

/**
 * Fetch the maths of the paytable the active game has loaded.
 *
 * Omit `paytableId` for the one the game's log last named; pass one of
 * `available` to inspect another without running the game on it.
 *
 * @param {{paytableId?: string|null, signal?: AbortSignal}} [options]
 * @returns {Promise<{game: string, label: string, paytable_id: string,
 *   directory: string, available: string[],
 *   source: {origin: string, log_path: string|null, log_line: string|null,
 *     logged_at: string|null, denomination: string|null},
 *   identity: {path: string, game_type: string|null, game_id: string|null,
 *     display_game_id: string|null, game_pct: number|null,
 *     min_game_pct: number|null, game_base_pct: number|null,
 *     min_game_base_pct: number|null, number_of_lines: number|null,
 *     min_total_bet: number|null, max_bets: number[],
 *     denominations: number[]}|null,
 *   math: {path: string, game_id: string|null, game_pct: number|null,
 *     min_game_pct: number|null, game_base_pct: number|null,
 *     min_game_base_pct: number|null,
 *     defaults: {symbol_set_id: string|null, reel_strip_set_id: string|null,
 *       paytable_id: string|null, payline_set_id: string|null,
 *       initial_stops: number[]},
 *     symbols: Array<{code: string, name: string|null, role: string,
 *       substitutes: string[], reel_stops: number}>,
 *     reel_strip_sets: Array<{identifier: string, strip_ids: string[],
 *       visible_heights: number[], is_default: boolean}>,
 *     reel_strips: Array<{identifier: string, set_id: string|null,
 *       reel_index: number|null, symbol_set_id: string|null, length: number,
 *       symbols: string[], weights: number[], truncated: boolean}>,
 *     payline_combos: Array<{combo_id: number|null, combo_set: string,
 *       group: number|null, value: number|null, symbols: string[],
 *       names: Array<string|null>, match_length: number}>,
 *     scatter_combos: Array<{combo_id: number|null, combo_set: string,
 *       group: number|null, value: number|null, symbols: string[],
 *       names: Array<string|null>, min_symbols: number|null,
 *       max_symbols: number|null, base_multiplier: string|null,
 *       bonus_code: number|null}>,
 *     paytables: Array<{identifier: string, combo_set_ids: string[]}>},
 *   win_geometry: {path: string, payline_set_id: string|null,
 *     resolved_from: string, line_count: number|null,
 *     sets: Array<{payline_set_id: string, line_count: number,
 *       is_applicable: boolean}>,
 *     paylines: Array<{line: number, number: number, elements: number[][],
 *       grid: number[][]}>,
 *     error: string|null}}>}
 */
export function getPaytable({ paytableId, signal } = {}) {
  return apiRequest({
    method: "GET",
    url: `${PAYTABLE_URL}/`,
    params: paytableId ? { paytable_id: paytableId } : undefined,
    signal,
  });
}
