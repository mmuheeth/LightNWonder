import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { http } from "@/lib/http";
import { GameConfigPage } from "@/pages/game-config-page";
import { renderWithProviders } from "@/test/utils";

const META = { request_id: "r1", timestamp: "2026-08-24T19:06:03Z" };

function envelope(data, message = "ok") {
  return { success: true, message, data, error: null, meta: META };
}

const LOGGED = "FortuneOx-1101YX-1c-90";
const OTHER = "FortuneOx-1102RX-100c-90";

const LOG_LINE =
  "08/24/26 19:06:03.063 01 FortuneOx:22372 DBG: [WagerGameApp.UpdatePayTable] " +
  `current denom[1.000] current paytableId[${LOGGED}] ` +
  "current supported denoms[1.000,2.000]";

function paytable(overrides = {}) {
  return envelope({
    game: "FortuneOx",
    label: "FortuneOx",
    paytable_id: LOGGED,
    directory: `C:\\re\\games\\FortuneOx\\GameConfig\\${LOGGED}`,
    available: [LOGGED, OTHER],
    source: {
      origin: "log",
      log_path: "C:\\logs\\Game\\FortuneOx\\Logs\\FortuneOx_Client.log",
      log_line: LOG_LINE,
      logged_at: "2026-08-24T19:06:03.063",
      denomination: "1.000",
      supported_denominations: ["1.000", "5.000", "100.000"],
    },
    identity: {
      path: "C:\\re\\gameConfig.cfg",
      game_type: "FortuneOx",
      game_id: LOGGED,
      display_game_id: "FortuneOx-40-900 88.01% MinTotalBet:88",
      game_pct: 88.01,
      min_game_pct: 88.01,
      game_base_pct: 82.9,
      min_game_base_pct: 82.9,
      number_of_lines: 40,
      min_total_bet: 88,
      max_bets: [88, 880],
      denominations: [1, 5],
    },
    math: {
      path: "C:\\re\\math.xml",
      game_id: "1101YX_1c_90",
      game_pct: 88.01,
      min_game_pct: 88.01,
      game_base_pct: 82.9,
      min_game_base_pct: 82.9,
      defaults: {
        symbol_set_id: "SymbolSet_Main",
        reel_strip_set_id: "Reels_BG_0",
        paytable_id: "Paytable_Main",
        payline_set_id: "40",
        initial_stops: [0, 28],
      },
      symbols: [
        {
          code: "WC",
          name: "Wild",
          role: "wild",
          substitutes: ["AA", "FF"],
          top_pay: 250,
          reel_stops: 62,
          total_stops: 431,
          strips: 36,
        },
        {
          code: "AA",
          name: "Ox",
          role: "regular",
          substitutes: [],
          top_pay: 50,
          reel_stops: 74,
          total_stops: 380,
          strips: 36,
        },
        {
          code: "FF",
          name: "King",
          role: "regular",
          substitutes: [],
          top_pay: 15,
          reel_stops: 76,
          total_stops: 393,
          strips: 36,
        },
        {
          code: "FG",
          name: "Free Games",
          role: "scatter",
          substitutes: [],
          top_pay: null,
          reel_stops: 0,
          total_stops: 48,
          strips: 6,
        },
        {
          code: "SO",
          name: "SO (feature)",
          role: "scatter",
          substitutes: [],
          top_pay: null,
          reel_stops: 0,
          total_stops: 0,
          strips: 0,
          on_reels: false,
        },
      ],
      reel_strip_sets: [
        {
          identifier: "Reels_BG_0",
          strip_ids: ["Reels_BG_0_0", "Reels_BG_0_1"],
          visible_heights: [3, 3],
          is_default: true,
        },
        {
          identifier: "Reels_FG",
          strip_ids: ["Reels_FG_0"],
          visible_heights: [3],
          is_default: false,
        },
      ],
      reel_strips: [
        {
          identifier: "Reels_BG_0_0",
          set_id: "Reels_BG_0",
          reel_index: 0,
          symbol_set_id: "SymbolSet_Main",
          length: 2,
          symbols: ["AA", "WC"],
          weights: [10, 10],
          truncated: false,
        },
        {
          identifier: "Reels_BG_0_1",
          set_id: "Reels_BG_0",
          reel_index: 1,
          symbol_set_id: "SymbolSet_Main",
          length: 1,
          symbols: ["FF"],
          weights: [1],
          truncated: false,
        },
        {
          identifier: "Reels_FG_0",
          set_id: "Reels_FG",
          reel_index: 0,
          symbol_set_id: "SymbolSet_Main",
          length: 1,
          symbols: ["FG"],
          weights: [1],
          truncated: false,
        },
      ],
      payline_combos: [
        {
          combo_id: 1,
          combo_set: "PaylineComboSet_Main",
          group: 100,
          value: 250,
          symbols: ["WC", "WC", "WC"],
          names: ["Wild", "Wild", "Wild"],
          match_length: 3,
        },
        {
          combo_id: 2,
          combo_set: "PaylineComboSet_Main",
          group: 100,
          value: 10,
          symbols: ["AA", "AA", "ANY"],
          names: ["Ox", "Ox", null],
          match_length: 2,
        },
      ],
      pay_lengths: [3, 2],
      pay_table: [
        {
          codes: ["WC"],
          names: ["Wild"],
          values: [250, null],
          top_pay: 250,
        },
        {
          codes: ["AA", "FF"],
          names: ["Ox", "King"],
          values: [null, 10],
          top_pay: 10,
        },
      ],
      scatter_combos: [
        {
          combo_id: 31,
          combo_set: "CountScatterComboSet_Main",
          group: 200,
          value: 2,
          symbols: ["FG"],
          names: ["Free Games"],
          min_symbols: 3,
          max_symbols: 3,
          base_multiplier: "TotalBet",
          bonus_code: 1,
        },
      ],
      paytables: [
        {
          identifier: "Paytable_Main",
          combo_set_ids: ["PaylineComboSet_Main"],
        },
      ],
    },
    win_geometry: {
      path: "C:\\re\\winGeometry.xml",
      payline_set_id: "40",
      resolved_from: "game_config",
      line_count: 2,
      sets: [
        { payline_set_id: "40", line_count: 2, is_applicable: true },
        { payline_set_id: "5", line_count: 1, is_applicable: false },
      ],
      paylines: [
        {
          line: 1,
          number: 0,
          elements: [
            [0, 1],
            [1, 1],
          ],
          grid: [
            [2, 1],
            [2, 2],
          ],
        },
        {
          line: 2,
          number: 1,
          elements: [
            [0, 0],
            [1, 0],
          ],
          grid: [
            [1, 1],
            [1, 2],
          ],
        },
      ],
      error: null,
    },
    ...overrides,
  });
}

const CATALOG = envelope({
  active_game: "FortuneOx",
  games: [{ game: "FortuneOx", label: "FortuneOx", process: "FortuneOx.exe" }],
});

/** Answer both requests the page makes: its own, and the game selector's. */
function respond(view = paytable()) {
  return vi.spyOn(http, "request").mockImplementation((config) => {
    if (config.url.endsWith("/games/")) {
      return Promise.resolve({ status: 200, data: CATALOG });
    }
    if (config.url.endsWith("/paytable/")) {
      return Promise.resolve({
        status: 200,
        data: typeof view === "function" ? view(config) : view,
      });
    }
    throw new Error(`unexpected request to ${config.url}`);
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GameConfigPage", () => {
  it("leads with the paytable id and the log line that produced it", async () => {
    respond();

    renderWithProviders(<GameConfigPage />, { route: "/game-config" });

    // The id is the whole join between a running game and a folder on disk, so
    // it leads at full size. Scoped to the paragraph because the same id is also
    // an <option> in the dropdown below.
    expect(await screen.findByText(LOGGED, { selector: "p" })).toBeInTheDocument();
    // The evidence is rendered but collapsed -- wanted when the page looks
    // wrong, noise the rest of the time.
    expect(screen.getByText("Where this came from")).toBeInTheDocument();
    expect(screen.getByText(LOG_LINE).closest("details")).not.toBeNull();
  });

  it("shows the denomination in play and the ones it can move to", async () => {
    respond();

    renderWithProviders(<GameConfigPage />, { route: "/game-config" });

    // The denomination is what selects the paytable on this cabinet, so the
    // supported set is the maths this session can reach without a restart.
    expect(await screen.findByText("Current denom")).toBeInTheDocument();
    expect(screen.getByText("1.000, 5.000, 100.000")).toBeInTheDocument();
  });

  it("names the symbol codes wherever they appear", async () => {
    respond();

    renderWithProviders(<GameConfigPage />, { route: "/game-config" });

    // The name is the one thing not read from the game's own files -- math.xml
    // carries no display text in any element -- and it travels with the code
    // into the pay table and the reel strips.
    expect(await screen.findByText("Ox / King")).toBeInTheDocument();
    expect(screen.getAllByText("Wild").length).toBeGreaterThan(0);
  });

  it("shows the four cards, in reading order", async () => {
    respond();

    renderWithProviders(<GameConfigPage />, { route: "/game-config" });

    await screen.findByText("Current paytable");
    const titles = screen
      .getAllByText(/^(Current paytable|Win geometry|Payline combos|Reel strips)$/)
      .map((node) => node.textContent);

    // Which paytable, where its lines run, what they pay, what is on each reel.
    expect(titles).toEqual([
      "Current paytable",
      "Win geometry",
      "Payline combos",
      "Reel strips",
    ]);
  });

  it("lays the line pays out as a paytable, a column per run length", async () => {
    respond();

    renderWithProviders(<GameConfigPage />, { route: "/game-config" });

    // A combo is one symbol repeated with an ANY tail, so the readable shape is
    // a row per symbol and a column per run -- not the file's own ordering.
    expect(await screen.findByText("x3")).toBeInTheDocument();
    expect(screen.getByText("x2")).toBeInTheDocument();
    expect(screen.getByText("250")).toBeInTheDocument();

    // Symbols paying alike share one row rather than repeating it.
    expect(screen.getByText("Ox / King")).toBeInTheDocument();
  });

  it("shows only the line pays, not the scatter awards", async () => {
    respond();

    renderWithProviders(<GameConfigPage />, { route: "/game-config" });

    // They answer a different question -- a count anywhere on screen, not a run
    // along a line -- and the page no longer asks it. The response still
    // carries them.
    await screen.findByText("Payline combos");
    expect(screen.queryByText("TotalBet")).not.toBeInTheDocument();
  });

  it("names the payline set in play and where that came from", async () => {
    respond();

    renderWithProviders(<GameConfigPage />, { route: "/game-config" });

    // The same math.xml ships in folders that play 5, 20 and 40 lines, so the
    // authority for the number matters as much as the number.
    expect(
      await screen.findByText(/this paytable's NumberOfLines/),
    ).toBeInTheDocument();
    expect(screen.getByText("2 lines")).toBeInTheDocument();
    expect(screen.getByText("Line 1")).toBeInTheDocument();
    expect(screen.getByText("Line 2")).toBeInTheDocument();
  });

  it("opens the reel strips on the set the base game spins", async () => {
    respond();

    renderWithProviders(<GameConfigPage />, { route: "/game-config" });

    const chooser = await screen.findByRole("combobox", { name: "Reel strip set" });
    expect(chooser).toHaveValue("Reels_BG_0");
    expect(screen.getByText("Reel 1")).toBeInTheDocument();
    expect(screen.getByText("Reel 2")).toBeInTheDocument();
    expect(screen.queryByText("Reels_FG_0 · 1")).not.toBeInTheDocument();
  });

  it("asks the backend for another paytable when one is picked", async () => {
    const request = respond();

    renderWithProviders(<GameConfigPage />, { route: "/game-config" });

    const chooser = await screen.findByRole("combobox", { name: "Paytable" });
    await userEvent.selectOptions(chooser, OTHER);

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        expect.objectContaining({
          url: expect.stringContaining("/paytable/"),
          params: { paytable_id: OTHER },
        }),
      );
    });
  });

  it("keeps the rest of the page when the geometry file is unreadable", async () => {
    respond(
      paytable({
        win_geometry: {
          path: "C:\\re\\winGeometry.xml",
          payline_set_id: "40",
          resolved_from: "game_config",
          line_count: null,
          sets: [],
          paylines: [],
          error: "No win geometry file at C:\\re\\winGeometry.xml",
        },
      }),
    );

    renderWithProviders(<GameConfigPage />, { route: "/game-config" });

    expect(await screen.findByText("No lines to draw")).toBeInTheDocument();
    // The other cards are still true, so they are still shown.
    expect(screen.getByText("Ox / King")).toBeInTheDocument();
    expect(screen.getAllByText("250").length).toBeGreaterThan(0);
  });

  it("surfaces a backend failure with a way back to the logged paytable", async () => {
    vi.spyOn(http, "request").mockImplementation((config) => {
      if (config.url.endsWith("/games/")) {
        return Promise.resolve({ status: 200, data: CATALOG });
      }
      return Promise.reject(
        Object.assign(new Error("boom"), {
          isAxiosError: true,
          response: {
            status: 409,
            data: {
              success: false,
              message: "The game config declares no 'game_config' directory",
              data: null,
              error: { code: "PAYTABLE_UNAVAILABLE", details: [] },
              meta: META,
            },
          },
        }),
      );
    });

    renderWithProviders(<GameConfigPage />, { route: "/game-config" });

    expect(
      await screen.findByText(/declares no 'game_config' directory/),
    ).toBeInTheDocument();
  });
});
