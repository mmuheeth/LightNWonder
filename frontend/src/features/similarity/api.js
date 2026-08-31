/**
 * Similarity check client. One source picture scored against every image in a
 * folder; both paths live on the backend's machine, so blank fields defer to
 * the defaults configured there rather than to anything the browser knows.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const SIMILARITY_URL = `${routes.API}/similarity`;

/**
 * Score the source image against every image under the folder (searched
 * recursively), one row per candidate, best score first. Every picture is
 * prepared before it is scored: transparency composited onto black, the
 * transparent margin cropped (`trimmed`), a size mismatch scaled to fill with
 * the overflow centre-cropped, never stretched or padded (`resized`); one
 * that cannot be read comes back with `error` set
 * instead of failing the run. Each row carries the candidate *as compared*
 * (after the preparation) as a data URI, beside `source.image_data` — pass
 * `include_images: false` to strip them when only the numbers are wanted.
 *
 * @param {{source?: string, directory?: string, threshold?: number,
 *   include_images?: boolean}} payload
 *   Omit `source`/`directory` for the backend's configured defaults; omit
 *   `threshold` for PAYLINE_MATCH_THRESHOLD.
 * @returns {Promise<{source: {file_name: string, width: number, height: number,
 *     image_data: string|null},
 *   directory: string, threshold: number, summary: string,
 *   results: Array<{file_name: string, score: number|null, matched: boolean,
 *     resized: boolean, trimmed: boolean, width: number|null, height: number|null,
 *     error: string|null, image_data: string|null}>,
 *   stats: {candidates: number, compared: number, errors: number,
 *     resized: number, matches: number, score_min: number|null,
 *     score_max: number|null, matched_min: number|null,
 *     rejected_max: number|null}}>}
 */
export function compareSimilarity(payload) {
  return apiRequest({
    method: "POST",
    url: `${SIMILARITY_URL}/compare`,
    data: payload,
    // A big reference library is ~90 files opened and vectorised in one
    // request, which can outlast the 15s default.
    timeout: 60_000,
  });
}
