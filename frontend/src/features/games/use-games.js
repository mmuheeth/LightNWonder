/** React-query hooks for the runtime game catalog and selector. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getGameCatalog, selectGame } from "@/features/games/api";
import { queryKeys } from "@/lib/query-keys";

export function useGameCatalog() {
  return useQuery({
    queryKey: queryKeys.games.catalog(),
    queryFn: ({ signal }) => getGameCatalog({ signal }),
    staleTime: 30_000,
  });
}

export function useSelectGame() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: selectGame,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.games.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.ideck.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.obs.all });
    },
  });
}
