import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RoiPanel } from "@/features/roi/roi-panel";
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

function catalog({ latestFrame = FRAME, regions = REGIONS } = {}) {
  return envelope({ game: "FortuneOx", regions, latest_frame: latestFrame });
}

const FRAME = {
  file_name: "screenshot-1787192384798.png",
  captured_at: "2026-08-20T02:19:45Z",
  width: 1280,
  height: 720,
};

const REGIONS = [
  {
    region: "cash_meter",
    label: "roi.cash_meter",
    roi: [0.229264, 0.844468, 0.762349, 0.884554],
    error: null,
  },
  {
    region: "win_meter",
    label: "roi.win_meter",
    roi: [0.4, 0.8, 0.6, 0.9],
    error: null,
  },
];

const CROP = envelope(
  {
    game: "FortuneOx",
    region: "cash_meter",
    roi: [0.229264, 0.844468, 0.762349, 0.884554],
    source: FRAME,
    box: [526, 608, 750, 637],
    content_box: [429, 0, 850, 720],
    letterboxed: true,
    width: 224,
    height: 29,
    image_data: PIXEL,
  },
  "Extracted roi.cash_meter from screenshot-1787192384798.png (683x29)",
);

/** Answer the catalog GET, and any POST with `extract`. */
function mockApi({ regions = catalog(), extract = CROP } = {}) {
  return vi.spyOn(http, "request").mockImplementation((config) => {
    if (config.method === "POST") {
      return Promise.resolve({ status: 200, data: extract });
    }
    return Promise.resolve({ status: 200, data: regions });
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("RoiPanel", () => {
  it("names the game and the frame an extraction would use", async () => {
    mockApi();

    renderWithProviders(<RoiPanel />);

    expect(await screen.findByText("FortuneOx")).toBeInTheDocument();
    expect(screen.getByText(/1280×720/)).toBeInTheDocument();
  });

  it("extracts the cash meter and shows the crop", async () => {
    const request = mockApi();
    const user = userEvent.setup();

    renderWithProviders(<RoiPanel />);
    await screen.findByText("FortuneOx");

    await user.click(screen.getByRole("button", { name: /extract/i }));

    const image = await screen.findByRole("img", {
      name: /crop of roi\.cash_meter/i,
    });
    expect(image).toHaveAttribute("src", PIXEL);
    // The pixel box is shown beside it: what to read when a crop looks off.
    expect(screen.getByText(/526, 608, 750, 637/)).toBeInTheDocument();
    // And the game's own rectangle, which is what the fractions resolved
    // against -- the number that says whether a misplaced crop is the region's
    // fault or the detection's.
    expect(screen.getByText(/421×720 at \[429, 0, 850, 720\]/)).toBeInTheDocument();

    const post = request.mock.calls.find(([config]) => config.method === "POST");
    // No file_name, so the backend picks the newest frame.
    expect(post[0].data).toEqual({ region: "cash_meter" });
  });

  it("refuses to extract before a screenshot has been taken", async () => {
    mockApi({ regions: catalog({ latestFrame: null }) });

    renderWithProviders(<RoiPanel />);
    await screen.findByText("FortuneOx");

    expect(screen.getByRole("button", { name: /extract/i })).toBeDisabled();
    // The empty state has to say where a frame comes from.
    expect(screen.getByText(/take one from the OBS panel/i)).toBeInTheDocument();
  });

  it("shows why a malformed cash meter region cannot be extracted", async () => {
    mockApi({
      regions: catalog({
        regions: [
          {
            region: "cash_meter",
            label: "roi.cash_meter",
            roi: [0, 0, 1, 1],
            error: "roi.cash_meter: left (0.5) must be less than right (0.2)",
          },
        ],
      }),
    });

    renderWithProviders(<RoiPanel />);
    await screen.findByText("FortuneOx");

    expect(
      screen.getByText(/left \(0.5\) must be less than right/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /extract/i })).toBeDisabled();
  });

  it("does not poll for regions", async () => {
    const request = mockApi();

    renderWithProviders(<RoiPanel />);
    await screen.findByText("FortuneOx");

    expect(
      request.mock.calls.filter(([config]) => config.method !== "POST"),
    ).toHaveLength(1);
  });
});
