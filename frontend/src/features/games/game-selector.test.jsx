import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GameSelector } from "@/features/games/game-selector";
import { http } from "@/lib/http";
import { renderWithProviders } from "@/test/utils";

const META = { request_id: "r1", timestamp: "2026-08-18T09:12:44Z" };

function envelope(data, message = "Request completed successfully") {
  return { success: true, message, data, error: null, meta: META };
}

const CATALOG = envelope({
  active_game: "HuffNPuffLink",
  games: [
    { game: "FortuneOx", label: "FortuneOx", process: "FortuneOx.exe" },
    {
      game: "HuffNPuffLink",
      label: "HuffNPuffLink",
      process: "HuffNPuffLink.exe",
    },
  ],
});

const SELECTED = envelope(
  {
    game: "FortuneOx",
    label: "FortuneOx",
    process: "FortuneOx.exe",
    obs_window_selected: null,
  },
  "Game selected; OBS will retarget when it connects",
);

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GameSelector", () => {
  it("loads the active game and switches it through the API", async () => {
    const request = vi.spyOn(http, "request").mockImplementation((config) =>
      Promise.resolve({
        status: 200,
        data: config.method === "PUT" ? SELECTED : CATALOG,
      }),
    );

    renderWithProviders(<GameSelector />);

    const selector = await screen.findByRole("combobox", { name: "Active game" });
    expect(selector).toHaveValue("HuffNPuffLink");

    await userEvent.selectOptions(selector, "FortuneOx");

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        expect.objectContaining({
          method: "PUT",
          url: expect.stringContaining("/games/active"),
          data: { game: "FortuneOx" },
        }),
      );
    });
    expect(selector).toHaveValue("FortuneOx");
  });
});
