/**
 * Evaluate Screen client. One request, one answer: the backend captures the game's
 * current screen (or reads a screenshot by name), names every symbol on the grid and
 * reads the cash meter. No stream, unlike Analyze Spin — there is no run to follow.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const EVALUATE_SCREEN_URL = `${routes.API}/evaluate-screen`;

/**
 * Well past the global 15s, because this one request is the whole pipeline: a
 * screenshot out of OBS, the grid split, a forward pass over fifteen tiles, then
 * a PaddleOCR read per scatter orb *and* per meter cell -- all on CPU, and the
 * first call of a process also loads the Paddle detector and recogniser off
 * disk. Measured on a nine-scatter screen: 70s, of which ~37s was the orbs.
 *
 * Deliberately a per-request timeout rather than a raise of `apiTimeoutMs`: a
 * three-minute global default would leave every genuinely hung request hanging
 * the page. Same reasoning, and same shape, as the image classifier's
 * `CLASSIFY_TIMEOUT_MS`.
 */
const ANALYZE_TIMEOUT_MS = 240_000;

/**
 * Read the game's current screen.
 * @param {{fileName?: string, architecture?: string, includeImages?: boolean}} [options]
 *   `fileName` reads a screenshot already on disk instead of capturing one.
 *   `architecture` picks which trained network names the tiles — omit for the
 *   backend's default.
 * @param {{signal?: AbortSignal}} [request] for a caller that has its own signal.
 *   `useAnalyzeScreen` passes none -- a react-query mutation has no signal to
 *   give -- so this is for a direct call or a test that wants to abort one.
 * @returns {Promise<object>} the reading: `source`, `reels`, `meter`, `errors`
 */
export function analyzeScreen(
  { fileName, architecture, includeImages = true } = {},
  { signal } = {},
) {
  return apiRequest({
    method: "POST",
    url: `${EVALUATE_SCREEN_URL}/analyze`,
    data: {
      file_name: fileName ?? null,
      architecture: architecture ?? null,
      include_images: includeImages,
    },
    timeout: ANALYZE_TIMEOUT_MS,
    signal,
  });
}
