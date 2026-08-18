/**
 * Central registry of react-query cache keys.
 *
 * Keeping them here (rather than inline strings) means invalidation can target a
 * whole subtree: `invalidateQueries({ queryKey: queryKeys.items.all })` clears
 * every item list and detail entry at once.
 */

export const queryKeys = Object.freeze({
  health: Object.freeze({
    all: ["health"],
    status: () => [...queryKeys.health.all, "status"],
  }),
  items: Object.freeze({
    all: ["items"],
    list: (params = {}) => [...queryKeys.items.all, "list", params],
    detail: (id) => [...queryKeys.items.all, "detail", id],
  }),
});
