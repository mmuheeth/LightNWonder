/**
 * A probability as a percentage, at the precision the classes actually separate
 * by.
 *
 * Its own module rather than a helper inside a component file because the
 * `react-refresh` lint rule wants a component file to export only components --
 * the same reason the old symbol-validation slice kept its `score()` apart.
 *
 * One decimal place. Unlike the payline check's cosine similarity, this **is** a
 * probability, so a percentage is an honest rendering of it rather than a rescaled
 * one -- but it is a probability over the *trained* symbols only, conditioned on
 * the tile being one of them, which it often is not. That is what the confidence
 * floor is for, and why 97.2% is worth distinguishing from 89.9% but 97.23% is
 * not.
 */
export function percent(value) {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
}
