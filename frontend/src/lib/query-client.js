import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "@/lib/api-error";

/**
 * Build the react-query client.
 *
 * A factory rather than a module-level singleton so tests get a fresh cache per
 * case and cannot leak state between them.
 */
export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Data is considered fresh for 30s; navigating back within that window
        // renders from cache with no request.
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        // Retrying a 404 or a validation error just wastes time and delays the
        // error the user needs to see.
        retry: (failureCount, error) =>
          error instanceof ApiError
            ? error.isRetryable && failureCount < 2
            : failureCount < 2,
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8_000),
        refetchOnWindowFocus: false,
      },
      mutations: {
        // Mutations are usually not idempotent; never retry them blindly.
        retry: false,
      },
    },
  });
}
