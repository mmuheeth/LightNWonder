/** react-query hooks for the Virtual OLED i-deck. */

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

/** Fetch the deck layout. */
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

/** Press one key by its layout name. */
export function usePressIDeckButton() {
  return useIDeckMutation(pressIDeckButton);
}

/** Press several keys in order. */
export function usePressIDeckSequence() {
  return useIDeckMutation(pressIDeckSequence);
}

/** Run the side-effect-free capability check. */
export function useProbeIDeck() {
  return useMutation({ mutationFn: probeIDeck });
}
