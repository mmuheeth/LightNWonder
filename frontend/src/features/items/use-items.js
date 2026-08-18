/**
 * react-query hooks for the example `items` resource.
 *
 * The pattern to copy: queries read, mutations write and then invalidate the
 * affected key subtree so lists refetch themselves.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { createItem, deleteItem, listItems, updateItem } from "@/features/items/api";
import { queryKeys } from "@/lib/query-keys";

/** Paginated list of items. */
export function useItems({ page = 1, pageSize = 20 } = {}) {
  return useQuery({
    queryKey: queryKeys.items.list({ page, pageSize }),
    queryFn: ({ signal }) => listItems({ page, pageSize, signal }),
    // Keep showing the previous page while the next one loads, so the list does
    // not collapse to a skeleton on every page change.
    placeholderData: (previous) => previous,
  });
}

/** Create an item, then refresh every item list. */
export function useCreateItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: createItem,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.items.all }),
  });
}

/** Partially update an item. */
export function useUpdateItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, ...payload }) => updateItem(id, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.items.all }),
  });
}

/** Delete an item. */
export function useDeleteItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: deleteItem,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.items.all }),
  });
}
