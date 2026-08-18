/**
 * Typed, validated access to build-time environment variables.
 *
 * Read `env` instead of `import.meta.env` directly so defaults and coercion
 * live in one place and a typo fails here rather than deep in a request.
 */

/** Parse a "true"/"false" string into a boolean. */
function toBoolean(value, fallback = false) {
  if (value == null || value === "") return fallback;
  return value === "true" || value === "1";
}

/** Parse a positive integer, falling back when absent or malformed. */
function toPositiveInt(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export const env = Object.freeze({
  /** Prefix for every API request. Empty in dev so the Vite proxy handles it. */
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL ?? "",
  apiTimeoutMs: toPositiveInt(import.meta.env.VITE_API_TIMEOUT_MS, 15_000),
  apiWithCredentials: toBoolean(import.meta.env.VITE_API_WITH_CREDENTIALS, false),
  isDevelopment: import.meta.env.DEV,
  isProduction: import.meta.env.PROD,
  mode: import.meta.env.MODE,
});

/**
 * Backend route prefixes.
 *
 * `HEALTH` is not under `API`: the backend mounts health at its root on
 * purpose, so probes never depend on the API's base path.
 */
export const routes = Object.freeze({
  API: "/api",
  HEALTH: "/health",
});
