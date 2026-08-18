import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest, apiRequestPage } from "@/lib/api";
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
      message: "Item retrieved successfully",
      data: { id: 1, name: "Table lamp" },
      error: null,
      meta: { request_id: "r1", timestamp: "2026-08-18T09:12:44Z" },
    });

    await expect(apiRequest({ method: "GET", url: "/api/items/1" })).resolves.toEqual({
      id: 1,
      name: "Table lamp",
    });
  });

  it("returns null for an empty payload, e.g. after a delete", async () => {
    mockResponse({
      success: true,
      message: "Item deleted successfully",
      data: null,
      error: null,
      meta: { request_id: "r1", timestamp: "2026-08-18T09:12:44Z" },
    });

    await expect(
      apiRequest({ method: "DELETE", url: "/api/items/1" }),
    ).resolves.toBeNull();
  });

  it("rejects when a 2xx body is not the expected envelope", async () => {
    mockResponse({ id: 1, name: "unwrapped" });

    await expect(
      apiRequest({ method: "GET", url: "/api/items/1" }),
    ).rejects.toMatchObject({ code: CLIENT_ERROR_CODES.MALFORMED });
  });
});

describe("apiRequestPage", () => {
  it("splits items from the pagination block in meta", async () => {
    mockResponse({
      success: true,
      message: "Retrieved 2 of 5 item(s)",
      data: [{ id: 3 }, { id: 4 }],
      error: null,
      meta: {
        request_id: "r1",
        timestamp: "2026-08-18T09:12:44Z",
        page: 2,
        page_size: 2,
        total_items: 5,
        total_pages: 3,
        has_next: true,
        has_previous: true,
      },
    });

    const { items, pagination } = await apiRequestPage({
      method: "GET",
      url: "/api/items",
    });

    expect(items).toHaveLength(2);
    expect(pagination).toMatchObject({ page: 2, total_pages: 3, has_next: true });
  });

  it("yields an empty list when data is not an array", async () => {
    mockResponse({
      success: true,
      message: "ok",
      data: null,
      error: null,
      meta: { request_id: "r1", timestamp: "2026-08-18T09:12:44Z" },
    });

    const { items } = await apiRequestPage({ method: "GET", url: "/api/items" });
    expect(items).toEqual([]);
  });
});
