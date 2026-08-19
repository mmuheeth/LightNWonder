/**
 * react-query hooks for the Virtual OLED i-deck.
 *
 * Panel state is server state, so it lives here rather than in the Zustand
 * store. Every mutation invalidates the whole `ideck` subtree, which refreshes
 * both the status and the button list straight after an action — a press
 * can restore the window, which changes the client coordinates of every key.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getIDeckButtons,
  getIDeckStatus,
  pressIDeckButton,
  pressIDeckSequence,
  probeIDeck,
} from "@/features/ideck/api";
import { queryKeys } from "@/lib/query-keys";

/**
 * Fetch the panel status.
 *
 * Background polling is opt-in so the dashboard does not continuously hit the
 * status endpoint. The panel's Refresh button and mutations still refresh it
 * immediately when the user needs current state.
 *
 * @param {{refetchInterval?: number|false}} [options]
 */
export function useIDeckStatus({ refetchInterval = false } = {}) {
  return useQuery({
    queryKey: queryKeys.ideck.status(),
    queryFn: ({ signal }) => getIDeckStatus({ signal }),
    refetchInterval,
    staleTime: 30_000,
  });
}

/**
 * Fetch the deck layout.
 *
 * The geometry is static, but the client coordinates move with the window, so
 * this is refreshed by the same invalidation as everything else.
 */
export function useIDeckButtons() {
  return useQuery({
    queryKey: queryKeys.ideck.buttons(),
    queryFn: ({ signal }) => getIDeckButtons({ signal }),
    staleTime: 0,
  });
}

/** Build a mutation that refreshes the panel state once it succeeds. */
function useIDeckMutation(mutationFn) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.ideck.all }),
  });
}

/** Press one key by alias or layout name. */
export function usePressIDeckButton() {
  return useIDeckMutation(pressIDeckButton);
}

/** Press several keys in order. */
export function usePressIDeckSequence() {
  return useIDeckMutation(pressIDeckSequence);
}

/**
 * Run the side-effect-free capability check.
 *
 * Deliberately invalidates nothing: the outcome is read from `mutation.data`,
 * so the panel needs no extra state to show the result.
 */
export function useProbeIDeck() {
  return useMutation({ mutationFn: probeIDeck });
}
