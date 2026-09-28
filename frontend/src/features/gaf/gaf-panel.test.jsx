import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GafPanel } from "@/features/gaf/gaf-panel";
import { http } from "@/lib/http";
import { renderWithProviders } from "@/test/utils";

const META = { request_id: "r1", timestamp: "2026-09-24T09:12:44Z" };

function envelope(data, message = "ok") {
  return { success: true, message, data, error: null, meta: META };
}

function status(state = "disconnected", overrides = {}) {
  return envelope(
    {
      state,
      game: "HuffNPuffHighRise",
      server_url: "http://127.0.0.1:8270",
      host: "127.0.0.1",
      port: 9090,
      game_type: "BallyStyle",
      gdk_version: "12",
      connected: state === "ready",
      query_files: [],
      object_count: state === "ready" ? 412 : null,
      idle_state: state === "ready" ? "stateIdleWithCredits" : null,
      detail: `GAF is ${state}`,
      ...overrides,
    },
    `GAF is ${state}`,
  );
}

const LOSING_SPIN = envelope({
  game: "HuffNPuffHighRise",
  pressed: true,
  outcome: "idle",
  settled: true,
  idle_state: "stateIdleWithCredits",
  game_state: "stateIdle",
  win_offered: false,
  meters: { credit: "$995.80", bet: "88", win: null },
  elapsed_ms: 12800,
  detail: "Spin finished in 12.8s with no win (stateIdleWithCredits).",
});

const WINNING_SPIN = envelope({
  game: "HuffNPuffHighRise",
  pressed: true,
  outcome: "win_offered",
  settled: true,
  idle_state: "statePlaying",
  game_state: "stateReelSpinDone",
  win_offered: true,
  meters: { credit: "$995.80", bet: "88", win: "$0.75" },
  elapsed_ms: 9400,
  detail: "Spin finished in 9.4s with a win waiting to be collected.",
});

/** Route the one GET the panel makes; POSTs resolve to whatever is passed. */
function mockApi({ state = "disconnected", post = LOSING_SPIN } = {}) {
  return vi.spyOn(http, "request").mockImplementation((config) => {
    if (config.url.endsWith("/status")) {
      return Promise.resolve({ status: 200, data: status(state) });
    }
    return Promise.resolve({ status: 200, data: post });
  });
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("GafPanel", () => {
  it("spins without being told which game or where it listens", async () => {
    // The whole point of reading them off the active game's config: a spin is
    // a request with no body at all.
    const request = mockApi();
    renderWithProviders(<GafPanel />);

    await userEvent.click(await screen.findByRole("button", { name: /^spin$/i }));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        expect.objectContaining({
          method: "POST",
          url: "/api/gaf/spin",
          data: {},
        }),
      );
    });
    expect(await screen.findByText("no win")).toBeInTheDocument();
  });

  it("shows a winning spin as finished, with the win still to collect", async () => {
    // A win holds the machine in play, so this is a completed spin and not a
    // stuck one — reading it as a failure is the trap the backend avoids.
    mockApi({ post: WINNING_SPIN });
    renderWithProviders(<GafPanel />);

    await userEvent.click(await screen.findByRole("button", { name: /^spin$/i }));

    expect(await screen.findByText("win to collect")).toBeInTheDocument();
    expect(await screen.findByText("$0.75")).toBeInTheDocument();
  });

  it("reports nothing to collect without looking like a failure", async () => {
    mockApi({
      post: envelope({
        game: "HuffNPuffHighRise",
        button: "TakeWinButton",
        interactable: false,
        pressed: false,
        settled: false,
        idle_state: "stateIdleWithCredits",
        meters: null,
        elapsed_ms: 190,
        detail: "TakeWinButton is not interactable, so there is nothing to collect.",
      }),
    });
    renderWithProviders(<GafPanel />);

    await userEvent.click(await screen.findByRole("button", { name: /take win/i }));

    expect(await screen.findByText("nothing to collect")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("names the standing fix when the automation server is absent", async () => {
    // The failure most likely to be mistaken for a bug in this app: nothing
    // here starts NRobot, so the card has to say who does.
    mockApi({ state: "unreachable" });
    renderWithProviders(<GafPanel />);

    expect(await screen.findByText("unreachable")).toBeInTheDocument();
    expect(await screen.findByText(/NRobotStartUpScript/i)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /^spin$/i })).toBeDisabled();
  });

  it("says a game that declares no gaf block cannot be driven", async () => {
    mockApi({ state: "not_configured" });
    renderWithProviders(<GafPanel />);

    expect(await screen.findByText("not_configured")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /take win/i })).toBeDisabled();
  });

  it("surfaces a rejected spin rather than looking successful", async () => {
    vi.spyOn(http, "request").mockImplementation((config) => {
      if (config.url.endsWith("/status")) {
        return Promise.resolve({ status: 200, data: status("ready") });
      }
      return Promise.reject({
        response: {
          status: 502,
          data: {
            success: false,
            message: "PRESSMECHANICALSPINBUTTON: the game declined",
            data: null,
            error: { code: "GAF_KEYWORD_FAILED", details: [] },
            meta: META,
          },
        },
      });
    });

    renderWithProviders(<GafPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /^spin$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/the game declined/i);
  });

  it("offers an override when the game is still playing", async () => {
    // The backend refuses to spin on top of a bonus or an uncollected win;
    // the override belongs beside that refusal, not on a standing checkbox.
    const request = vi.spyOn(http, "request").mockImplementation((config) => {
      if (config.url.endsWith("/status")) {
        return Promise.resolve({ status: 200, data: status("ready") });
      }
      if (config.data?.force) {
        return Promise.resolve({ status: 200, data: WINNING_SPIN });
      }
      return Promise.reject({
        response: {
          status: 409,
          data: {
            success: false,
            message: "HuffNPuffHighRise is still in statePlaying",
            data: null,
            error: { code: "GAF_NOT_IDLE", details: [] },
            meta: META,
          },
        },
      });
    });

    renderWithProviders(<GafPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /^spin$/i }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    const anyway = await screen.findByRole("button", { name: /spin anyway/i });
    await userEvent.click(anyway);

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        expect.objectContaining({ url: "/api/gaf/spin", data: { force: true } }),
      );
    });
  });

  it("does not offer the override for an ordinary failure", async () => {
    vi.spyOn(http, "request").mockImplementation((config) => {
      if (config.url.endsWith("/status")) {
        return Promise.resolve({ status: 200, data: status("ready") });
      }
      return Promise.reject({
        response: {
          status: 502,
          data: {
            success: false,
            message: "the game declined",
            data: null,
            error: { code: "GAF_KEYWORD_FAILED", details: [] },
            meta: META,
          },
        },
      });
    });

    renderWithProviders(<GafPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /^spin$/i }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /spin anyway/i })).toBeNull();
  });

  it("offers disconnect only while a session is held", async () => {
    mockApi({ state: "ready" });
    renderWithProviders(<GafPanel />);

    expect(await screen.findByText("ready")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /disconnect/i })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /^connect$/i })).toBeNull();
  });

  // Last in the file on purpose: it swaps in fake timers, and a full-suite
  // run is where a leaked clock shows up as another case timing out.
  it("does not poll the status endpoint automatically", async () => {
    vi.useFakeTimers();
    const request = mockApi();

    renderWithProviders(<GafPanel />);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("disconnected")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(15_000));

    expect(
      request.mock.calls.filter(([config]) => config.url.endsWith("/status")),
    ).toHaveLength(1);
  });
});
