import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReplayPanel } from "@/features/replay/replay-panel";
import { ApiError } from "@/lib/api-error";
import { http } from "@/lib/http";
import { renderWithProviders } from "@/test/utils";

const META = { request_id: "r1", timestamp: "2026-09-17T09:12:44Z" };

function envelope(data, message = "3 of 3 windows are ready") {
  return { success: true, message, data, error: null, meta: META };
}

function step(key, label, state, extra = {}) {
  return {
    key,
    label,
    window: "devtool",
    state,
    target: null,
    screen_x: null,
    screen_y: null,
    confirmed: state === "completed",
    detail: null,
    started_at: null,
    finished_at: null,
    duration_ms: 120,
    error: null,
    error_code: null,
    ...extra,
  };
}

function shot({ moment = "before-spin", blank = false, attempts = 1 } = {}) {
  return {
    moment,
    source_name: "FortuneOx Window",
    file_name: `replay-abc123-${moment}-1.png`,
    file_path: `C:/obs-captured-files/replay/replay-abc123-${moment}-1.png`,
    attempts,
    blank,
  };
}

function log(sequence, message, { level = "info", stepKey = null } = {}) {
  return {
    sequence,
    at: "2026-09-17T09:12:41",
    level,
    step: stepKey,
    message,
  };
}

function run({
  state = "completed",
  steps,
  logs,
  screenshots = [shot(), shot({ moment: "after-spin" })],
  message = "Replayed the latest game play",
} = {}) {
  return {
    run_id: "abc123",
    game: "FortuneOx",
    state,
    message,
    screenshots,
    steps: steps ?? [
      step("open-devtool", "Open the DevTool window", "completed"),
      step("connect", "Connect the I/O hub", "completed"),
    ],
    logs: logs ?? [log(0, "starting: 11 steps on FortuneOx")],
    started_at: "2026-09-17T09:12:40",
    finished_at: state === "running" ? null : "2026-09-17T09:12:52",
    duration_ms: 11800,
    error: null,
    error_code: null,
  };
}

function status({
  devtool = "ready",
  admin = "not_found",
  game = "ready",
  menuReachable = true,
  menuProbed = true,
  menuOpen = false,
  gameExit = true,
  exits = true,
  running = false,
  record = null,
} = {}) {
  return envelope({
    running,
    game: "FortuneOx",
    windows: [
      {
        window: "devtool",
        state: devtool,
        title: "DevTool",
        hwnd: 132992,
        client_width: 755,
        client_height: 568,
        controls: ["Connect", "Attendant Key"],
      },
      {
        window: "system-admin",
        state: admin,
        title: "System Admin",
        hwnd: null,
        client_width: 0,
        client_height: 0,
        controls: [],
      },
      {
        window: "game",
        state: game,
        title: "FortuneOx",
        hwnd: 67628,
        client_width: 1080,
        client_height: 1849,
        controls: [],
      },
    ],
    menu: {
      reachable: menuReachable,
      probed: menuProbed,
      cdp_url: "http://127.0.0.1:9999",
      page_url: menuOpen ? "http://localhost:9002/home/homepage" : null,
      page_title: menuOpen ? "System Admin" : null,
      labels: menuOpen ? ["Events / History", "Game Play", "Exit"] : [],
      label_count: menuOpen ? 3 : 0,
      error: menuReachable
        ? null
        : "No debuggable browser answered at http://127.0.0.1:9999/json/list",
    },
    run: record,
    exits_after_screenshot: exits,
    game_exit_target: "exit_gameplay",
    game_exit_configured: gameExit,
    game_spin_target: "spin",
    game_spin_configured: gameExit,
  });
}

/**
 * Mock the transport with a status that can change between polls — which is
 * the whole shape of this panel: the POST only starts the sequence, and every
 * later fact about it arrives on `/status`.
 */
function serve({ statuses, started = run({ state: "running" }) }) {
  const queue = Array.isArray(statuses) ? [...statuses] : [statuses];
  return vi.spyOn(http, "request").mockImplementation(({ method }) => {
    if (method === "POST")
      return Promise.resolve({ status: 200, data: envelope(started) });
    const next = queue.length > 1 ? queue.shift() : queue[0];
    return Promise.resolve({ status: 200, data: next });
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ReplayPanel", () => {
  it("starts the sequence from one button and follows it on the status", async () => {
    // The button posts once; everything the panel then shows comes from the
    // poll, because the run is still walking when the request has answered.
    const request = serve({
      statuses: [
        status(),
        status({ running: true, record: run({ state: "running" }) }),
        status({ record: run() }),
      ],
    });

    renderWithProviders(<ReplayPanel />);
    await screen.findByText("FortuneOx");

    await userEvent.click(screen.getByRole("button", { name: "Replay" }));

    await waitFor(() =>
      expect(screen.getByText("Connect the I/O hub")).toBeInTheDocument(),
    );
    const posts = request.mock.calls.filter(([config]) => config.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0][0].url).toContain("/replay/run");
  });

  it("shows a failed run's steps rather than an error alert", async () => {
    // A step that failed comes back as *data*, so the panel has to render the
    // record -- the point of the run is how far it got.
    serve({
      statuses: status({
        record: run({
          state: "failed",
          message: "Replay stopped at press the attendant key",
          screenshots: [],
          steps: [
            step("attendant-key", "Press the attendant key", "failed", {
              confirmed: false,
              error: "the System Admin window never opened within 20.0s",
              error_code: "REPLAY_STEP_NOT_CONFIRMED",
            }),
            step("events-history", "Open Events / History", "pending", {
              confirmed: false,
              duration_ms: null,
            }),
          ],
        }),
      }),
    });

    renderWithProviders(<ReplayPanel />);

    await screen.findByText("failed");
    expect(
      screen.getByText(/the System Admin window never opened/),
    ).toBeInTheDocument();
    // The unreached step is still listed, which is how "pending" stays a
    // different fact from "skipped".
    expect(screen.getByText("Open Events / History")).toBeInTheDocument();
  });

  it("marks a completed but unproven step as unconfirmed", async () => {
    // One step of the eleven has nothing it can read back -- what View starts
    // happens in the game's window. Rendering it as a plain success would
    // claim a confirmation nothing checked.
    serve({
      statuses: status({
        record: run({
          steps: [
            step("view-latest", "View the latest record", "completed", {
              confirmed: false,
              detail: "clicked the first of 3 'View' on the page",
            }),
          ],
        }),
      }),
    });

    renderWithProviders(<ReplayPanel />);

    await waitFor(() => expect(screen.getByText(/unconfirmed —/)).toBeInTheDocument());
  });

  it("shows the screenshot while the run is still walking", async () => {
    // The ask this was built for: the picture is what a reader of a replay
    // came for, and the run goes on to press two more buttons after taking it.
    serve({
      statuses: status({
        running: true,
        record: run({
          state: "running",
          message: "Exit the game play view (10 of 11)",
        }),
      }),
    });

    renderWithProviders(<ReplayPanel />);

    const pictures = await screen.findAllByRole("img", { name: /replayed game/i });
    // Served as files, not carried on a record that is polled once a second.
    expect(pictures[0]).toHaveAttribute(
      "src",
      "/api/replay/screenshot/replay-abc123-before-spin-1.png",
    );
    // Both moments are named, because which is which is the whole point of
    // taking two.
    expect(screen.getByText("Before the spin")).toBeInTheDocument();
    expect(screen.getByText("After the spin")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
  });

  it("shows the run's own log, newest last", async () => {
    // What "live logs of all the steps" means in the card: the steps say what
    // it is doing, the log says what it found while doing it.
    serve({
      statuses: status({
        record: run({
          logs: [
            log(0, "starting: 11 steps on FortuneOx"),
            log(1, "waiting up to 90s for the server to return the game-play records", {
              stepKey: "game-play",
            }),
            log(2, "replay-abc123-1.png came back empty; retaking it", {
              level: "warning",
              stepKey: "screenshot",
            }),
          ],
        }),
      }),
    });

    renderWithProviders(<ReplayPanel />);

    const entries = await screen.findByRole("list", { name: "Replay progress log" });
    expect(entries).toHaveTextContent("starting: 11 steps on FortuneOx");
    expect(entries).toHaveTextContent("waiting up to 90s");
    expect(entries).toHaveTextContent("came back empty; retaking it");
  });

  it("says so when the screenshot came back empty", async () => {
    // A black frame looks like a result, and OBS reports writing it happily.
    serve({
      statuses: status({
        record: run({
          state: "failed",
          screenshots: [shot({ blank: true }), shot({ moment: "after-spin" })],
        }),
      }),
    });

    renderWithProviders(<ReplayPanel />);

    expect(await screen.findByText(/came back empty/)).toBeInTheDocument();
  });

  it("will not start a second run while one is walking", async () => {
    // Two would fight over the cursor and the foreground window, and the
    // backend refuses -- so the button should not offer it.
    serve({
      statuses: status({ running: true, record: run({ state: "running" }) }),
    });

    renderWithProviders(<ReplayPanel />);
    await screen.findByText("FortuneOx");

    expect(screen.getByRole("button", { name: "Replay" })).toBeDisabled();
  });

  it("explains an unreachable window instead of just badging it", async () => {
    serve({ statuses: status({ devtool: "access_denied" }) });

    renderWithProviders(<ReplayPanel />);

    expect(await screen.findByText("access_denied")).toBeInTheDocument();
    expect(screen.getByText(/Restart the backend elevated/)).toBeInTheDocument();
  });

  it("stays quiet about an unmeasured in-game Exit while exiting is off", async () => {
    // With exiting off the run never presses that button, so a game which has
    // not measured it is not missing anything.
    serve({ statuses: status({ gameExit: false, exits: false }) });

    renderWithProviders(<ReplayPanel />);
    await screen.findByText("FortuneOx");

    expect(screen.queryByText(/cannot be exited yet/)).not.toBeInTheDocument();
  });

  it("flags an unmeasured in-game Exit, and says why it is not obvious", async () => {
    serve({ statuses: status({ gameExit: false, exits: true }) });

    renderWithProviders(<ReplayPanel />);

    expect(await screen.findByText(/cannot be exited yet/)).toBeInTheDocument();
    expect(
      screen.getByText(/only exists while a replay is on screen/),
    ).toBeInTheDocument();
  });

  it("explains a browser whose debugging port did not answer", async () => {
    // The menu is a web page driven through that port, so this is the one
    // thing about it a reader has to be told rather than left to guess.
    serve({ statuses: status({ menuReachable: false }) });

    renderWithProviders(<ReplayPanel />);

    expect(
      await screen.findByText(/No debuggable browser answered/),
    ).toBeInTheDocument();
    // Named twice -- as the address, and again inside the error it returned.
    expect(screen.getAllByText(/127\.0\.0\.1:9999/).length).toBeGreaterThan(0);
  });

  it("does not call the menu unreachable when nothing asked it", async () => {
    // While a run walks, the page is not re-read -- so `reachable: false` here
    // means "not probed", and reporting it as a failure would be a lie about
    // the one thing the run is in the middle of using.
    serve({
      statuses: status({
        running: true,
        menuReachable: false,
        menuProbed: false,
        record: run({ state: "running" }),
      }),
    });

    renderWithProviders(<ReplayPanel />);
    await screen.findByText("FortuneOx");

    expect(screen.queryByText(/did not answer/)).not.toBeInTheDocument();
  });

  it("reports an open menu's page, and stays quiet about a closed one", async () => {
    serve({ statuses: status({ admin: "ready", menuOpen: true }) });

    renderWithProviders(<ReplayPanel />);

    expect(await screen.findByText(/localhost:9002/)).toBeInTheDocument();
    expect(screen.getByText(/showing 3 labels/)).toBeInTheDocument();
  });

  it("does not poll the status endpoint while nothing is running", async () => {
    const request = serve({ statuses: status() });

    renderWithProviders(<ReplayPanel />);
    await screen.findByText("FortuneOx");
    const calls = request.mock.calls.length;

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(request.mock.calls).toHaveLength(calls);
  });

  it("polls while a sequence is walking", async () => {
    // The one thing that earns a poll: a record being written a step at a time.
    const request = serve({
      statuses: status({ running: true, record: run({ state: "running" }) }),
    });

    renderWithProviders(<ReplayPanel />);
    await screen.findByText("FortuneOx");
    const calls = request.mock.calls.length;

    await waitFor(() => expect(request.mock.calls.length).toBeGreaterThan(calls), {
      timeout: 3_000,
    });
  });

  it("surfaces a refusal to start as an error", async () => {
    // A second run, or a host with no Windows API: the sequence never began,
    // so there is no record to show and the alert is the whole answer.
    vi.spyOn(http, "request").mockImplementation(({ method }) =>
      method === "POST"
        ? Promise.reject(
            new ApiError({
              message: "A replay sequence is already in progress",
              code: "REPLAY_ALREADY_RUNNING",
              status: 409,
            }),
          )
        : Promise.resolve({ status: 200, data: status() }),
    );

    renderWithProviders(<ReplayPanel />);
    await screen.findByText("FortuneOx");
    await userEvent.click(screen.getByRole("button", { name: "Replay" }));

    expect(await screen.findByText(/already in progress/)).toBeInTheDocument();
  });

  it("says so when the backend will not serve the picture", async () => {
    // The record says a screenshot was written and not blank, so a broken
    // image icon leaves a reader unable to tell that from "the replay itself
    // came out empty".
    serve({ statuses: status({ record: run({ screenshots: [shot()] }) }) });

    renderWithProviders(<ReplayPanel />);
    const picture = await screen.findByRole("img", { name: /replayed game/i });
    fireEvent.error(picture);

    expect(await screen.findByText(/would not serve it/)).toBeInTheDocument();
    expect(
      screen.queryByRole("img", { name: /replayed game/i }),
    ).not.toBeInTheDocument();
  });
});
