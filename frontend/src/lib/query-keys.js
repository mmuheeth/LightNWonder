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
  analyzeSpin: Object.freeze({
    all: ["analyze-spin"],
    // The report is the same endpoint asked for its pictures, and they only
    // exist once a run has finished -- so it is its own key rather than a
    // parameter on one, and the progress stream never invalidates it mid-run.
    report: () => [...queryKeys.analyzeSpin.all, "report"],
  }),
  paytable: Object.freeze({
    all: ["paytable"],
    // Parameterised: inspecting another paytable of the same game is a
    // different answer, not a refetch of the loaded one.
    view: (paytableId) => [...queryKeys.paytable.all, "view", paytableId ?? null],
  }),
  paylines: Object.freeze({
    all: ["paylines"],
    layout: () => [...queryKeys.paylines.all, "layout"],
  }),
  eventCapture: Object.freeze({
    all: ["event-capture"],
    status: () => [...queryKeys.eventCapture.all, "status"],
    runs: () => [...queryKeys.eventCapture.all, "runs"],
    run: (runId) => [...queryKeys.eventCapture.all, "run", runId],
  }),
  cyclicMessages: Object.freeze({
    all: ["cyclic-messages"],
    status: () => [...queryKeys.cyclicMessages.all, "status"],
    runs: () => [...queryKeys.cyclicMessages.all, "runs"],
    run: (runId) => [...queryKeys.cyclicMessages.all, "run", runId],
  }),
  imageClassifier: Object.freeze({
    all: ["image-classifier"],
    status: () => [...queryKeys.imageClassifier.all, "status"],
    dataset: () => [...queryKeys.imageClassifier.all, "dataset"],
    splits: () => [...queryKeys.imageClassifier.all, "splits"],
  }),
});
