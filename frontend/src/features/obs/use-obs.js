/**
 * react-query hooks for OBS Studio. Every mutation invalidates the whole `obs`
 * subtree, which refreshes the status straight after an action.
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
 * Fetch the OBS connection and recording status. Background polling is opt-in;
 * the panel's Refresh button and mutations still refresh it immediately.
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
 * Capture a screenshot. Invalidates `roi`/`grid`, not `obs` — a screenshot is
 * also the frame those panels crop and split, and each catalog names the newest one.
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
