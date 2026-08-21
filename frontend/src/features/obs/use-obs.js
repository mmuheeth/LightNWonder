/**
 * react-query hooks for OBS Studio.
 *
 * Connection state is server state, so it lives here rather than in the Zustand
 * store. Every mutation invalidates the whole `obs` subtree, which refreshes the
 * status straight after an action.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  connectObs,
  disconnectObs,
  getObsStatus,
  pauseRecording,
  resumeRecording,
  selectGameWindow,
  startRecording,
  stopRecording,
  takeScreenshot,
} from "@/features/obs/api";
import { queryKeys } from "@/lib/query-keys";

/**
 * Fetch the OBS connection and recording status.
 *
 * Background polling is opt-in so the dashboard does not continuously hit the
 * status endpoint. The panel's Refresh button and mutations still refresh it
 * immediately when the user needs current state.
 *
 * @param {{refetchInterval?: number|false}} [options]
 */
export function useObsStatus({ refetchInterval = false } = {}) {
  return useQuery({
    queryKey: queryKeys.obs.status(),
    queryFn: ({ signal }) => getObsStatus({ signal }),
    refetchInterval,
    staleTime: 30_000,
  });
}

/** Build a mutation that refreshes OBS status once it succeeds. */
function useObsMutation(mutationFn) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.obs.all }),
  });
}

export function useConnectObs() {
  return useObsMutation(connectObs);
}

export function useDisconnectObs() {
  return useObsMutation(disconnectObs);
}

export function useSelectGameWindow() {
  return useObsMutation(selectGameWindow);
}

export function useStartRecording() {
  return useObsMutation(startRecording);
}

export function useStopRecording() {
  return useObsMutation(stopRecording);
}

export function usePauseRecording() {
  return useObsMutation(pauseRecording);
}

export function useResumeRecording() {
  return useObsMutation(resumeRecording);
}

/**
 * Capture a screenshot.
 *
 * The preview is read from `mutation.data`, so nothing in the `obs` subtree
 * needs invalidating for the panel's own sake. The `roi` and `grid` subtrees do:
 * a screenshot is also the frame those panels crop and split, and each of their
 * catalogs names the newest one.
 */
export function useTakeScreenshot() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: takeScreenshot,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.roi.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.grid.all });
    },
  });
}
