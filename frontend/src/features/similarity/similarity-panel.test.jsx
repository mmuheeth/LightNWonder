import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SimilarityPanel } from "@/features/similarity/similarity-panel";
import { ApiError } from "@/lib/api-error";
import { http } from "@/lib/http";
import { renderWithProviders } from "@/test/utils";

const META = { request_id: "r1", timestamp: "2026-08-26T10:00:00Z" };

function envelope(data, message = "ok") {
  return { success: true, message, data, error: null, meta: META };
}

function match({ name, score, matched = score >= 0.9, resized = false }) {
  return {
    file_name: name,
    score,
    matched,
    resized,
    width: 40,
    height: 40,
    error: null,
  };
}

/** Three candidates: a resized perfect match, a match, and a clear reject. */
const RESULT = envelope({
  source: { file_name: "r1c2.png", width: 40, height: 40 },
  directory: "C:\\backend\\symbol-validation",
  threshold: 0.9,
  summary: "2 of 3 matched r1c2.png at 0.9",
  results: [
    match({ name: "AA/AA_00000.png", score: 1.0, resized: true }),
    match({ name: "AA/AA_00001.png", score: 0.9743 }),
    match({ name: "AA/AA_00002.png", score: 0.6512 }),
  ],
  stats: {
    candidates: 3,
    compared: 3,
    errors: 0,
    resized: 1,
    matches: 2,
    score_min: 0.6512,
    score_max: 1.0,
    matched_min: 0.9743,
    rejected_max: 0.6512,
  },
});

function mockCompare(result = RESULT) {
  return vi.spyOn(http, "request").mockResolvedValue({ status: 200, data: result });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SimilarityPanel", () => {
  it("compares with the backend defaults when every field is blank", async () => {
    const request = mockCompare();
    renderWithProviders(<SimilarityPanel />);

    await userEvent.click(screen.getByRole("button", { name: /compare/i }));

    const [config] = request.mock.calls.find(([call]) => call.method === "POST");
    expect(config.url).toMatch(/\/api\/similarity\/compare$/);
    expect(config.data).toEqual({});
  });

  it("sends typed paths and threshold verbatim", async () => {
    const request = mockCompare();
    renderWithProviders(<SimilarityPanel />);

    await userEvent.type(
      screen.getByRole("textbox", { name: "Source image" }),
      "tiles/r1c2.png",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Candidates folder" }),
      "refs",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Match threshold" }),
      "0.93",
    );
    await userEvent.click(screen.getByRole("button", { name: /compare/i }));

    const [config] = request.mock.calls.find(([call]) => call.method === "POST");
    expect(config.data).toEqual({
      source: "tiles/r1c2.png",
      directory: "refs",
      threshold: 0.93,
    });
  });

  it("rejects an out-of-range threshold before anything is sent", async () => {
    const request = mockCompare();
    renderWithProviders(<SimilarityPanel />);

    await userEvent.type(screen.getByRole("textbox", { name: "Match threshold" }), "7");

    expect(
      screen.getByText("The threshold must be a number from -1 to 1."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /compare/i })).toBeDisabled();
    expect(request).not.toHaveBeenCalled();
  });

  it("renders the summary, the chart and every score after a run", async () => {
    mockCompare();
    renderWithProviders(<SimilarityPanel />);

    await userEvent.click(screen.getByRole("button", { name: /compare/i }));

    expect(
      await screen.findByText("2 of 3 matched r1c2.png at 0.9"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /similarity of 3 candidates/i }),
    ).toBeInTheDocument();
    expect(screen.getByText("AA/AA_00000.png")).toBeInTheDocument();
    // The score is also the stats block's "lowest match", so it appears twice.
    expect(screen.getAllByText("0.9743").length).toBeGreaterThan(0);
    expect(screen.getByText("resized")).toBeInTheDocument();
  });

  it("shows the backend's message when the run fails, and can be retried", async () => {
    vi.spyOn(http, "request").mockRejectedValue(
      ApiError.from({
        response: {
          status: 404,
          data: {
            success: false,
            message: "No source image at C:\\missing.png",
            data: null,
            error: { code: "SIMILARITY_SOURCE_NOT_FOUND", details: [] },
            meta: META,
          },
        },
      }),
    );
    renderWithProviders(<SimilarityPanel />);

    await userEvent.click(screen.getByRole("button", { name: /compare/i }));

    expect(
      await screen.findByText("No source image at C:\\missing.png"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /compare/i })).toBeEnabled();
  });
});
