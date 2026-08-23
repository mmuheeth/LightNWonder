/** React-query hooks for the payline check. */

import { useMutation, useQuery } from "@tanstack/react-query";

import { checkPaylines, getPaylineLayout } from "@/features/paylines/api";
import { queryKeys } from "@/lib/query-keys";

/**
 * The sets the active game declares, and the split a check would read. No
 * `refetchInterval` — the split only changes via a click in another panel.
 */
export function usePaylineLayout() {
  return useQuery({
    queryKey: queryKeys.paylines.layout(),
    queryFn: ({ signal }) => getPaylineLayout({ signal }),
    staleTime: 30_000,
  });
}

/** Check one set against one split. Invalidates nothing — the verdict is read from `mutation.data`. */
export function useCheckPaylines() {
  return useMutation({ mutationFn: checkPaylines });
}
