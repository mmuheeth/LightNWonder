import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GridPanel } from "@/features/grid/grid-panel";
import { http } from "@/lib/http";
import { renderWithProviders } from "@/test/utils";

const META = { request_id: "r1", timestamp: "2026-08-20T02:19:45Z" };

// A 1x1 PNG. The panel only ever hands this to an <img src>, so its content
// matters less than that it arrives as a data URI.
const PIXEL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGP4z8AAAAMBAQAY3Y2wAAAAAElFTkSuQmCC";

function envelope(data, message = "ok") {
  return { success: true, message, data, error: null, meta: META };
}

const FRAME = {
  file_name: "screenshot-1787192384798.png",
  captured_at: "2026-08-20T02:19:45Z",
  width: 1280,
  height: 720,
};

/** A 2x3 grid, small enough to assert every tile by name. */
const POSITIONS = [
  ["r1c1", "r1c2", "r1c3"],
  ["r2c1", "r2c2", "r2c3"],
];

function layout({
  latestFrame = FRAME,
  error = null,
  rows = 2,
  columns = 3,
  inset = [0.03, 0.03, 0.03, 0.03],
} = {}) {
  return envelope({
    game: "FortuneOx",
    region: "reels",
    roi: error ? null : [0.35, 0.559722, 0.649219, 0.822222],
    rows: error ? 0 : rows,
    columns: error ? 0 : columns,
    positions: error ? [] : POSITIONS,
    inset: error ? [0, 0, 0, 0] : inset,
    latest_frame: latestFrame,
    error,
  });
}

const TILES = POSITIONS.flatMap((names, rowIndex) =>
  names.map((name, columnIndex) => ({
    row: rowIndex + 1,
    column: columnIndex + 1,
    name,
    file_name: `${name}.png`,
    roi: [0, 0, 0.2, 0.5],
    box: [0, 0, 40, 50],
    width: 40,
    height: 50,
    image_data: PIXEL,
  })),
);

const SPLIT = envelope(
  {
    game: "FortuneOx",
    region: "reels",
    roi: [0.35, 0.559722, 0.649219, 0.822222],
    source: FRAME,
    box: [448, 403, 831, 592],
    content_box: [429, 0, 850, 720],
    letterboxed: true,
    width: 383,
    height: 189,
    rows: 2,
    columns: 3,
    inset: [0.03, 0.03, 0.03, 0.03],
    tile_width: 69,
    tile_height: 59,
    output_dir:
      "C:\\Workspace\\feature-grid\\backend\\obs-captured-files\\grid\\screenshot-1787192384798",
    crop_file: "reels.png",
    crop_image: PIXEL,
    positions: POSITIONS,
    tiles: TILES,
  },
  "Split roi.reels of screenshot-1787192384798.png into 2x3 tiles",
);

/** Answer the layout GET, and any POST with `split`. */
function mockApi({ grid = layout(), split = SPLIT } = {}) {
  return vi.spyOn(http, "request").mockImplementation((config) => {
    if (config.method === "POST") {
      return Promise.resolve({ status: 200, data: split });
    }
    return Promise.resolve({ status: 200, data: grid });
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GridPanel", () => {
  it("reports the shape of the active game's grid", async () => {
    mockApi();

    renderWithProviders(<GridPanel />);

    expect(await screen.findByText("FortuneOx")).toBeInTheDocument();
    expect(screen.getByText(/2 × 3 · roi\.reels/)).toBeInTheDocument();
    // The frame a split would use is named before anyone presses Split.
    expect(screen.getByText(/1280×720/)).toBeInTheDocument();
  });

  it("splits the newest frame and shows every tile by its position", async () => {
    const request = mockApi();
    const user = userEvent.setup();

    renderWithProviders(<GridPanel />);
    await screen.findByText("FortuneOx");

    await user.click(screen.getByRole("button", { name: /split/i }));

    // Each tile is labelled with the matrix position it came from, so a
    // transposed grid would be visible rather than merely wrong.
    for (const name of POSITIONS.flat()) {
      expect(await screen.findByRole("img", { name: `Tile ${name}` })).toHaveAttribute(
        "src",
        PIXEL,
      );
      expect(screen.getByText(name)).toBeInTheDocument();
    }

    const post = request.mock.calls.find(([config]) => config.method === "POST");
    // No file_name, so the backend picks the newest frame.
    expect(post[0].data).toEqual({});
  });

  it("lays the tiles out as many across as the response says", async () => {
    mockApi();
    const user = userEvent.setup();

    renderWithProviders(<GridPanel />);
    await screen.findByText("FortuneOx");
    await user.click(screen.getByRole("button", { name: /split/i }));

    const tile = await screen.findByRole("img", { name: "Tile r1c1" });
    // The width of a row comes off the response, so six reels needs no change.
    expect(tile.closest("figure").parentElement).toHaveStyle({
      gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
    });
  });

  it("shows the reels crop and where the files were written", async () => {
    mockApi();
    const user = userEvent.setup();

    renderWithProviders(<GridPanel />);
    await screen.findByText("FortuneOx");
    await user.click(screen.getByRole("button", { name: /split/i }));

    expect(
      await screen.findByRole("img", { name: /crop of roi\.reels/i }),
    ).toHaveAttribute("src", PIXEL);
    // The pixel box is shown beside it: what to read when a crop looks off.
    expect(screen.getByText(/448, 403, 831, 592/)).toBeInTheDocument();
    // And the game's own rectangle, which is what `roi.reels` resolved against.
    expect(screen.getByText(/421×720 at \[429, 0, 850, 720\]/)).toBeInTheDocument();
    // The files are the deliverable, so the panel says where they went. Matched
    // on the frame's own folder, because the hint above it names the root too.
    expect(screen.getByText(/grid.screenshot-1787192384798$/)).toBeInTheDocument();
  });

  it("refuses to split before a screenshot has been taken", async () => {
    mockApi({ grid: layout({ latestFrame: null }) });

    renderWithProviders(<GridPanel />);
    await screen.findByText("FortuneOx");

    expect(screen.getByRole("button", { name: /split/i })).toBeDisabled();
    // The empty state has to say where a frame comes from.
    expect(screen.getByText(/take one from the OBS panel/i)).toBeInTheDocument();
  });

  it("says why a game without reels cannot be split", async () => {
    mockApi({
      grid: layout({
        error:
          "The game config for 'HuffNPuffLink' declares no 'roi.reels' region to split",
      }),
    });

    renderWithProviders(<GridPanel />);
    await screen.findByText("FortuneOx");

    // A state, not an error card: the request succeeded and the game has no reels.
    expect(screen.getByText(/declares no 'roi.reels'/)).toBeInTheDocument();
    expect(screen.getByText("not configured")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /split/i })).toBeDisabled();
  });

  it("surfaces a failed split without losing the layout", async () => {
    mockApi();
    const user = userEvent.setup();

    renderWithProviders(<GridPanel />);
    await screen.findByText("FortuneOx");

    vi.spyOn(http, "request").mockRejectedValue({
      response: {
        status: 502,
        data: {
          success: false,
          message: "shot.png could not be read as an image",
          data: null,
          error: { code: "ROI_EXTRACT_FAILED", details: [] },
          meta: META,
        },
      },
    });
    await user.click(screen.getByRole("button", { name: /split/i }));

    await waitFor(() => {
      expect(screen.getByText(/could not be read as an image/)).toBeInTheDocument();
    });
    // The card still shows what game and grid it was looking at.
    expect(screen.getByText("FortuneOx")).toBeInTheDocument();
  });

  it("reports the border trim the config asks for", async () => {
    mockApi();

    renderWithProviders(<GridPanel />);
    await screen.findByText("FortuneOx");

    // Four equal edges read as one number rather than as a list of four.
    expect(screen.getByText("0.03")).toBeInTheDocument();
  });

  it("reads a game with no trim as none rather than as zeros", async () => {
    mockApi({ grid: layout({ inset: [0, 0, 0, 0] }) });

    renderWithProviders(<GridPanel />);
    await screen.findByText("FortuneOx");

    expect(screen.getByText("none")).toBeInTheDocument();
  });

  it("spells out a trim that is not the same on every edge", async () => {
    mockApi({ grid: layout({ inset: [0.04, 0.02, 0.04, 0.02] }) });

    renderWithProviders(<GridPanel />);
    await screen.findByText("FortuneOx");

    expect(screen.getByText("0.04, 0.02, 0.04, 0.02")).toBeInTheDocument();
  });

  it("sends no trim when the override is left blank", async () => {
    const request = mockApi();
    const user = userEvent.setup();

    renderWithProviders(<GridPanel />);
    await screen.findByText("FortuneOx");

    await user.click(screen.getByRole("button", { name: /split/i }));

    const post = request.mock.calls.find(([config]) => config.method === "POST");
    // Blank means "whatever the config says", not "trim nothing".
    expect(post[0].data).toEqual({});
  });

  it("sends the override the trim box was typed into", async () => {
    const request = mockApi();
    const user = userEvent.setup();

    renderWithProviders(<GridPanel />);
    await screen.findByText("FortuneOx");

    await user.type(screen.getByLabelText("Trim override"), "0.06");
    await user.click(screen.getByRole("button", { name: /split/i }));

    await waitFor(() => {
      const post = request.mock.calls.find(([c]) => c.method === "POST");
      expect(post[0].data).toEqual({ inset: 0.06 });
    });
  });

  it("refuses a trim that could not leave a tile", async () => {
    mockApi();
    const user = userEvent.setup();

    renderWithProviders(<GridPanel />);
    await screen.findByText("FortuneOx");

    await user.type(screen.getByLabelText("Trim override"), "0.8");

    expect(screen.getByRole("button", { name: /split/i })).toBeDisabled();
    expect(screen.getByText(/must be a fraction from 0 up to 0.5/)).toBeInTheDocument();
  });

  it("reports the trim a split actually used", async () => {
    mockApi();
    const user = userEvent.setup();

    renderWithProviders(<GridPanel />);
    await screen.findByText("FortuneOx");
    await user.click(screen.getByRole("button", { name: /split/i }));

    await screen.findByRole("img", { name: "Tile r1c1" });
    // Once from the layout, once from the result -- the second is what was done.
    expect(screen.getAllByText("0.03")).toHaveLength(2);
  });

  it("reports one tile size for the whole grid", async () => {
    mockApi();
    const user = userEvent.setup();

    renderWithProviders(<GridPanel />);
    await screen.findByText("FortuneOx");
    await user.click(screen.getByRole("button", { name: /split/i }));

    // One number for all six, rather than six numbers to be compared by eye.
    expect(await screen.findByText(/69×59 · all 6 equal/)).toBeInTheDocument();
  });

  it("does not poll for the layout", async () => {
    const request = mockApi();

    renderWithProviders(<GridPanel />);
    await screen.findByText("FortuneOx");

    expect(
      request.mock.calls.filter(([config]) => config.method !== "POST"),
    ).toHaveLength(1);
  });
});
