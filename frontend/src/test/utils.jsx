/** Test helpers: render a component with the providers it needs. */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

export function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

/**
 * @param {React.ReactElement} ui
 * @param {{route?: string, queryClient?: QueryClient}} [options]
 */
export function renderWithProviders(ui, { route = "/", queryClient } = {}) {
  const client = queryClient ?? createTestQueryClient();

  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );

  return { ...result, queryClient: client };
}
