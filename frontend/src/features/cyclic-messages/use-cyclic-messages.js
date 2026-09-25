/**
 * react-query hooks for Cyclic Messages. Same shape as
 * `features/event-capture/use-event-capture.js`, including its functional
 * `refetchInterval` — nothing polls unless a run is actually going.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getCyclicLive,
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

/**
 * How often the live view refetches while a presentation is being captured or
 * its just-closed pass is being read.
 *
 * Tighter than the status poll because this is the view whose whole point is
 * that a frame shows up as soon as it exists. Capture and reading never run at
 * the same time — see the backend's own note on `CYCLIC_MESSAGES_LIVE_READ` —
 * so this one interval covers both: 500ms is well inside the 2s a frame is
 * captured at and short enough to show each caption landing during the batch
 * read that follows a pass.
 */
const CAPTURING_INTERVAL_MS = 500;

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

/**
 * Poll the presentation being captured right now.
 *
 * Three speeds rather than two, because "a run is going" and "a win is being
 * captured" are different states: half a second while frames are arriving, the
 * ordinary status interval while a run idles between wins (readings for the
 * last pass can still be landing), and nothing at all when no run is going.
 *
 * @param {boolean} isActive whether a run is in progress, from `useCyclicStatus`
 */
export function useCyclicLive(isActive) {
  return useQuery({
    queryKey: queryKeys.cyclicMessages.live(),
    queryFn: ({ signal }) => getCyclicLive({ signal }),
    enabled: Boolean(isActive),
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data?.active) return false;
      return data.capturing ? CAPTURING_INTERVAL_MS : ACTIVE_INTERVAL_MS;
    },
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
