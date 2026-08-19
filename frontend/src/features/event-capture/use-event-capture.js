/**
 * react-query hooks for Event Based Capture.
 *
 * Same shape as `use-obs.js`: run state is server state, so it is polled rather
 * than mirrored into the store, and every mutation invalidates the feature's
 * whole subtree so the poll refreshes straight after an action.
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
const IDLE_INTERVAL_MS = 10_000;

/**
 * Poll the run in progress.
 *
 * The interval is a function of the last result so a live run feels responsive
 * without an idle dashboard asking ten times a minute for the same answer.
 */
export function useCaptureStatus() {
  return useQuery({
    queryKey: queryKeys.eventCapture.status(),
    queryFn: ({ signal }) => getCaptureStatus({ signal }),
    refetchInterval: (query) =>
      query.state.data?.active ? ACTIVE_INTERVAL_MS : IDLE_INTERVAL_MS,
    // Run state is a live signal; never serve it from a stale cache.
    staleTime: 0,
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
