import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ClassifyCard } from "@/features/image-classifier/classify-card";
import { useClassifyTiles } from "@/features/image-classifier/use-image-classifier";
import { http } from "@/lib/http";
import { renderWithProviders } from "@/test/utils";

const META = { request_id: "r1", timestamp: "2026-08-28T00:00:00Z" };

// A 1x1 PNG. The card only hands this to an <img src>, so what matters is that it
// arrives as a data URI, not what it depicts.
const PIXEL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGP4z8AAAAMBAQAY3Y2wAAAAAElFTkSuQmCC";

function envelope(data, message = "ok") {
  return { success: true, message, data, error: null, meta: META };
}

const STATUS = {
  state: "ready",
  detail: null,
  threads: 6,
  min_confidence: 0.9,
  architecture: "efficientnet_b0",
  architectures: [
    {
      name: "efficientnet_b0",
      label: "EfficientNet-B0",
      trained: true,
      is_default: true,
      trained_at: "2026-08-28T02:10:31",
      holdout_accuracy: 1,
      detail: null,
    },
    {
      name: "resnet34",
      label: "ResNet34",
      trained: true,
      is_default: false,
      trained_at: "2026-08-28T02:32:21",
      holdout_accuracy: 1,
      detail: null,
    },
  ],
  model: { classes: ["AA", "BB", "CC"], image_size: 224 },
  dataset: { exists: true, classes: [], warnings: [] },
  training: null,
  active: false,
};

const SPLITS = {
  splits: [{ name: "shot", written_at: META.timestamp, rows: 1, columns: 2, tiles: 2 }],
  latest: "shot",
  error: null,
};

const RESULT = {
  game: "FortuneOx",
  split: "shot",
  rows: 1,
  columns: 2,
  min_confidence: 0.9,
  model: {
    classes: ["AA", "BB", "CC"],
    image_size: 224,
    architecture: "efficientnet_b0",
    label: "EfficientNet-B0",
    metrics: {},
  },
  symbol_grid: [["AA", null]],
  label_grid: [["Ox", null]],
  tiles: [
    {
      name: "r1c1",
      row: 1,
      column: 1,
      symbol: "AA",
      label: "Ox",
      confidence: 0.98,
      known: true,
      predictions: [{ symbol: "AA", label: "Ox", confidence: 0.98 }],
      width: 24,
      height: 18,
      image_data: null,
    },
    {
      name: "r1c2",
      row: 1,
      column: 2,
      symbol: null,
      label: "unknown",
      confidence: 0.42,
      known: false,
      predictions: [{ symbol: "AA", label: "Ox", confidence: 0.42 }],
      width: 24,
      height: 18,
      image_data: null,
    },
  ],
  named: 1,
  unknown: 1,
  summary: "1 of 2 tiles named; 1 below 0.90",
  output_dir: "grid/shot/classifier",
  overlay_file: "symbols.png",
  overlay_image: PIXEL,
};

/** Mounts the card with the real classify mutation hook behind a stubbed axios. */
function Harness() {
  const classify = useClassifyTiles();
  return (
    <ClassifyCard
      status={STATUS}
      splits={{ data: SPLITS, isFetching: false, refetch: vi.fn() }}
      classify={classify}
    />
  );
}

/** Press Classify, wait for the result, and hand back the rendered tables. */
async function classify() {
  renderWithProviders(<Harness />);
  await userEvent.click(screen.getByRole("button", { name: /classify/i }));
  await waitFor(() => expect(screen.getByText(RESULT.summary)).toBeInTheDocument());
  return screen.getAllByRole("table");
}

function cellsOf(table) {
  return within(table)
    .getAllByRole("cell")
    .map((cell) => cell.textContent);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ClassifyCard", () => {
  it("shows the codes and the display names as two matrices", async () => {
    vi.spyOn(http, "request").mockResolvedValue({ data: envelope(RESULT) });

    const [codes, names] = await classify();

    // Same shape, same holes -- both arrive on the response rather than one being
    // derived here, so they cannot disagree about which cell is which.
    expect(cellsOf(codes)).toEqual(["AA", "—"]);
    expect(cellsOf(names)).toEqual(["Ox", "—"]);
  });

  it("shows a rejected tile's best guess as a percentage, not as unknown", async () => {
    vi.spyOn(http, "request").mockResolvedValue({ data: envelope(RESULT) });

    const tables = await classify();

    // The per-tile table is the last one; the first two are the matrices above.
    const rows = within(tables[tables.length - 1])
      .getAllByRole("row")
      .slice(1);
    expect(
      rows.map((row) =>
        within(row)
          .getAllByRole("cell")
          .map((cell) => cell.textContent),
      ),
    ).toEqual([
      ["Ox", "AA", "r1c1", "98.0%"],
      // The floor decides the *grid*; this table still reports what the model
      // thought, so a rejected tile names its leading candidate rather than
      // going blank. Only the styling says it did not clear the floor.
      ["Ox", "AA", "r1c2", "42.0%"],
    ]);
  });

  it("marks the rejected row's figure with the destructive colour", async () => {
    vi.spyOn(http, "request").mockResolvedValue({ data: envelope(RESULT) });

    const tables = await classify();
    const rows = within(tables[tables.length - 1])
      .getAllByRole("row")
      .slice(1);

    const [named, rejected] = rows.map((row) => within(row).getAllByRole("cell")[3]);
    expect(named.className).not.toMatch(/text-destructive/);
    expect(rejected.className).toMatch(/text-destructive/);
  });

  it("sends the split and the floor the operator chose", async () => {
    const request = vi
      .spyOn(http, "request")
      .mockResolvedValue({ data: envelope(RESULT) });

    renderWithProviders(<Harness />);
    await userEvent.selectOptions(screen.getByLabelText("Split"), "shot");
    await userEvent.selectOptions(screen.getByLabelText("Engine"), "resnet34");
    // Typed as a percentage, because that is how the whole page reads them.
    await userEvent.type(screen.getByLabelText("Floor %"), "85");
    await userEvent.click(screen.getByRole("button", { name: /classify/i }));

    await waitFor(() => expect(request).toHaveBeenCalled());
    // Sent as a probability: the API speaks fractions and the field speaks
    // percent, and this is the one place the two units meet.
    expect(request.mock.calls[0][0].data).toEqual({
      split: "shot",
      architecture: "resnet34",
      min_confidence: 0.85,
      include_images: false,
    });
  });

  it("offers both trained engines and defaults to neither being named", async () => {
    vi.spyOn(http, "request").mockResolvedValue({ data: envelope(RESULT) });

    renderWithProviders(<Harness />);
    const options = within(screen.getByLabelText("Engine")).getAllByRole("option");

    expect(options.map((option) => option.value)).toEqual([
      "",
      "efficientnet_b0",
      "resnet34",
    ]);
    expect(options[0].textContent).toMatch(/Default \(EfficientNet-B0\)/);
  });

  it("never asks for the per-tile pictures, since none are rendered", async () => {
    const request = vi
      .spyOn(http, "request")
      .mockResolvedValue({ data: envelope(RESULT) });

    renderWithProviders(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: /classify/i }));

    await waitFor(() => expect(request).toHaveBeenCalled());
    // Split and floor omitted so the backend picks the newest and its own floor;
    // images off because fifteen base64 PNGs would be most of the payload.
    expect(request.mock.calls[0][0].data).toEqual({ include_images: false });
  });
});
