/** React-query hooks for the similarity check. */

import { useMutation } from "@tanstack/react-query";

import { compareSimilarity } from "@/features/similarity/api";

/**
 * Run one comparison. Invalidates nothing — the scores are read from
 * `mutation.data`, and no query holds them.
 */
export function useCompareSimilarity() {
  return useMutation({ mutationFn: compareSimilarity });
}
