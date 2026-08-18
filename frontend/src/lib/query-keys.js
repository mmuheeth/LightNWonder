/** Central registry of react-query cache keys. */

export const queryKeys = Object.freeze({
  obs: Object.freeze({
    all: ["obs"],
    status: () => [...queryKeys.obs.all, "status"],
  }),
  ideck: Object.freeze({
    all: ["ideck"],
    status: () => [...queryKeys.ideck.all, "status"],
    buttons: () => [...queryKeys.ideck.all, "buttons"],
  }),
  games: Object.freeze({
    all: ["games"],
    catalog: () => [...queryKeys.games.all, "catalog"],
  }),
});
