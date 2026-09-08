// Unwraps the backend's `{ success, message, data, error, meta }` envelope so
// feature code and react-query see plain domain data; errors still arrive as
// `ApiError` (see `@/lib/api-error`).

import { ApiError, CLIENT_ERROR_CODES } from "@/lib/api-error";
import { http } from "@/lib/http";

/** Reject if a 2xx response somehow is not the expected envelope. */
function assertEnvelope(envelope, status) {
  if (
    !envelope ||
    typeof envelope !== "object" ||
    typeof envelope.success !== "boolean"
  ) {
    throw new ApiError({
      message: "Unexpected response format from the server.",
      code: CLIENT_ERROR_CODES.MALFORMED,
      status,
    });
  }
}

/**
 * Perform a request and return the envelope's `data`.
 * @param {import("axios").AxiosRequestConfig} config
 * @returns {Promise<unknown>} the unwrapped payload (`null` for empty responses)
 */
export async function apiRequest(config) {
  const response = await http.request(config);
  assertEnvelope(response.data, response.status);
  return response.data.data ?? null;
}
