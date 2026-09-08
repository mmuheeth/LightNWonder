// The single error type every API call rejects with. `ApiError.from` normalises
// the backend's failure envelope — plus network failures, timeouts, and
// non-envelope responses (a proxy's HTML 502, say) — into one shape.

/** Codes assigned client-side, when the request never reached the API. */
export const CLIENT_ERROR_CODES = Object.freeze({
  NETWORK: "NETWORK_ERROR",
  TIMEOUT: "TIMEOUT",
  CANCELED: "CANCELED",
  UNKNOWN: "UNKNOWN_ERROR",
  MALFORMED: "MALFORMED_RESPONSE",
});

export class ApiError extends Error {
  /**
   * @param {object} init
   * @param {string} init.message   Human-readable summary, safe to display.
   * @param {string} init.code      Stable code, e.g. "NOT_FOUND".
   * @param {Array<{field?: string|null, message: string, type?: string|null}>} [init.details]
   * @param {number|null} [init.status]    HTTP status, null if no response.
   * @param {string|null} [init.requestId] Correlation id, for support tickets.
   * @param {unknown} [init.cause]
   */
  constructor({ message, code, details = [], status = null, requestId = null, cause }) {
    super(message, { cause });
    this.name = "ApiError";
    this.code = code;
    this.details = details;
    this.status = status;
    this.requestId = requestId;
  }

  /** True when the caller can fix the request (4xx). */
  get isClientError() {
    return this.status != null && this.status >= 400 && this.status < 500;
  }

  /** True when retrying might help (5xx, network blips, timeouts). */
  get isRetryable() {
    if (this.status == null) return this.code !== CLIENT_ERROR_CODES.CANCELED;
    return this.status >= 500;
  }

  get isNotFound() {
    return this.status === 404;
  }

  /** True when the user is unauthenticated or lacks permission. */
  get isAuthError() {
    return this.status === 401 || this.status === 403;
  }

  /**
   * Field-level problems keyed by field name, for binding to form inputs.
   * @returns {Record<string, string>}
   */
  get fieldErrors() {
    const result = {};
    for (const detail of this.details) {
      if (!detail?.field) continue;
      const name = detail.field.replace(/^(body|query|path|header|cookie)\./, "");
      // Keep the first problem per field; that is the one a form should show.
      result[name] ??= detail.message;
    }
    return result;
  }

  /**
   * Build an ApiError from anything axios rejects with.
   * @param {unknown} error
   * @returns {ApiError}
   */
  static from(error) {
    if (error instanceof ApiError) return error;

    const response = error?.response;

    if (response) {
      const envelope = response.data;
      const isEnvelope = envelope && typeof envelope === "object";
      const requestId =
        envelope?.meta?.request_id ?? response.headers?.["x-request-id"] ?? null;

      // A response that is not our envelope means something between us and the
      // API answered (proxy, gateway, CDN). Say so rather than showing raw HTML.
      if (!isEnvelope || typeof envelope.success !== "boolean") {
        return new ApiError({
          message: `Unexpected response from the server (HTTP ${response.status}).`,
          code: CLIENT_ERROR_CODES.MALFORMED,
          status: response.status,
          requestId,
          cause: error,
        });
      }

      return new ApiError({
        message: envelope.message || "The request failed.",
        code: envelope.error?.code ?? `HTTP_${response.status}`,
        details: envelope.error?.details ?? [],
        status: response.status,
        requestId,
        cause: error,
      });
    }

    if (error?.code === "ECONNABORTED" || error?.code === "ETIMEDOUT") {
      return new ApiError({
        message: "The request timed out. Please try again.",
        code: CLIENT_ERROR_CODES.TIMEOUT,
        cause: error,
      });
    }

    if (error?.code === "ERR_CANCELED") {
      return new ApiError({
        message: "The request was canceled.",
        code: CLIENT_ERROR_CODES.CANCELED,
        cause: error,
      });
    }

    if (error?.request) {
      return new ApiError({
        message: "Could not reach the server. Check your connection and try again.",
        code: CLIENT_ERROR_CODES.NETWORK,
        cause: error,
      });
    }

    return new ApiError({
      message: error?.message || "Something went wrong.",
      code: CLIENT_ERROR_CODES.UNKNOWN,
      cause: error,
    });
  }
}
