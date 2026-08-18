import { describe, expect, it } from "vitest";

import { ApiError, CLIENT_ERROR_CODES } from "@/lib/api-error";

/** Shape an axios-style rejection carrying the backend's failure envelope. */
function axiosErrorWithEnvelope({ status, message, code, details = [], requestId }) {
  return {
    response: {
      status,
      headers: { "x-request-id": requestId },
      data: {
        success: false,
        message,
        data: null,
        error: { code, details },
        meta: { request_id: requestId, timestamp: "2026-08-18T09:12:44Z" },
      },
    },
  };
}

describe("ApiError.from", () => {
  it("reads code, message, details and request id out of the envelope", () => {
    const error = ApiError.from(
      axiosErrorWithEnvelope({
        status: 404,
        message: "Game configuration was not found",
        code: "NOT_FOUND",
        requestId: "abc123",
      }),
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error.message).toBe("Game configuration was not found");
    expect(error.code).toBe("NOT_FOUND");
    expect(error.status).toBe(404);
    expect(error.requestId).toBe("abc123");
    expect(error.isNotFound).toBe(true);
    expect(error.isClientError).toBe(true);
    expect(error.isRetryable).toBe(false);
  });

  it("maps validation details onto input names, stripping the location prefix", () => {
    const error = ApiError.from(
      axiosErrorWithEnvelope({
        status: 422,
        message: "Request validation failed",
        code: "VALIDATION_ERROR",
        details: [
          { field: "body.name", message: "Field required", type: "missing" },
          {
            field: "body.quantity",
            message: "Input should be greater than or equal to 0",
            type: "greater_than_equal",
          },
        ],
      }),
    );

    expect(error.fieldErrors).toEqual({
      name: "Field required",
      quantity: "Input should be greater than or equal to 0",
    });
  });

  it("keeps the first problem when a field has several", () => {
    const error = ApiError.from(
      axiosErrorWithEnvelope({
        status: 422,
        message: "Request validation failed",
        code: "VALIDATION_ERROR",
        details: [
          { field: "body.name", message: "first" },
          { field: "body.name", message: "second" },
        ],
      }),
    );

    expect(error.fieldErrors).toEqual({ name: "first" });
  });

  it("treats 5xx as retryable", () => {
    const error = ApiError.from(
      axiosErrorWithEnvelope({
        status: 503,
        message: "Service is unhealthy; not ready to serve traffic",
        code: "SERVICE_UNAVAILABLE",
      }),
    );

    expect(error.isRetryable).toBe(true);
    expect(error.isClientError).toBe(false);
  });

  it("flags auth failures", () => {
    for (const status of [401, 403]) {
      const error = ApiError.from(
        axiosErrorWithEnvelope({ status, message: "Nope", code: "FORBIDDEN" }),
      );
      expect(error.isAuthError).toBe(true);
    }
  });

  it("reports a non-envelope response as malformed rather than leaking it", () => {
    const error = ApiError.from({
      response: { status: 502, headers: {}, data: "<html>Bad Gateway</html>" },
    });

    expect(error.code).toBe(CLIENT_ERROR_CODES.MALFORMED);
    expect(error.status).toBe(502);
    expect(error.message).toContain("502");
  });

  it("distinguishes timeouts from other network failures", () => {
    expect(ApiError.from({ code: "ECONNABORTED" }).code).toBe(
      CLIENT_ERROR_CODES.TIMEOUT,
    );
    expect(ApiError.from({ request: {} }).code).toBe(CLIENT_ERROR_CODES.NETWORK);
    expect(ApiError.from({ code: "ERR_CANCELED" }).code).toBe(
      CLIENT_ERROR_CODES.CANCELED,
    );
  });

  it("does not retry a canceled request", () => {
    expect(ApiError.from({ code: "ERR_CANCELED" }).isRetryable).toBe(false);
    expect(ApiError.from({ request: {} }).isRetryable).toBe(true);
  });

  it("passes an existing ApiError through untouched", () => {
    const original = new ApiError({ message: "already wrapped", code: "X" });
    expect(ApiError.from(original)).toBe(original);
  });
});
