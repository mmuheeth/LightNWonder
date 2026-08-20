/** React-query hooks for ROI extraction. */

import { useMutation, useQuery } from "@tanstack/react-query";

import { extractRoi, getRoiRegions } from "@/features/roi/api";
import { queryKeys } from "@/lib/query-keys";

/**
 * The active game's regions, and the frame an extraction would use.
 *
 * No `refetchInterval`, like the OBS and i-deck status queries: the frame only
 * changes when someone takes a screenshot, and `useTakeScreenshot` invalidates
 * this key when they do.
 */
export function useRoiRegions() {
  return useQuery({
    queryKey: queryKeys.roi.regions(),
    queryFn: ({ signal }) => getRoiRegions({ signal }),
    staleTime: 30_000,
  });
}

/**
 * Extract one region.
 *
 * Deliberately does not invalidate anything: the crop is read from
 * `mutation.data`, so the panel needs no extra state to hold it.
 */
export function useExtractRoi() {
  return useMutation({ mutationFn: extractRoi });
}
