/** React-query hooks for ROI extraction. */

import { useMutation, useQuery } from "@tanstack/react-query";

import { extractRoi, getRoiRegions } from "@/features/roi/api";
import { queryKeys } from "@/lib/query-keys";

/**
 * The active game's regions, and the frame an extraction would use. No
 * `refetchInterval` — `useTakeScreenshot` invalidates this key when needed.
 */
export function useRoiRegions() {
  return useQuery({
    queryKey: queryKeys.roi.regions(),
    queryFn: ({ signal }) => getRoiRegions({ signal }),
    staleTime: 30_000,
  });
}

/** Extract one region. Invalidates nothing — the crop is read from `mutation.data`. */
export function useExtractRoi() {
  return useMutation({ mutationFn: extractRoi });
}
