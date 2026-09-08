/** Hooks for Analyze Spin. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import {
  cancelSpin,
  getSpinStatus,
  spinStreamUrl,
  startSpin,
} from "@/features/analyze-spin/api";
import { queryKeys } from "@/lib/query-keys";

/** How long to wait before reopening a dropped socket. */
const RECONNECT_MS = 2_000;

/**
 * The run as it happens.
 * @returns {{state: {active: boolean, run: object|null}|null, connected: boolean}}
 */
export function useSpinStream() {
  const [state, setState] = useState(null);
  const [connected, setConnected] = useState(false);
  const queryClient = useQueryClient();
  // Which run-and-state has already had its report fetched, so a stream that
  // keeps pushing a finished run does not refetch it on every frame.
  const fetched = useRef(null);

  useEffect(() => {
    let socket = null;
    let retry = null;
    let stopped = false;

    const open = () => {
      socket = new WebSocket(spinStreamUrl());

      socket.onopen = () => setConnected(true);

      socket.onmessage = (message) => {
        let next;
        try {
          next = JSON.parse(message.data);
        } catch {
          return;
        }
        setState(next);

        const run = next?.run;
        if (!run || next.active) return;
        // A finished run is the moment its pictures exist.
        const stamp = `${run.run_id}:${run.state}`;
        if (fetched.current === stamp) return;
        fetched.current = stamp;
        queryClient.invalidateQueries({ queryKey: queryKeys.analyzeSpin.all });
      };

      socket.onerror = () => socket?.close();

      socket.onclose = () => {
        setConnected(false);
        if (!stopped) retry = setTimeout(open, RECONNECT_MS);
      };
    };

    open();

    return () => {
      stopped = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, [queryClient]);

  return { state, connected };
}

/**
 * The last run with its pictures. Not polled: nothing about a finished run
 * changes, and `useSpinStream` invalidates this the moment one ends.
 */
export function useSpinReport() {
  return useQuery({
    queryKey: queryKeys.analyzeSpin.report(),
    queryFn: ({ signal }) => getSpinStatus({ includeImages: true, signal }),
    staleTime: 30_000,
  });
}

/** Build a mutation that refreshes the report once it succeeds. */
function useSpinMutation(mutationFn) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.analyzeSpin.all }),
  });
}

/** Spin once and validate it. `mutate({ record })` to also make a video. */
export function useStartSpin() {
  return useSpinMutation(startSpin);
}

/** Ask the run in progress to stop. */
export function useCancelSpin() {
  return useSpinMutation(cancelSpin);
}

/** The run to render, and the one to take pictures from. */
export function useSpinView() {
  const { state, connected } = useSpinStream();
  const report = useSpinReport();

  const live = state ?? report.data ?? null;
  const run = live?.run ?? null;
  const reported = report.data?.run ?? null;
  const detailed = run && reported && reported.run_id === run.run_id ? reported : null;

  return {
    run,
    detailed,
    active: Boolean(live?.active),
    connected,
    error: report.error,
    isPending: state === null && report.isPending,
    refetch: report.refetch,
  };
}
