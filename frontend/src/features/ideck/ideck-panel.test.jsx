import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IDeckPanel } from "@/features/ideck/ideck-panel";
import { http } from "@/lib/http";
import { renderWithProviders } from "@/test/utils";

const META = { request_id: "r1", timestamp: "2026-08-18T09:12:44Z" };

function envelope(data, message = "ok") {
  return { success: true, message, data, error: null, meta: META };
}

function status(state = "ready") {
  return envelope(
    {
      state,
      window_title: "Virtual OLED",
      window_class: "SDL_app",
      hwnd: 329156,
      client_width: state === "minimized" ? 0 : 849,
      client_height: state === "minimized" ? 0 : 183,
      panel_id: "Virtual OLED",
      panel_width: 849,
      panel_height: 183,
      game: "ExampleGame",
      button_count: 14,
      panel_xml: "C:\\cfg\\virtual_oled.xml",
      log_path: "C:\\logs\\OledPanelSvc.log",
      verify_presses: true,
    },
    `The i-deck is ${state}`,
  );
}

/** Three keys is enough to cover both rows and a wide key. */
const BUTTONS = envelope([
  {
    xml_id: "Line1",
    button_id: 0,
    panel_x: 136,
    panel_y: 15,
    width: 106,
    height: 74,
    client_x: 189,
    client_y: 52,
  },
  {
    xml_id: "Rebet",
    button_id: 10,
    panel_x: 701,
    panel_y: 15,
    width: 138,
    height: 74,
    client_x: 770,
    client_y: 52,
  },
  {
    xml_id: "Collect",
    button_id: 12,
    panel_x: 10,
    panel_y: 94,
    width: 106,
    height: 74,
    client_x: 63,
    client_y: 131,
  },
]);

const PRESSED = envelope({
  button: "Rebet",
  xml_id: "Rebet",
  button_id: 10,
  client_x: 770,
  client_y: 52,
  confirmed: true,
  verified: true,
  evidence: "Button Pressed ID=a",
  restored: false,
  refocused: false,
  elapsed_ms: 240,
});

/** Route the two GETs the panel makes; POSTs resolve to `pressed`. */
function mockApi({ state = "ready", pressed = PRESSED } = {}) {
  return vi.spyOn(http, "request").mockImplementation((config) => {
    if (config.url.endsWith("/buttons")) {
      return Promise.resolve({ status: 200, data: BUTTONS });
    }
    if (config.url.endsWith("/status")) {
      return Promise.resolve({ status: 200, data: status(state) });
    }
    return Promise.resolve({ status: 200, data: pressed });
  });
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("IDeckPanel", () => {
  it("does not poll the status endpoint automatically", async () => {
    vi.useFakeTimers();
    const request = mockApi();

    renderWithProviders(<IDeckPanel />);

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("ready")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(15_000));

    expect(
      request.mock.calls.filter(([config]) => config.url.endsWith("/status")),
    ).toHaveLength(1);
  });

  it("lays the deck out from the panel layout", async () => {
    mockApi();
    renderWithProviders(<IDeckPanel />);

    expect(await screen.findByText("ready")).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: /^LINE1$/ }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: /^REBET$/ }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: /^COLLECT$/ }),
    ).toBeInTheDocument();
  });

  it("presses the key that was clicked", async () => {
    const request = mockApi();
    renderWithProviders(<IDeckPanel />);

    await userEvent.click(
      await screen.findByRole("button", { name: /^REBET$/ }),
    );

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        expect.objectContaining({
          method: "POST",
          url: "/api/ideck/press",
          data: { button: "Rebet" },
        }),
      );
    });
    expect(await screen.findByText("confirmed")).toBeInTheDocument();
  });

  it("explains an access-denied panel instead of just disabling it", async () => {
    // The failure most likely to be mistaken for a bug: everything looks fine
    // but Windows drops the input, so the card has to name the cause.
    mockApi({ state: "access_denied" });
    renderWithProviders(<IDeckPanel />);

    expect(await screen.findByText("access_denied")).toBeInTheDocument();
    expect(await screen.findByText(/Run as administrator/i)).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: /^REBET$/ }),
    ).toBeDisabled();
  });

  it("still offers the keys while the panel is only minimized", async () => {
    // Minimized is recoverable: the backend restores it as part of the press.
    mockApi({ state: "minimized" });
    renderWithProviders(<IDeckPanel />);

    expect(await screen.findByText("minimized")).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: /^REBET$/ }),
    ).toBeEnabled();
  });

  it("reports a failed press rather than looking successful", async () => {
    vi.spyOn(http, "request").mockImplementation((config) => {
      if (config.url.endsWith("/buttons")) {
        return Promise.resolve({ status: 200, data: BUTTONS });
      }
      if (config.url.endsWith("/status")) {
        return Promise.resolve({ status: 200, data: status("ready") });
      }
      return Promise.reject({
        response: {
          status: 502,
          data: {
            success: false,
            message: "the panel logged nothing",
            data: null,
            error: { code: "IDECK_PRESS_NOT_CONFIRMED", details: [] },
            meta: META,
          },
        },
      });
    });

    renderWithProviders(<IDeckPanel />);
    await userEvent.click(
      await screen.findByRole("button", { name: /^REBET$/ }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /the panel logged nothing/i,
    );
  });

  it("runs the side-effect-free probe", async () => {
    const request = vi.spyOn(http, "request").mockImplementation((config) => {
      if (config.url.endsWith("/buttons")) {
        return Promise.resolve({ status: 200, data: BUTTONS });
      }
      if (config.url.endsWith("/status")) {
        return Promise.resolve({ status: 200, data: status("ready") });
      }
      return Promise.resolve({
        status: 200,
        data: envelope({
          supported: true,
          window_found: true,
          posted: true,
          observed: true,
          evidence: "Mouse entered window 1",
          detail: "The panel reacted to a posted mouse move.",
        }),
      });
    });

    renderWithProviders(<IDeckPanel />);
    await userEvent.click(await screen.findByRole("button", { name: /probe/i }));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        expect.objectContaining({ method: "POST", url: "/api/ideck/probe" }),
      );
    });
    expect(
      await screen.findByText(/reacted to a posted mouse move/i),
    ).toBeInTheDocument();
  });
});
