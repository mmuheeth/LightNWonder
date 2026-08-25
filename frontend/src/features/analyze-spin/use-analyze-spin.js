/**
 * Hooks for Analyze Spin. Unlike every other slice here, the live half is not
 * react-query: a run publishes a snapshot on every step transition, and polling
 * an endpoint fast enough to catch twelve of them in twenty seconds is worse in
 * every way than the socket the backend already offers.
 *
 * So the two halves are split by what they are for:
 *
 * - `useSpinStream` is the run as it happens, straight off the WebSocket. Each
 *   frame is a whole state, never a delta, so a dropped frame or a late
 *   subscriber is still correct — and the socket sends the current state on
 *   connect, so there is no gap to fill on mount.
 * - `useSpinReport` is the pictures. They only exist once a run has finished
 *   and the stream deliberately leaves them out, so they are fetched once, when
 *   the stream says the run is over.
 */

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
 *
 * Reconnects on its own, because the backend restarting under a dev server is
 * routine and a page that silently stopped updating is the worst way to find
 * out. `connected` is surfaced rather than hidden for the same reason.
 *
 * @returns {{state: {active: boolean, run: object|null}|null,
 *   connected: boolean}}
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

/** Spin once and validate it. */
export function useStartSpin() {
  return useSpinMutation(startSpin);
}

/** Ask the run in progress to stop. */
export function useCancelSpin() {
  return useSpinMutation(cancelSpin);
}

/**
 * The run to render, and the one to take pictures from.
 *
 * The two can disagree for a moment — the stream is already on the new run
 * while the report still holds the last one — so the pictures are only used
 * when both name the same run id. Showing the previous spin's reels beside this
 * spin's verdict would be worse than showing none.
 */
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
