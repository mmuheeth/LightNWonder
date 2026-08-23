/** React-query hooks for the reel grid. */

import { useMutation, useQuery } from "@tanstack/react-query";

import { getGridLayout, splitGrid } from "@/features/grid/api";
import { queryKeys } from "@/lib/query-keys";

/**
 * The active game's grid, and the frame a split would use. No `refetchInterval`
 * — the shape only changes on game/config change, and a new frame invalidates
 * this key.
 */
export function useGridLayout() {
  return useQuery({
    queryKey: queryKeys.grid.layout(),
    queryFn: ({ signal }) => getGridLayout({ signal }),
    staleTime: 30_000,
  });
}

/**
 * Split the reels of the newest frame. Invalidates nothing — tiles are read
 * from `mutation.data`, so the panel needs no extra state to hold them.
 */
export function useSplitGrid() {
  return useMutation({ mutationFn: splitGrid });
}
