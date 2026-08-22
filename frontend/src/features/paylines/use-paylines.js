/** React-query hooks for the payline check. */

import { useMutation, useQuery } from "@tanstack/react-query";

import { checkPaylines, getPaylineLayout } from "@/features/paylines/api";
import { queryKeys } from "@/lib/query-keys";

/**
 * The sets the active game declares, and the split a check would read.
 *
 * No `refetchInterval`, like the grid and ROI queries: the sets only change when
 * the game config or the selected game does, and the split only when someone
 * splits one — which is a click in another panel, so the refresh button is here.
 */
export function usePaylineLayout() {
  return useQuery({
    queryKey: queryKeys.paylines.layout(),
    queryFn: ({ signal }) => getPaylineLayout({ signal }),
    staleTime: 30_000,
  });
}

/**
 * Check one set against one split.
 *
 * Deliberately invalidates nothing, like `useSplitGrid`: the verdict is read
 * from `mutation.data`, so the panel needs no extra state to hold it, and the
 * picture it wrote is not something the page reads back.
 */
export function useCheckPaylines() {
  return useMutation({ mutationFn: checkPaylines });
}
