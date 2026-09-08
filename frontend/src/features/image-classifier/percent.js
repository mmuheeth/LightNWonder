/** A probability as a percentage, at the precision the classes actually separate by. */
export function percent(value) {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
}
