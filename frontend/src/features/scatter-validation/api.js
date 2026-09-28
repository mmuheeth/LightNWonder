/**
 * Scatter Value Validation client. One request, one answer: the backend captures
 * the game's current screen (or reads a screenshot by name), names every symbol on
 * the grid, OCRs the figure on each scatter, and checks that figure against the
 * value range the loaded maths declares for it. No stream, unlike Analyze Spin --
 * there is no run to follow.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const SCATTER_VALIDATION_URL = `${routes.API}/scatter-validation`;

/**
 * Same budget as Evaluate Screen: this is the same pipeline (screenshot, grid
 * split, classifier, OCR per scatter -- but not the meter, which this feature
 * has no use for) plus a paytable read, all on CPU.
 */
const ANALYZE_TIMEOUT_MS = 240_000;

/**
 * Read the game's current screen and validate every landed scatter's figure.
 * @param {{fileName?: string, architecture?: string, includeImages?: boolean}} [options]
 *   `fileName` reads a screenshot already on disk instead of capturing one.
 *   `architecture` picks which trained network names the tiles — omit for the
 *   backend's default.
 * @param {{signal?: AbortSignal}} [request] for a caller that has its own signal.
 * @returns {Promise<object>} the reading: `source`, `reels`, `meter`, `bet_info`,
 *   `checks`, `errors`
 */
export function analyzeScatterValidation(
  { fileName, architecture, includeImages = true } = {},
  { signal } = {},
) {
  return apiRequest({
    method: "POST",
    url: `${SCATTER_VALIDATION_URL}/analyze`,
    data: {
      file_name: fileName ?? null,
      architecture: architecture ?? null,
      include_images: includeImages,
    },
    timeout: ANALYZE_TIMEOUT_MS,
    signal,
  });
}
