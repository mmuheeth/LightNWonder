import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HealthCard } from "@/features/health/health-card";
import { ApiError } from "@/lib/api-error";
import { http } from "@/lib/http";
import { renderWithProviders } from "@/test/utils";

const HEALTHY_ENVELOPE = {
  success: true,
  message: "Service is healthy",
  data: {
    status: "healthy",
    service: "LightNWonder API",
    version: "0.1.0",
    environment: "local",
    uptime_seconds: 125,
    checks: [],
  },
  error: null,
  meta: { request_id: "r1", timestamp: "2026-08-18T09:12:44Z" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("HealthCard", () => {
  it("renders the report once the request resolves", async () => {
    vi.spyOn(http, "request").mockResolvedValue({
      status: 200,
      data: HEALTHY_ENVELOPE,
    });

    renderWithProviders(<HealthCard />);

    expect(await screen.findByText("healthy")).toBeInTheDocument();
    expect(screen.getByText("LightNWonder API")).toBeInTheDocument();
    expect(screen.getByText("0.1.0")).toBeInTheDocument();
    expect(screen.getByText("2m 5s")).toBeInTheDocument();
    expect(screen.getByText(/No dependency probes registered/)).toBeInTheDocument();
  });

  it("lists dependency probes when the backend reports them", async () => {
    vi.spyOn(http, "request").mockResolvedValue({
      status: 200,
      data: {
        ...HEALTHY_ENVELOPE,
        message: "Service is degraded",
        data: {
          ...HEALTHY_ENVELOPE.data,
          status: "degraded",
          checks: [
            { name: "postgres", healthy: false, latency_ms: 12.5, error: "timeout" },
            { name: "cache", healthy: true, latency_ms: 1.2, error: null },
          ],
        },
      },
    });

    renderWithProviders(<HealthCard />);

    expect(await screen.findByText("degraded")).toBeInTheDocument();
    expect(screen.getByText("postgres")).toBeInTheDocument();
    expect(screen.getByText("down")).toBeInTheDocument();
    expect(screen.getByText("up")).toBeInTheDocument();
  });

  it("shows the backend message and request id when the call fails", async () => {
    vi.spyOn(http, "request").mockRejectedValue(
      new ApiError({
        message: "Could not reach the server. Check your connection and try again.",
        code: "NETWORK_ERROR",
        requestId: "trace-9",
      }),
    );

    renderWithProviders(<HealthCard />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByText(/Could not reach the server/)).toBeInTheDocument();
    expect(screen.getByText(/trace-9/)).toBeInTheDocument();
  });
});
