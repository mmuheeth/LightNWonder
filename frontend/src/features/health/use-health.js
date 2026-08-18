import { useQuery } from "@tanstack/react-query";

import { getHealth } from "@/features/health/api";
import { queryKeys } from "@/lib/query-keys";

/**
 * Poll the backend health report.
 *
 * @param {{refetchInterval?: number|false}} [options]
 */
export function useHealth({ refetchInterval = 30_000 } = {}) {
  return useQuery({
    queryKey: queryKeys.health.status(),
    // react-query passes an AbortSignal; forwarding it cancels in-flight
    // requests when the component unmounts.
    queryFn: ({ signal }) => getHealth({ signal }),
    refetchInterval,
    // Health is a live signal, so never serve it from a stale cache.
    staleTime: 0,
  });
}
