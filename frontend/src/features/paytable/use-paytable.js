/** React-query hooks for the loaded paytable's maths. */

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getPaytable } from "@/features/paytable/api";
import { queryKeys } from "@/lib/query-keys";

/**
 * The maths of one paytable.
 * @param {string|null} [paytableId] one of the ids in `available`, or null for the one
 *   the game's log last named.
 */
export function usePaytable(paytableId = null) {
  return useQuery({
    queryKey: queryKeys.paytable.view(paytableId),
    queryFn: ({ signal }) => getPaytable({ paytableId, signal }),
    staleTime: 30_000,
  });
}

/** Re-read the game's log and fetch whatever it names now. */
export function useRefreshPaytable() {
  const queryClient = useQueryClient();

  return () => queryClient.invalidateQueries({ queryKey: queryKeys.paytable.all });
}
