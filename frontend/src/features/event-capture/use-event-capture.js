/**
 * react-query hooks for Event Based Capture. Same shape as `use-obs.js`: run
 * state is polled server state, and every mutation invalidates the subtree.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getCaptureRun,
  getCaptureStatus,
  listCaptureRuns,
  startCapture,
  stopCapture,
} from "@/features/event-capture/api";
import { queryKeys } from "@/lib/query-keys";

/** While tracking, events land every few seconds; idle, nothing changes. */
const ACTIVE_INTERVAL_MS = 2_000;
const IDLE_STALE_MS = 30_000;

/**
 * Poll the run in progress. Idles on `false` rather than a slow interval when
 * nothing is active, in line with `useObsStatus` — mutations invalidate the
 * subtree so the card still updates the moment you act on it.
 */
export function useCaptureStatus() {
  return useQuery({
    queryKey: queryKeys.eventCapture.status(),
    queryFn: ({ signal }) => getCaptureStatus({ signal }),
    refetchInterval: (query) => (query.state.data?.active ? ACTIVE_INTERVAL_MS : false),
    // Idle status is not a live signal; a remount inside the window is cached.
    staleTime: IDLE_STALE_MS,
  });
}

/** Build a mutation that refreshes capture state once it succeeds. */
function useCaptureMutation(mutationFn) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.eventCapture.all }),
  });
}

export function useStartCapture() {
  return useCaptureMutation(startCapture);
}

/** Stopping also adds a run to the list, which the shared invalidate covers. */
export function useStopCapture() {
  return useCaptureMutation(stopCapture);
}

/** Every finished run, newest first. */
export function useCaptureRuns() {
  return useQuery({
    queryKey: queryKeys.eventCapture.runs(),
    queryFn: ({ signal }) => listCaptureRuns({ signal }),
  });
}

/** One run with its events. Skipped until a run is selected. */
export function useCaptureRun(runId) {
  return useQuery({
    queryKey: queryKeys.eventCapture.run(runId),
    queryFn: ({ signal }) => getCaptureRun(runId, { signal }),
    enabled: Boolean(runId),
  });
}
