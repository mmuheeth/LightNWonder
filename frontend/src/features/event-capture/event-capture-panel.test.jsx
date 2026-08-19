import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EventCapturePanel } from "@/features/event-capture/event-capture-panel";
import { ApiError } from "@/lib/api-error";
import { http } from "@/lib/http";
import { renderWithProviders } from "@/test/utils";

const META = { request_id: "r1", timestamp: "2026-08-19T09:12:44Z" };

function envelope(data, message = "ok") {
  return { success: true, message, data, error: null, meta: META };
}

const IDLE = envelope(
  {
    active: false,
    run_id: null,
    game: null,
    started_at: null,
    duration_ms: 0,
    event_count: 0,
    recent_events: [],
    errors: [],
  },
  "No capture run is in progress",
);

function running({ events = [], errors = [] } = {}) {
  return envelope({
    active: true,
    run_id: "2026-08-19_14-32-07",
    game: "FortuneOx",
    started_at: "2026-08-19T14:32:07Z",
    duration_ms: 95_000,
    event_count: events.length,
    recent_events: events,
    errors,
  });
}

const SPIN_EVENT = {
  sequence: 1,
  event: "spin-started",
  at: "2026-08-19T14:32:11",
  summary: "Spin requested",
  fields: {},
  screenshot: "001_spin-started_14-32-11.png",
  capture_error: null,
  log_line: "08/19/26 14:32:11.204 00 FortuneOx:9244 DBG: ...",
};

/** Route by URL, the way `obs-panel.test.jsx` does. */
function respond(map) {
  return vi.spyOn(http, "request").mockImplementation((config) => {
    for (const [suffix, response] of Object.entries(map)) {
      if (config.url.endsWith(suffix)) {
        return Promise.resolve({ status: 200, data: response });
      }
    }
    throw new Error(`unexpected request to ${config.url}`);
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("EventCapturePanel", () => {
  it("offers Start while nothing is being tracked", async () => {
    respond({ "/status": IDLE });

    renderWithProviders(<EventCapturePanel />);

    expect(
      await screen.findByRole("button", { name: /start tracking/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("idle")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /stop tracking/i }),
    ).not.toBeInTheDocument();
  });

  it("shows the run and its latest events while tracking", async () => {
    respond({ "/status": running({ events: [SPIN_EVENT] }) });

    renderWithProviders(<EventCapturePanel />);

    expect(await screen.findByText("recording events")).toBeInTheDocument();
    expect(screen.getByText("FortuneOx")).toBeInTheDocument();
    expect(screen.getByText("2026-08-19_14-32-07")).toBeInTheDocument();
    // 95_000ms formatted, so the card is not showing raw milliseconds.
    expect(screen.getByText("1m 35s")).toBeInTheDocument();
    expect(screen.getByText("spin-started")).toBeInTheDocument();
    expect(screen.getByText("Spin requested")).toBeInTheDocument();
  });

  it("starts a run when Start is pressed", async () => {
    const request = respond({ "/status": IDLE, "/start": running() });

    renderWithProviders(<EventCapturePanel />);
    await userEvent.click(
      await screen.findByRole("button", { name: /start tracking/i }),
    );

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        expect.objectContaining({
          method: "POST",
          url: expect.stringContaining("/event-capture/start"),
        }),
      );
    });
  });

  it("reports the finished run after Stop", async () => {
    respond({
      "/status": running({ events: [SPIN_EVENT] }),
      "/stop": envelope({
        run_id: "2026-08-19_14-32-07",
        game: "FortuneOx",
        status: "completed",
        started_at: "2026-08-19T14:32:07Z",
        stopped_at: "2026-08-19T14:41:52Z",
        event_count: 12,
        log_path: "C:\\logs\\Game\\FortuneOx\\Logs\\FortuneOx_Client.log",
        events: [],
        errors: [],
      }),
    });

    renderWithProviders(<EventCapturePanel />);
    await userEvent.click(
      await screen.findByRole("button", { name: /stop tracking/i }),
    );

    expect(await screen.findByText(/12 events/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open it/i })).toHaveAttribute(
      "href",
      "/captures/2026-08-19_14-32-07",
    );
  });

  it("explains what to do when the game is not running", async () => {
    vi.spyOn(http, "request").mockImplementation((config) => {
      if (config.url.endsWith("/status")) {
        return Promise.resolve({ status: 200, data: IDLE });
      }
      return Promise.reject(
        new ApiError({
          message: "The log for 'FortuneOx' does not exist yet",
          code: "EVENT_CAPTURE_LOG_UNAVAILABLE",
          status: 409,
        }),
      );
    });

    renderWithProviders(<EventCapturePanel />);
    await userEvent.click(
      await screen.findByRole("button", { name: /start tracking/i }),
    );

    expect(await screen.findByText(/does not exist yet/i)).toBeInTheDocument();
    // The backend says what happened; the card says what fixes it.
    expect(
      screen.getByText(/start the game so it begins writing/i),
    ).toBeInTheDocument();
  });

  it("surfaces capture problems without hiding the run", async () => {
    respond({
      "/status": running({
        events: [SPIN_EVENT],
        errors: ["spin-started: OBS went away"],
      }),
    });

    renderWithProviders(<EventCapturePanel />);

    expect(await screen.findByText("recording events")).toBeInTheDocument();
    expect(screen.getByText(/OBS went away/)).toBeInTheDocument();
  });

  it("links to the captures page", async () => {
    respond({ "/status": IDLE });

    renderWithProviders(<EventCapturePanel />);

    expect(await screen.findByRole("link", { name: /view captures/i })).toHaveAttribute(
      "href",
      "/captures",
    );
  });
});
