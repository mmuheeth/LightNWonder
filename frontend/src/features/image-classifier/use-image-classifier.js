import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  cancelTraining,
  classifyTiles,
  getClassifierStatus,
  getSplits,
  trainClassifier,
} from "@/features/image-classifier/api";
import { queryKeys } from "@/lib/query-keys";

// Polled only while a run is going, the `useCaptureStatus` pattern. Training
// reports once an epoch, which is every twenty seconds or so, so a faster poll
// would only add requests -- and a standing poll when idle would add them for no
// reason at all. Deliberately not a WebSocket: the backend's one stream is the
// spin progress, and this data does not change faster than a person reads it.
const ACTIVE_INTERVAL_MS = 2_000;
const IDLE_STALE_MS = 30_000;

/** Engine state, the model, the dataset and the live run. */
export function useClassifierStatus() {
  return useQuery({
    queryKey: queryKeys.imageClassifier.status(),
    queryFn: ({ signal }) => getClassifierStatus({ signal }),
    refetchInterval: (query) => (query.state.data?.active ? ACTIVE_INTERVAL_MS : false),
    staleTime: IDLE_STALE_MS,
  });
}

/** Which splits could be classified, and which one is newest. */
export function useSplits() {
  return useQuery({
    queryKey: queryKeys.imageClassifier.splits(),
    queryFn: ({ signal }) => getSplits({ signal }),
    staleTime: IDLE_STALE_MS,
  });
}

/** Build a mutation that refreshes classifier status once it succeeds. */
function useClassifierMutation(mutationFn) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.imageClassifier.all }),
  });
}

export function useTrainClassifier() {
  return useClassifierMutation(trainClassifier);
}

export function useCancelTraining() {
  return useClassifierMutation(cancelTraining);
}

/**
 * Classify a split. Invalidates nothing — the result is read straight off
 * `mutation.data`, so the page needs no extra state to hold it, and re-running at
 * a different threshold replaces its own answer.
 */
export function useClassifyTiles() {
  return useMutation({ mutationFn: classifyTiles });
}
