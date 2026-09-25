/** react-query hooks for GAF automation. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  connectGaf,
  disconnectGaf,
  getGafStatus,
  spinGaf,
  takeWinGaf,
} from "@/features/gaf/api";
import { queryKeys } from "@/lib/query-keys";

/**
 * Fetch the automation status. Background polling is off by default, like the
 * other device panels: the mutations below invalidate this, so an action still
 * refreshes it immediately.
 * @param {{refetchInterval?: number|false}} [options]
 */
export function useGafStatus({ refetchInterval = false } = {}) {
  return useQuery({
    queryKey: queryKeys.gaf.status(),
    queryFn: ({ signal }) => getGafStatus({ signal }),
    refetchInterval,
    staleTime: 30_000,
  });
}

/** Build a mutation that refreshes the automation state once it succeeds. */
function useGafMutation(mutationFn) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.gaf.all }),
  });
}

/** Open a session up front. */
export function useConnectGaf() {
  return useGafMutation(connectGaf);
}

/** Release the game. */
export function useDisconnectGaf() {
  return useGafMutation(disconnectGaf);
}

/**
 * Spin the reels. Resolves only once the spin has settled, which on a winning
 * spin means the game is holding with a win to collect — so the take-win
 * button is meaningful the moment this resolves.
 */
export function useSpinGaf() {
  return useGafMutation(spinGaf);
}

/** Collect a win. */
export function useTakeWinGaf() {
  return useGafMutation(takeWinGaf);
}
