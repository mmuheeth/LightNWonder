import { afterEach, describe, expect, it, vi } from "vitest";

import { readCyclicText } from "@/features/cyclic-messages/api";
import { http } from "@/lib/http";

const META = { request_id: "r1", timestamp: "2026-09-09T22:12:53Z" };

function respond(data) {
  return vi
    .spyOn(http, "request")
    .mockResolvedValue({
      status: 200,
      data: { success: true, message: "ok", data, error: null, meta: META },
    });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("readCyclicText", () => {
  it("allows minutes rather than the client's global 15s timeout", async () => {
    // The regression this guards: reading a 90s clip is ~90 OCR calls, and on
    // Tesseract at twice the sampling rate that measured 46s, so the default
    // 15s timeout failed it every time with "The request timed out" and no
    // reading at all.
    const request = respond({ messages: [] });

    await readCyclicText("2026-09-09_22-12-53");

    const [config] = request.mock.calls[0];
    expect(config.timeout).toBeGreaterThanOrEqual(120_000);
    // Under the 300s requestTimeout Node gives the Vite dev proxy, or the
    // proxy cuts it first and the reason arrives as a bare network error.
    expect(config.timeout).toBeLessThan(300_000);
  });

  it("names the clip and the interval only when asked to", async () => {
    const request = respond({ messages: [] });

    await readCyclicText("run-1");
    expect(request.mock.calls[0][0].url).toMatch(/\/runs\/run-1\/text$/);

    await readCyclicText("run-1", { cycle: 4, intervalSeconds: 0.25 });
    expect(request.mock.calls[1][0].url).toContain("cycle=4");
    expect(request.mock.calls[1][0].url).toContain("interval_seconds=0.25");
  });

  it("escapes a run id rather than pasting it into the path", async () => {
    const request = respond({ messages: [] });

    await readCyclicText("a/../b");

    expect(request.mock.calls[0][0].url).toContain("a%2F..%2Fb");
  });
});
