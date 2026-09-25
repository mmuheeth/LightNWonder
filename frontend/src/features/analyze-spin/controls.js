/**
 * What drives the two presses of a run, in the words each page needs.
 *
 * Everything between them is the same either way — the same thirteen steps,
 * the same screenshots, the same log-following, the same three readings at the
 * end — so this is the whole of the difference between the two Analyze Spin
 * routes, and `name` is what travels to the backend as `?control=`.
 */
export const SPIN_CONTROLS = Object.freeze({
  ideck: Object.freeze({
    name: "ideck",
    badge: "i-deck",
    title: "Analyze Spin",
    description:
      "Spin once, and validate the meter and the paylines against the maths the game has loaded",
    idle: "No spin analysed yet. The game and OBS need to be running, and the i-deck panel open.",
  }),
  gaf: Object.freeze({
    name: "gaf",
    badge: "GAF",
    title: "Analyze Spin with EGM",
    description:
      "The same spin, driven by calling the game's own methods rather than by pressing the panel and clicking the glass",
    idle: "No spin analysed yet. The game and OBS need to be running, and NRobot.Server.exe answering.",
  }),
});
