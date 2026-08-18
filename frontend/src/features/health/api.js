import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

/**
 * Fetch the backend health report.
 *
 * Note the path: health is mounted at the backend root, *not* under the API
 * prefix, which is why it uses `routes.HEALTH` rather than `routes.API`.
 *
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<{status: string, service: string, version: string,
 *   environment: string, uptime_seconds: number,
 *   checks: Array<{name: string, healthy: boolean, latency_ms: number|null, error: string|null}>}>}
 */
export function getHealth({ signal } = {}) {
  return apiRequest({ method: "GET", url: routes.HEALTH, signal });
}
