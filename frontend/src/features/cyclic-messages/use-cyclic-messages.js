/**
 * react-query hooks for Cyclic Messages. Same shape as
 * `features/event-capture/use-event-capture.js`, including its functional
 * `refetchInterval` — nothing polls unless a run is actually going.
 *
 * The interval is tighter than event capture's 2s: a sampled line-message pass
 * lasts under 7s and takes a frame roughly every second, so a 2s poll would
 * show the panel's "latest messages" list jumping several at a time.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getCyclicRun,
  getCyclicStatus,
  listCyclicRuns,
  readCyclicText,
  startCyclic,
  stopCyclic,
} from "@/features/cyclic-messages/api";
import { queryKeys } from "@/lib/query-keys";

const ACTIVE_INTERVAL_MS = 1_000;
const IDLE_STALE_MS = 30_000;

/** Poll the run in progress. */
export function useCyclicStatus() {
  return useQuery({
    queryKey: queryKeys.cyclicMessages.status(),
    queryFn: ({ signal }) => getCyclicStatus({ signal }),
    refetchInterval: (query) => (query.state.data?.active ? ACTIVE_INTERVAL_MS : false),
    // Idle status is not a live signal; a remount inside the window is cached.
    staleTime: IDLE_STALE_MS,
  });
}

/** Build a mutation that refreshes cyclic message state once it succeeds. */
function useCyclicMutation(mutationFn) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.cyclicMessages.all }),
  });
}

export function useStartCyclic() {
  return useCyclicMutation(startCyclic);
}

/** Stopping also adds a run to the list, which the shared invalidate covers. */
export function useStopCyclic() {
  return useCyclicMutation(stopCyclic);
}

/** Every finished run, newest first. */
export function useCyclicRuns() {
  return useQuery({
    queryKey: queryKeys.cyclicMessages.runs(),
    queryFn: ({ signal }) => listCyclicRuns({ signal }),
  });
}

/** One run with its events. Skipped until a run is selected. */
export function useCyclicRun(runId) {
  return useQuery({
    queryKey: queryKeys.cyclicMessages.run(runId),
    queryFn: ({ signal }) => getCyclicRun(runId, { signal }),
    enabled: Boolean(runId),
  });
}

/**
 * Read one clip's messages back out of it.
 *
 * A mutation rather than a query, even though it only reads: it decodes ~180
 * frames and shells out to Tesseract for each, so it has to be asked for
 * rather than fetched on mount. Deliberately does not invalidate anything —
 * reading a clip changes nothing on the run.
 */
export function useReadCyclicText(runId) {
  return useMutation({
    mutationFn: (options) => readCyclicText(runId, options),
  });
}
