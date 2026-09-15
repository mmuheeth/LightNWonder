/** Hooks for Scatter Value Validation. */

import { useMutation } from "@tanstack/react-query";

import { analyzeScatterValidation } from "@/features/scatter-validation/api";

/**
 * Read the game's current screen and validate every landed scatter's figure.
 *
 * A mutation rather than a query, for the same reason Evaluate Screen is one:
 * the reading is of the screen *at the moment the button was pressed*, so there
 * is no server state to keep fresh or cache under a key. `mutation.data` holds
 * the last reading until another is asked for.
 *
 * @returns {import("@tanstack/react-query").UseMutationResult}
 */
export function useAnalyzeScatterValidation() {
  return useMutation({ mutationFn: analyzeScatterValidation });
}
