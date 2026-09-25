import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CyclicMessagesPanel } from "@/features/cyclic-messages/cyclic-messages-panel";
import { ApiError } from "@/lib/api-error";
import { http } from "@/lib/http";
import { renderWithProviders } from "@/test/utils";

const META = { request_id: "r1", timestamp: "2026-09-08T16:36:16Z" };

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
    message_count: 0,
    sampled_count: 0,
    cycle_count: 0,
    recording: false,
    video_count: 0,
    sampling: false,
    recent_events: [],
    errors: [],
  },
  "No cyclic message run is in progress",
);

function running({
  events = [],
  errors = [],
  sampling = false,
  sampled = 0,
  recording = false,
  clips = 0,
} = {}) {
  return envelope({
    active: true,
    run_id: "2026-09-08_16-36-16",
    game: "FortuneOx",
    started_at: "2026-09-08T16:36:16Z",
    duration_ms: 95_000,
    message_count: events.length,
    sampled_count: sampled,
    cycle_count: 1,
    // Off by default now: a run spends most of its life waiting for a win,
    // and a clip is only recorded while one is being presented.
    recording,
    video_count: clips,
    sampling,
    recent_events: events,
    errors,
  });
}

/** The event the whole feature is aimed at: the amount, in shown credits. */
const GAME_PAYS = {
  sequence: 2,
  event: "cyclic-game-pays",
  at: "2026-09-08T16:36:21.982",
  summary: "Game pays 168",
  cycle: 1,
  position: 1,
  fields: { win_cents: "16800.000", denom: "100", win_credits: "168" },
  captured: true,
  screenshot: "002_cyclic-game-pays_16-36-21.png",
  capture_error: null,
  source: "log",
  log_line: "09/08/26 16:36:21.982 00 FortuneOx:12980 DBG: SpinBuffer...",
};

const SAMPLED_LINE = {
  sequence: 4,
  event: "cyclic-line-pays-shown",
  at: "2026-09-08T16:36:24.454",
  summary: "Line message on screen 0.9s into the pass",
  cycle: 1,
  position: 3,
  fields: { sample_index: "1", elapsed_seconds: "0.9" },
  captured: true,
  screenshot: "004_cyclic-line-pays-shown_16-36-24.png",
  capture_error: null,
  source: "sampled",
  log_line: "09/08/26 16:36:23.554 01 FortuneOx:12980 DBG: InputManager...",
};

/** Route by URL, the way `event-capture-panel.test.jsx` does. */
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

describe("CyclicMessagesPanel", () => {
  it("offers Start while nothing is being tracked", async () => {
    respond({ "/status": IDLE });

    renderWithProviders(<CyclicMessagesPanel />);

    expect(
      await screen.findByRole("button", { name: /start tracking/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("idle")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /stop tracking/i }),
    ).not.toBeInTheDocument();
  });

  it("shows the run and the amount the strip is displaying", async () => {
    respond({ "/status": running({ events: [GAME_PAYS] }) });

    renderWithProviders(<CyclicMessagesPanel />);

    expect(await screen.findByText("tracking messages")).toBeInTheDocument();
    expect(screen.getByText("FortuneOx")).toBeInTheDocument();
    expect(screen.getByText("2026-09-08_16-36-16")).toBeInTheDocument();
    // 95_000ms formatted, so the card is not showing raw milliseconds.
    expect(screen.getByText("1m 35s")).toBeInTheDocument();
    expect(screen.getByText("cyclic-game-pays")).toBeInTheDocument();
    // Credits, not the 16800 cents the log states.
    expect(screen.getByText("Game pays 168")).toBeInTheDocument();
  });

  it("says when it is sampling a line-message pass, and how many it sampled", async () => {
    respond({
      "/status": running({ events: [SAMPLED_LINE], sampling: true, sampled: 3 }),
    });

    renderWithProviders(<CyclicMessagesPanel />);

    expect(await screen.findByText("sampling the pass")).toBeInTheDocument();
    expect(screen.getByText(/3 sampled/)).toBeInTheDocument();
  });

  it("says a clip is being recorded only while a win is being presented", async () => {
    respond({ "/status": running({ events: [GAME_PAYS], recording: true }) });

    renderWithProviders(<CyclicMessagesPanel />);

    expect(await screen.findByText("recording this win")).toBeInTheDocument();
  });

  it("waits for a win rather than recording the idle strip", async () => {
    respond({ "/status": running({ events: [GAME_PAYS], clips: 2 }) });

    renderWithProviders(<CyclicMessagesPanel />);

    expect(await screen.findByText("2 clips saved")).toBeInTheDocument();
    expect(screen.queryByText("recording this win")).not.toBeInTheDocument();
  });

  it("does not claim to be sampling once the pass has ended", async () => {
    respond({ "/status": running({ events: [GAME_PAYS], sampling: false }) });

    renderWithProviders(<CyclicMessagesPanel />);

    expect(await screen.findByText("tracking messages")).toBeInTheDocument();
    expect(screen.queryByText("sampling the pass")).not.toBeInTheDocument();
  });

  it("starts a run when Start is pressed", async () => {
    const request = respond({ "/status": IDLE, "/start": running() });

    renderWithProviders(<CyclicMessagesPanel />);
    await userEvent.click(
      await screen.findByRole("button", { name: /start tracking/i }),
    );

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        expect.objectContaining({
          method: "POST",
          url: expect.stringContaining("/cyclic-messages/start"),
        }),
      );
    });
  });

  it("reports the finished run after Stop", async () => {
    respond({
      "/status": running({ events: [GAME_PAYS] }),
      "/stop": envelope({
        run_id: "2026-09-08_16-36-16",
        game: "FortuneOx",
        status: "completed",
        started_at: "2026-09-08T16:36:16Z",
        stopped_at: "2026-09-08T16:41:52Z",
        message_count: 14,
        sampled_count: 7,
        cycle_count: 2,
        log_path: "C:\\logs\\Game\\FortuneOx\\Logs\\FortuneOx_Client.log",
        videos: [
          {
            file_name: "cycle-001_win-video_16-36-21.mp4",
            source_path: null,
            error: null,
            cycle: 1,
            started_at: "2026-09-08T16:36:21.982",
            stopped_at: "2026-09-08T16:36:30.305",
            closed_by: "cyclic-line-pays-cycle-finished",
          },
        ],
        events: [],
        errors: [],
      }),
    });

    renderWithProviders(<CyclicMessagesPanel />);
    await userEvent.click(
      await screen.findByRole("button", { name: /stop tracking/i }),
    );

    expect(await screen.findByText(/14 messages/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open it/i })).toHaveAttribute(
      "href",
      "/cyclic-messages/2026-09-08_16-36-16",
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
          code: "CYCLIC_MESSAGES_LOG_UNAVAILABLE",
          status: 409,
        }),
      );
    });

    renderWithProviders(<CyclicMessagesPanel />);
    await userEvent.click(
      await screen.findByRole("button", { name: /start tracking/i }),
    );

    expect(await screen.findByText(/does not exist yet/i)).toBeInTheDocument();
    // The backend says what happened; the card says what fixes it.
    expect(
      screen.getByText(/start the game so it begins writing/i),
    ).toBeInTheDocument();
  });

  it("surfaces problems without hiding the run", async () => {
    respond({
      "/status": running({
        events: [GAME_PAYS],
        errors: ["video: OBS went away"],
      }),
    });

    renderWithProviders(<CyclicMessagesPanel />);

    expect(await screen.findByText("tracking messages")).toBeInTheDocument();
    expect(screen.getByText(/OBS went away/)).toBeInTheDocument();
  });
});
