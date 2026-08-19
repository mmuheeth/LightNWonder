import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { BrowserRouter } from "react-router-dom";

import { ErrorBoundary } from "@/components/error-boundary";
import { ThemeProvider } from "@/components/theme-provider";
import { createQueryClient } from "@/lib/query-client";

/**
 * Every app-wide provider, in one place.
 *
 * The query client is created in state rather than at module scope so it is not
 * shared across renders in tests or across requests if this ever runs on a
 * server.
 */
export function AppProviders({ children }) {
  const [queryClient] = useState(createQueryClient);

  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <BrowserRouter>{children}</BrowserRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
