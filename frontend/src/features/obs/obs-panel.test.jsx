import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ObsPanel } from "@/features/obs/obs-panel";
import { ApiError } from "@/lib/api-error";
import { http } from "@/lib/http";
import { renderWithProviders } from "@/test/utils";

const META = { request_id: "r1", timestamp: "2026-08-18T09:12:44Z" };

function envelope(data, message = "OBS is connected") {
  return { success: true, message, data, error: null, meta: META };
}

const DISCONNECTED = envelope(
  {
    state: "disconnected",
    url: "ws://127.0.0.1:4455",
    obs_version: null,
    obs_websocket_version: null,
    platform: null,
    current_scene: null,
    recording: null,
  },
  "OBS is disconnected",
);

function connected({ active = false, paused = false } = {}) {
  return envelope({
    state: "connected",
    url: "ws://127.0.0.1:4455",
    obs_version: "32.1.2",
    obs_websocket_version: "5.7.3",
    platform: "windows",
    current_scene: "Main",
    recording: {
      active,
      paused,
      timecode: active ? "00:00:12.500" : "00:00:00.000",
      duration_ms: active ? 12500 : 0,
      bytes_written: active ? 2048 : 0,
      output_path: null,
    },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ObsPanel", () => {
  it("offers Connect while OBS is closed", async () => {
    vi.spyOn(http, "request").mockResolvedValue({
      status: 200,
      data: DISCONNECTED,
    });

    renderWithProviders(<ObsPanel />);

    expect(await screen.findByText("disconnected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /connect/i })).toBeInTheDocument();
    // Nothing to record or capture until a session exists.
    expect(screen.getByRole("button", { name: /record/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /screenshot/i })).toBeDisabled();
  });

  it("shows what OBS reports once connected and idle", async () => {
    vi.spyOn(http, "request").mockResolvedValue({
      status: 200,
      data: connected(),
    });

    renderWithProviders(<ObsPanel />);

    expect(await screen.findByText("connected")).toBeInTheDocument();
    expect(screen.getByText("32.1.2")).toBeInTheDocument();
    expect(screen.getByText("Main")).toBeInTheDocument();
    expect(screen.getByText("idle")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^record$/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /disconnect/i })).toBeInTheDocument();
  });

  it("swaps Record for Stop and Pause while recording", async () => {
    vi.spyOn(http, "request").mockResolvedValue({
      status: 200,
      data: connected({ active: true }),
    });

    renderWithProviders(<ObsPanel />);

    expect(await screen.findByText("recording")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /stop/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /pause/i })).toBeInTheDocument();
    expect(screen.getByText("00:00:12.500")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^record$/i })).not.toBeInTheDocument();
  });

  it("offers Resume rather than Pause while paused", async () => {
    vi.spyOn(http, "request").mockResolvedValue({
      status: 200,
      data: connected({ active: true, paused: true }),
    });

    renderWithProviders(<ObsPanel />);

    expect(await screen.findByText("paused")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /resume/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /pause/i })).not.toBeInTheDocument();
  });

  it("shows where the recording was saved after stopping", async () => {
    const stopped = envelope({
      active: false,
      paused: false,
      timecode: "00:00:00.000",
      duration_ms: 0,
      bytes_written: 4096,
      output_path: "C:/captures/take-1.mp4",
    });

    // GET /status never carries output_path, so the panel has to read it from
    // the stop mutation's own result.
    vi.spyOn(http, "request").mockImplementation((config) =>
      Promise.resolve({
        status: 200,
        data: config.url.endsWith("/recording/stop")
          ? stopped
          : connected({ active: true }),
      }),
    );

    renderWithProviders(<ObsPanel />);

    await userEvent.click(await screen.findByRole("button", { name: /stop/i }));

    expect(await screen.findByText("Saved C:/captures/take-1.mp4")).toBeInTheDocument();
  });

  it("surfaces the backend message and request id when the status call fails", async () => {
    vi.spyOn(http, "request").mockRejectedValue(
      new ApiError({
        message: "Could not reach the server. Check your connection and try again.",
        code: "NETWORK_ERROR",
        requestId: "trace-9",
      }),
    );

    renderWithProviders(<ObsPanel />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByText(/Could not reach the server/)).toBeInTheDocument();
    expect(screen.getByText(/trace-9/)).toBeInTheDocument();
  });
});
