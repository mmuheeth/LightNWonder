/**
 * react-query hooks for OBS Studio.
 *
 * Connection state is server state, so it lives here rather than in the Zustand
 * store. Every mutation invalidates the whole `obs` subtree, which refreshes the
 * status poll straight after an action.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  connectObs,
  disconnectObs,
  getObsStatus,
  pauseRecording,
  resumeRecording,
  startRecording,
  stopRecording,
  takeScreenshot,
} from "@/features/obs/api";
import { queryKeys } from "@/lib/query-keys";

/**
 * Poll the OBS connection and recording status.
 *
 * @param {{refetchInterval?: number|false}} [options]
 */
export function useObsStatus({ refetchInterval = 5_000 } = {}) {
  return useQuery({
    queryKey: queryKeys.obs.status(),
    queryFn: ({ signal }) => getObsStatus({ signal }),
    refetchInterval,
    // Connection state is a live signal; never serve it from a stale cache.
    staleTime: 0,
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
 * Deliberately does not invalidate anything: the image is read from
 * `mutation.data`, so the panel needs no extra state to hold the preview.
 */
export function useTakeScreenshot() {
  return useMutation({ mutationFn: takeScreenshot });
}
