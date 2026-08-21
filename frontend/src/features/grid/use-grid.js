/** React-query hooks for the reel grid. */

import { useMutation, useQuery } from "@tanstack/react-query";

import { getGridLayout, splitGrid } from "@/features/grid/api";
import { queryKeys } from "@/lib/query-keys";

/**
 * The active game's grid, and the frame a split would use.
 *
 * No `refetchInterval`, like the ROI and status queries: the shape only changes
 * when the game config or the selected game does, and the frame only when
 * someone takes a screenshot — which invalidates this key.
 */
export function useGridLayout() {
  return useQuery({
    queryKey: queryKeys.grid.layout(),
    queryFn: ({ signal }) => getGridLayout({ signal }),
    staleTime: 30_000,
  });
}

/**
 * Split the reels of the newest frame.
 *
 * Deliberately does not invalidate anything: the tiles are read from
 * `mutation.data`, so the panel needs no extra state to hold them. The files it
 * wrote are not something the page reads back.
 */
export function useSplitGrid() {
  return useMutation({ mutationFn: splitGrid });
}
