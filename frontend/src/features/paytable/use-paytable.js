/** React-query hooks for the loaded paytable's maths. */

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getPaytable } from "@/features/paytable/api";
import { queryKeys } from "@/lib/query-keys";

/**
 * The maths of one paytable. No `refetchInterval`: the answer only moves when
 * the game changes denomination, and the request parses close to a megabyte of
 * XML — a standing poll would cost far more than it could ever notice. The
 * Refresh button is what covers the move.
 *
 * @param {string|null} [paytableId] one of the ids in `available`, or null for
 *   the one the game's log last named.
 */
export function usePaytable(paytableId = null) {
  return useQuery({
    queryKey: queryKeys.paytable.view(paytableId),
    queryFn: ({ signal }) => getPaytable({ paytableId, signal }),
    staleTime: 30_000,
  });
}

/**
 * Re-read the game's log and fetch whatever it names now.
 *
 * Invalidates the whole `paytable` subtree rather than refetching one key: the
 * thing that may have moved is *which* paytable is loaded, and a stale cached
 * entry for the id the log used to name would otherwise still be served the
 * next time the page lands on it.
 */
export function useRefreshPaytable() {
  const queryClient = useQueryClient();

  return () => queryClient.invalidateQueries({ queryKey: queryKeys.paytable.all });
}
