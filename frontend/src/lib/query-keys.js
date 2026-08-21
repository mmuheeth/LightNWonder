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
  roi: Object.freeze({
    all: ["roi"],
    regions: () => [...queryKeys.roi.all, "regions"],
  }),
  grid: Object.freeze({
    all: ["grid"],
    layout: () => [...queryKeys.grid.all, "layout"],
  }),
  eventCapture: Object.freeze({
    all: ["event-capture"],
    status: () => [...queryKeys.eventCapture.all, "status"],
    runs: () => [...queryKeys.eventCapture.all, "runs"],
    run: (runId) => [...queryKeys.eventCapture.all, "run", runId],
  }),
});
