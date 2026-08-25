import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { http } from "@/lib/http";
import { CapturesPage } from "@/pages/captures-page";
import { renderWithProviders } from "@/test/utils";

const META = { request_id: "r1", timestamp: "2026-08-19T09:12:44Z" };

function envelope(data, message = "ok") {
  return { success: true, message, data, error: null, meta: META };
}

const STATUS = envelope({
  active: false,
  run_id: null,
  game: null,
  started_at: null,
  duration_ms: 0,
  event_count: 0,
  recent_events: [],
  errors: [],
});

const RUNS = envelope([
  {
    run_id: "2026-08-19_14-32-07",
    game: "FortuneOx",
    status: "completed",
    started_at: "2026-08-19T14:32:07Z",
    stopped_at: "2026-08-19T14:41:52Z",
    event_count: 2,
    log_path: "C:\\logs\\Game\\FortuneOx\\Logs\\FortuneOx_Client.log",
  },
]);

const RUN = envelope({
  run_id: "2026-08-19_14-32-07",
  game: "FortuneOx",
  status: "completed",
  started_at: "2026-08-19T14:32:07Z",
  stopped_at: "2026-08-19T14:41:52Z",
  event_count: 2,
  log_path: "C:\\logs\\Game\\FortuneOx\\Logs\\FortuneOx_Client.log",
  errors: [],
  events: [
    {
      sequence: 1,
      event: "bet-changed",
      at: "2026-08-19T14:32:11",
      summary: "Bet changed to 1000.000 (5 units at denom 100.000)",
      fields: { denom: "100.000", units: "5", total_bet: "1000.000" },
      screenshot: "001_bet-changed_14-32-11.png",
      capture_error: null,
      log_line: "08/19/26 14:32:11.204 00 FortuneOx:9244 DBG: ...",
    },
    {
      sequence: 2,
      event: "reels-stopped",
      at: "2026-08-19T14:32:14",
      summary: "Reels stopped",
      fields: {},
      screenshot: null,
      capture_error: "OBS went away",
      log_line: "08/19/26 14:32:14.554 01 FortuneOx:9244 INF: ...",
    },
  ],
});

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

describe("CapturesPage", () => {
  it("says what to do when nothing has been captured yet", async () => {
    respond({ "/runs": envelope([]), "/status": STATUS });

    renderWithProviders(<CapturesPage />);

    expect(
      await screen.findByText(/start one from the event based capture card/i),
    ).toBeInTheDocument();
  });

  it("shows the newest run's events without one having to be chosen", async () => {
    respond({ "/runs/2026-08-19_14-32-07": RUN, "/runs": RUNS, "/status": STATUS });

    renderWithProviders(<CapturesPage />, { route: "/event-captures" });

    expect(await screen.findByText(/Bet changed to 1000.000/)).toBeInTheDocument();
    // The values the rule pulled out of the log line, beside the screenshot.
    expect(screen.getByText("denom")).toBeInTheDocument();
    expect(screen.getByText("100.000")).toBeInTheDocument();
  });

  it("points each screenshot at the backend's image route", async () => {
    respond({ "/runs/2026-08-19_14-32-07": RUN, "/runs": RUNS, "/status": STATUS });

    renderWithProviders(<CapturesPage />, { route: "/event-captures" });

    const image = await screen.findByAltText(/bet-changed at/i);
    expect(image).toHaveAttribute(
      "src",
      "/api/event-capture/runs/2026-08-19_14-32-07/screenshots/001_bet-changed_14-32-11.png",
    );
  });

  it("explains an event that has no screenshot", async () => {
    respond({ "/runs/2026-08-19_14-32-07": RUN, "/runs": RUNS, "/status": STATUS });

    renderWithProviders(<CapturesPage />, { route: "/event-captures" });

    // The event is still listed -- a failed capture must not hide what happened.
    expect(await screen.findByText("Reels stopped")).toBeInTheDocument();
    expect(screen.getByText("OBS went away")).toBeInTheDocument();
  });

  it("does not show the raw log line", async () => {
    respond({ "/runs/2026-08-19_14-32-07": RUN, "/runs": RUNS, "/status": STATUS });

    renderWithProviders(<CapturesPage />, { route: "/event-captures" });

    await screen.findByText(/Bet changed to 1000.000/);
    expect(screen.queryByText(/FortuneOx:9244 DBG/)).not.toBeInTheDocument();
  });

  it("lays events out two to a row", async () => {
    // Two rather than three or four: the screenshot is the point of the card,
    // and a slot game's reels stop being readable below about half the width.
    respond({ "/runs/2026-08-19_14-32-07": RUN, "/runs": RUNS, "/status": STATUS });

    renderWithProviders(<CapturesPage />, { route: "/event-captures" });

    const summary = await screen.findByText(/Bet changed to 1000.000/);
    const list = summary.closest("ul");
    expect(list.className).toMatch(/grid-cols-2/);
    expect(list.className).not.toMatch(/grid-cols-[34]/);
  });

  it("puts the description above the screenshot", async () => {
    respond({ "/runs/2026-08-19_14-32-07": RUN, "/runs": RUNS, "/status": STATUS });

    renderWithProviders(<CapturesPage />, { route: "/event-captures" });

    const summary = await screen.findByText(/Bet changed to 1000.000/);
    const card = summary.closest("li");
    const image = card.querySelector("img");
    expect(image).not.toBeNull();
    // Node.compareDocumentPosition: 4 means the image follows the summary.
    expect(summary.compareDocumentPosition(image) & 4).toBeTruthy();
  });
});
