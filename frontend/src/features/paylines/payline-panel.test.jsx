import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PaylinePanel } from "@/features/paylines/payline-panel";
import { ApiError } from "@/lib/api-error";
import { http } from "@/lib/http";
import { renderWithProviders } from "@/test/utils";

const META = { request_id: "r1", timestamp: "2026-08-22T07:39:12Z" };

// A 1x1 PNG. The panel only ever hands this to an <img src>, so its content
// matters less than that it arrives as a data URI.
const PIXEL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGP4z8AAAAMBAQAY3Y2wAAAAAElFTkSuQmCC";

function envelope(data, message = "ok") {
  return { success: true, message, data, error: null, meta: META };
}

const SPLIT = {
  split: "screenshot-1787384342702",
  written_at: "2026-08-22T07:39:12Z",
  rows: 3,
  columns: 5,
  tile_width: 82,
  tile_height: 59,
  width: 455,
  height: 189,
};

function layout({ latestSplit = SPLIT, error = null } = {}) {
  return envelope({
    game: "FortuneOx",
    sets: error
      ? []
      : [
          { name: "5", label: "5 lines" },
          { name: "20", label: "20 lines" },
        ],
    default_set: error ? null : "5",
    threshold: 0.7,
    rows: error ? 0 : 3,
    columns: error ? 0 : 5,
    latest_split: latestSplit,
    error,
  });
}

/** One line, with its steps spelled out so the dropdown has something to show. */
function line({ name, pays, positions, steps, color, breakPosition = null }) {
  return {
    name,
    label: `Line ${name}`,
    positions,
    pays,
    paying: pays >= 2,
    matched_positions: positions.slice(0, pays),
    color,
    steps,
    break_position: breakPosition,
    image_data: PIXEL,
  };
}

const MIDDLE = ["r2c1", "r2c2", "r2c3", "r2c4", "r2c5"];
const TOP = ["r1c1", "r1c2", "r1c3", "r1c4", "r1c5"];

/** Line 1 pays 2 and then breaks; line 2 never gets going. */
const RESULT = envelope({
  game: "FortuneOx",
  set: "5",
  threshold: 0.93,
  source: SPLIT,
  summary: "Line 1 pays 2",
  lines: [
    line({
      name: "1",
      pays: 2,
      positions: MIDDLE,
      color: "#00E5FF",
      breakPosition: "r2c3",
      steps: [
        {
          left: "r2c1",
          right: "r2c2",
          similarity: 0.9765,
          matched: true,
          counted: true,
        },
        {
          left: "r2c2",
          right: "r2c3",
          similarity: 0.8259,
          matched: false,
          counted: true,
        },
        {
          left: "r2c3",
          right: "r2c4",
          similarity: 0.826,
          matched: false,
          counted: false,
        },
        {
          left: "r2c4",
          right: "r2c5",
          similarity: 0.7276,
          matched: false,
          counted: false,
        },
      ],
    }),
    line({
      name: "2",
      pays: 0,
      positions: TOP,
      color: "#FF3D71",
      breakPosition: "r1c2",
      steps: [
        {
          left: "r1c1",
          right: "r1c2",
          similarity: 0.7598,
          matched: false,
          counted: true,
        },
        // Matched but never counted: the run had already broken. This is the one
        // distinction the dropdown exists to make visible.
        {
          left: "r1c2",
          right: "r1c3",
          similarity: 0.9987,
          matched: true,
          counted: false,
        },
        {
          left: "r1c3",
          right: "r1c4",
          similarity: 0.9988,
          matched: true,
          counted: false,
        },
        {
          left: "r1c4",
          right: "r1c5",
          similarity: 0.7993,
          matched: false,
          counted: false,
        },
      ],
    }),
  ],
  stats: {
    lines: 5,
    paying: 1,
    comparisons: 20,
    matches: 7,
    best_line: "1",
    best_pays: 2,
    score_min: 0.5989,
    score_max: 0.9988,
    matched_min: 0.9731,
    rejected_max: 0.826,
  },
  output_dir: "C:\\captures\\grid\\screenshot-1787384342702\\paylines",
  output_file: "5.png",
  overlay_image: PIXEL,
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** Answer the layout GET, and any POST with `result`. */
function mockApi({ payload = layout(), result = RESULT } = {}) {
  return vi.spyOn(http, "request").mockImplementation((config) => {
    if (config.method === "POST") {
      return Promise.resolve({ status: 200, data: result });
    }
    return Promise.resolve({ status: 200, data: payload });
  });
}

async function renderReady(payload = layout()) {
  const request = mockApi({ payload });
  renderWithProviders(<PaylinePanel />);
  await screen.findByText("FortuneOx");
  return request;
}

/**
 * One labelled figure in the statistics block.
 *
 * Scoped rather than looked up by its value, because a score printed in the
 * statistics is often the same score printed in a line's comparisons -- and a
 * bare `getByText("0.8260")` matching both says nothing about which block is
 * showing what.
 */
function figure(label) {
  return screen.getByText(label).closest("div");
}

/**
 * A line's dropdown trigger, disambiguated from the result box above it.
 *
 * A paying line's name is now shown twice -- once as its own result box, once
 * as the header of its dropdown -- so a bare `getByText("Line 1")` matches
 * both and throws. Only the dropdown's copy sits inside a `<summary>`.
 */
function dropdownFor(label) {
  return screen
    .getAllByText(label)
    .map((el) => el.closest("summary"))
    .find(Boolean);
}

/** Resolves once a check has rendered its combined overlay. */
async function waitForResult() {
  await screen.findByAltText(/paying lines of the 5-line set/i);
}

describe("PaylinePanel", () => {
  it("shows the game a check would read", async () => {
    await renderReady();

    expect(screen.getByText("FortuneOx")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Line set" })).toHaveValue("5");
    // Neither the grid size, the raw set list, nor the split's own dimensions
    // are shown as stats -- the dropdown already offers the sets, and the
    // split is either usable or it isn't.
    expect(screen.queryByText("3 × 5")).not.toBeInTheDocument();
    expect(screen.queryByText("5 · 20")).not.toBeInTheDocument();
    expect(screen.queryByText(/455×189/)).not.toBeInTheDocument();
  });

  it("disables Check until something has been split", async () => {
    await renderReady(layout({ latestSplit: null }));

    expect(screen.getByRole("button", { name: /check/i })).toBeDisabled();
    expect(screen.getByText(/no split yet/i)).toBeInTheDocument();
  });

  it("reports a game with no paylines as a state rather than an error", async () => {
    await renderReady(
      layout({ error: "The game config for 'HuffNPuffLink' declares no 'paylines'" }),
    );

    expect(screen.getByText(/declares no 'paylines'/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /check/i })).toBeDisabled();
  });

  it("checks the selected set and shows the box for the paying line and the picture", async () => {
    const request = await renderReady();

    await userEvent.click(screen.getByRole("button", { name: /check/i }));
    await waitForResult();

    const post = request.mock.calls.find(([config]) => config.method === "POST");
    // No `split`, so the backend picks the newest one.
    expect(post[0].data).toEqual({ set: "5" });
    expect(screen.getByAltText(/paying lines of the 5-line set/i)).toHaveAttribute(
      "src",
      PIXEL,
    );
    // The comma sentence is gone; the paying line is its own box instead.
    expect(screen.queryByText(RESULT.data.summary)).not.toBeInTheDocument();
    expect(screen.getAllByText("Line 1")).toHaveLength(2); // its box, its dropdown
    // The result box, the dropdown's badge, and the "best line" statistic all
    // say "pays 2" independently -- three places, not a shared string.
    expect(screen.getAllByText("pays 2")).toHaveLength(3);
  });

  it("sends the typed threshold instead of the configured one", async () => {
    const request = await renderReady();

    await userEvent.type(
      screen.getByRole("textbox", { name: "Match threshold" }),
      "0.93",
    );
    await userEvent.click(screen.getByRole("button", { name: /^check$/i }));
    await waitForResult();

    const post = request.mock.calls.find(([config]) => config.method === "POST");
    expect(post[0].data).toEqual({ set: "5", threshold: 0.93 });
  });

  it("refuses a threshold outside the range the measure produces", async () => {
    await renderReady();

    await userEvent.type(screen.getByRole("textbox", { name: "Match threshold" }), "7");

    expect(screen.getByText(/must be a number from -1 to 1/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^check$/i })).toBeDisabled();
  });

  it("shows the whole distribution, not only the verdict", async () => {
    await renderReady();

    await userEvent.click(screen.getByRole("button", { name: /check/i }));
    await waitForResult();

    // The pair that says whether the threshold is separating the symbols at all.
    expect(within(figure("Lowest match")).getByText("0.9731")).toBeVisible();
    expect(within(figure("Highest reject")).getByText("0.8260")).toBeVisible();
    expect(within(figure("Pairs")).getByText("20")).toBeVisible();
    expect(within(figure("Best line")).getByText("pays 2")).toBeVisible();
    expect(within(figure("Score range")).getByText("0.5989–0.9988")).toBeVisible();
    // Removed along with the suggestion feature: nothing offers a one-click fill.
    expect(screen.queryByText(/suggested threshold/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /use 0\./i })).not.toBeInTheDocument();
  });

  it("does not show the file path a check wrote its picture to", async () => {
    await renderReady();

    await userEvent.click(screen.getByRole("button", { name: /check/i }));
    await waitForResult();

    expect(screen.queryByText(/paylines\5\.png/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/screenshot-1787384342702.*paylines/i),
    ).not.toBeInTheDocument();
  });

  it("keeps every line's comparisons behind its own dropdown", async () => {
    await renderReady();

    await userEvent.click(screen.getByRole("button", { name: /check/i }));
    await waitForResult();

    // Both lines are listed, and neither has spilled its scores onto the page.
    // `toBeVisible` rather than `toBeInTheDocument`: a closed <details> keeps its
    // content in the DOM, so presence is not the question -- being shown is.
    expect(dropdownFor("Line 1")).toBeVisible();
    expect(dropdownFor("Line 2")).toBeVisible();
    expect(screen.getByText("0.9765")).not.toBeVisible();

    await userEvent.click(dropdownFor("Line 1"));

    expect(screen.getByText("0.9765")).toBeVisible();
    expect(screen.getByText("0.8259")).toBeVisible();
    // Still only line 1's: opening one must not open the rest.
    expect(screen.getByText("0.7598")).not.toBeVisible();
  });

  it("shows a matched-but-uncounted score without labelling it in text", async () => {
    await renderReady();

    await userEvent.click(screen.getByRole("button", { name: /check/i }));
    await waitForResult();

    await userEvent.click(dropdownFor("Line 2"));

    // 0.9987 is a match on a line that pays nothing -- the whole reason the two
    // flags are reported apart -- but the breaking picture carries that now,
    // not a line of text.
    expect(screen.getByText("0.9987")).toBeVisible();
    expect(screen.queryByText(/after the run broke/i)).not.toBeInTheDocument();
    expect(within(dropdownFor("Line 2")).getByText("no win")).toBeInTheDocument();
  });

  it("shows each line's own tracking picture with its break called out", async () => {
    await renderReady();

    await userEvent.click(screen.getByRole("button", { name: /check/i }));
    await waitForResult();

    await userEvent.click(dropdownFor("Line 1"));
    expect(
      screen.getByAltText(/line 1 traced over the reels, breaking at r2c3/i),
    ).toHaveAttribute("src", PIXEL);

    await userEvent.click(dropdownFor("Line 2"));
    expect(
      screen.getByAltText(/line 2 traced over the reels, breaking at r1c2/i),
    ).toHaveAttribute("src", PIXEL);
  });

  it("surfaces a failed check without losing the panel", async () => {
    vi.spyOn(http, "request").mockImplementation((config) => {
      if (config.method === "POST") {
        return Promise.reject(
          ApiError.from({
            response: {
              status: 409,
              data: {
                success: false,
                message: "The split does not match the grid the game now declares",
                data: null,
                error: { code: "PAYLINE_SOURCE_STALE", details: [] },
                meta: META,
              },
            },
          }),
        );
      }
      return Promise.resolve({ status: 200, data: layout() });
    });
    renderWithProviders(<PaylinePanel />);
    await screen.findByText("FortuneOx");

    await userEvent.click(screen.getByRole("button", { name: /check/i }));

    await waitFor(() =>
      expect(screen.getByText(/does not match the grid/i)).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /check/i })).toBeEnabled();
  });
});
