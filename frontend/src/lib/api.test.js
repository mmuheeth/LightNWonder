import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest } from "@/lib/api";
import { CLIENT_ERROR_CODES } from "@/lib/api-error";
import { http } from "@/lib/http";

function mockResponse(data, status = 200) {
  return vi.spyOn(http, "request").mockResolvedValue({ status, data });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("apiRequest", () => {
  it("unwraps data out of the envelope", async () => {
    mockResponse({
      success: true,
      message: "Game selected successfully",
      data: { game: "FortuneOx" },
      error: null,
      meta: { request_id: "r1", timestamp: "2026-08-18T09:12:44Z" },
    });

    await expect(apiRequest({ method: "GET", url: "/api/games/" })).resolves.toEqual({
      game: "FortuneOx",
    });
  });

  it("returns null for an empty payload", async () => {
    mockResponse({
      success: true,
      message: "Request completed successfully",
      data: null,
      error: null,
      meta: { request_id: "r1", timestamp: "2026-08-18T09:12:44Z" },
    });

    await expect(apiRequest({ method: "GET", url: "/api/games/" })).resolves.toBeNull();
  });

  it("rejects when a 2xx body is not the expected envelope", async () => {
    mockResponse({ id: 1, name: "unwrapped" });

    await expect(
      apiRequest({ method: "GET", url: "/api/games/" }),
    ).rejects.toMatchObject({ code: CLIENT_ERROR_CODES.MALFORMED });
  });
});
