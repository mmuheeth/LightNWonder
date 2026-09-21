/**
 * react-query hooks for the replay sequence.
 *
 * One query does both jobs, because the backend's `/status` does: it answers
 * "could a replay run" when nothing is happening and "what is this run doing"
 * while one is. So the poll is opt-in on the only thing that justifies it —
 * a sequence actually walking — and there is no standing poll otherwise, like
 * `useObsStatus` and `useIDeckStatus`.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getReplayStatus, runReplay } from "@/features/replay/api";
import { queryKeys } from "@/lib/query-keys";

/**
 * While a run walks: each step is seconds, and the record is what a person is
 * watching, so this is the interval at which "it is on the record list" stops
 * feeling like a hang.
 */
const RUNNING_INTERVAL_MS = 1_000;
const IDLE_STALE_MS = 30_000;

/**
 * Fetch replay readiness and the live run.
 *
 * @param {{refetchInterval?: number|false}} [options] Override the poll; the
 *   default follows a running sequence and stops when it ends.
 */
export function useReplayStatus({ refetchInterval } = {}) {
  return useQuery({
    queryKey: queryKeys.replay.status(),
    queryFn: ({ signal }) => getReplayStatus({ signal }),
    refetchInterval:
      refetchInterval === undefined
        ? (query) => (query.state.data?.running ? RUNNING_INTERVAL_MS : false)
        : refetchInterval,
    // Idle readiness is not a live signal; a remount inside the window is cached.
    staleTime: IDLE_STALE_MS,
  });
}

/**
 * Start the sequence. Resolves once it has *begun*, not once it has finished,
 * so the record to render is the status query's `run` rather than this
 * mutation's data — invalidating on success is what gets the poll going.
 *
 * A failed step still resolves; only a refusal to start rejects.
 */
export function useRunReplay() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: runReplay,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.replay.all }),
  });
}
