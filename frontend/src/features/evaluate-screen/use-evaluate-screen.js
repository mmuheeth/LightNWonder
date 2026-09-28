/** Hooks for Evaluate Screen. */

import { useMutation } from "@tanstack/react-query";

import { analyzeScreen } from "@/features/evaluate-screen/api";

/**
 * Read the game's current screen.
 *
 * A mutation rather than a query, deliberately: there is no server state here to
 * keep fresh. The reading is of the screen *at the moment the button was
 * pressed*, so refetching it would answer a different question, and caching it
 * under a key would suggest it stays true. `mutation.data` holds the last
 * reading until another is asked for, which is exactly the lifetime it has.
 *
 * @returns {import("@tanstack/react-query").UseMutationResult}
 */
export function useAnalyzeScreen() {
  // No abort signal: a react-query mutation's context carries `client`, `meta`
  // and `mutationKey` but no `signal` (unlike a query's), so there is nothing to
  // forward. A reading left in flight by navigating away simply finishes and is
  // discarded -- which is why `api.js` sets a bounded timeout rather than
  // relying on the caller to cancel.
  return useMutation({ mutationFn: analyzeScreen });
}
