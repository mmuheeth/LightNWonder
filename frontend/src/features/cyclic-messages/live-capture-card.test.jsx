import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LiveCaptureCard } from "@/features/cyclic-messages/live-capture-card";
import { http } from "@/lib/http";
import { renderWithProviders } from "@/test/utils";

const META = { request_id: "r1", timestamp: "2026-09-18T14:24:39Z" };

/**
 * One frame of the between-spins strip. `readings` is what the states below
 * turn on: absent is a frame the reader has not reached, present-but-blank is
 * one it read off a strip that was between messages.
 */
function frame(sequence, readings) {
  return {
    sequence,
    event: "cyclic-idle-message-shown",
    at: "2026-09-18T14:24:39Z",
    summary: `Strip frame ${sequence}`,
    cycle: 2,
    position: null,
    fields: {},
    captured: true,
    screenshot: `00${sequence}_cyclic-idle-message-shown.png`,
    capture_error: null,
    source: "sampled",
    log_line: "09/18/26 14:24:39.000 00 FortuneOx:1 DBG: InputManager...",
    readings,
  };
}

function band(region, repaired, confidence = 97) {
  return {
    text: repaired.toUpperCase(),
    repaired,
    confidence,
    reliable: true,
    engine: "paddle",
    region,
    key: repaired.replace(/[^a-z0-9]/gi, "").toLowerCase(),
    crop: `crop_${region}.png`,
    read_ms: 40,
    error: null,
  };
}

/** A band the recogniser reached and found nothing believable on. */
function blank(region) {
  return { ...band(region, ""), text: "", confidence: 0, reliable: false, key: "" };
}

function live({ frames, capturing = false, reading = false, strip = "idle-video" }) {
  return {
    success: true,
    message: "ok",
    data: {
      active: true,
      run_id: "2026-09-18_14-24-00",
      game: "FortuneOx",
      cycle: 2,
      strip,
      capturing,
      reading,
      recovering: false,
      recovery_pending: 0,
      queue_depth: 0,
      frame_count: frames.length,
      read_count: frames.filter((one) => one.readings.length > 0).length,
      recovered_count: 0,
      sample_rate: 0.59,
      sample_interval_seconds: 1,
      spins_without_pay: 0,
      errors: [],
      frames,
    },
    error: null,
    meta: META,
  };
}

function respond(payload) {
  return vi.spyOn(http, "request").mockResolvedValue({ status: 200, data: payload });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("LiveCaptureCard", () => {
  it("drops a frame that was read and caught the strip mid-change", async () => {
    // Sampling runs faster than the strip changes on purpose, so a frame lands
    // in the gap between two messages and every band of it reads blank. There
    // is nothing in it for a reader -- a screenshot of an empty band under the
    // words "no message" -- and on a between-spins pass half the grid was them.
    respond(
      live({
        frames: [
          frame(5, [
            blank("cyclic_message_1"),
            band("cyclic_message_2", "Game Pays 0"),
          ]),
          frame(6, [blank("cyclic_message_1"), blank("cyclic_message_2")]),
        ],
      }),
    );
    renderWithProviders(<LiveCaptureCard isActive />);

    expect(await screen.findByText("Game Pays 0")).toBeInTheDocument();
    expect(screen.queryByText("no message on the strip")).not.toBeInTheDocument();
    expect(screen.queryByText("reading…")).not.toBeInTheDocument();
    // Its picture is gone with it.
    expect(screen.queryByAltText(/cyclic-idle-message-shown at/)).toBeInTheDocument();
    expect(screen.getAllByAltText(/cyclic-idle-message-shown at/)).toHaveLength(1);
  });

  it("counts the frames it dropped rather than losing them silently", async () => {
    // Four tiles under "Frames 7" looks like frames that went missing, which is
    // the one thing this card exists to make visible.
    respond(
      live({
        frames: [
          frame(5, [blank("cyclic_message_1"), blank("cyclic_message_2")]),
          frame(6, [blank("cyclic_message_1"), blank("cyclic_message_2")]),
          frame(7, [blank("cyclic_message_1"), band("cyclic_message_2", "Game Over")]),
        ],
      }),
    );
    renderWithProviders(<LiveCaptureCard isActive />);

    expect(await screen.findByText("Game Over")).toBeInTheDocument();
    expect(screen.getByText("Mid-change")).toBeInTheDocument();
    // By its own title, not by the bare figure: "2" is also a sequence number
    // and a frame count elsewhere on the card.
    expect(screen.getByTitle(/caught the gap the strip leaves/)).toHaveTextContent("2");
  });

  it("says so when every frame of a pass caught the strip mid-change", async () => {
    respond(
      live({
        frames: [frame(5, [blank("cyclic_message_1"), blank("cyclic_message_2")])],
      }),
    );
    renderWithProviders(<LiveCaptureCard isActive />);

    expect(
      await screen.findByText(/caught the strip between two messages/),
    ).toBeInTheDocument();
  });

  it("still waits on a frame the reader has not reached", async () => {
    respond(live({ frames: [frame(6, [])] }));
    renderWithProviders(<LiveCaptureCard isActive />);

    expect(await screen.findByText("reading…")).toBeInTheDocument();
    expect(screen.queryByText("no message on the strip")).not.toBeInTheDocument();
  });

  it("says nothing is read until the strip stops, while it is still capturing", async () => {
    respond(live({ frames: [frame(6, [])], capturing: true }));
    renderWithProviders(<LiveCaptureCard isActive />);

    expect(await screen.findByText("read when the strip stops")).toBeInTheDocument();
  });

  it("names the view that spans a win and the strip after it", async () => {
    // One spin can be two strips -- the win paying out, then what the game
    // says once it has been taken -- and the card shows both together. The
    // badge has to say so, or a reader takes a view holding both for a view
    // of whichever one they can see at the top.
    respond(
      live({
        strip: "win-then-idle",
        frames: [
          frame(5, [blank("cyclic_message_1"), band("cyclic_message_2", "Game Over")]),
        ],
      }),
    );
    renderWithProviders(<LiveCaptureCard isActive />);

    expect(
      await screen.findByText("win presentation, then between-spins"),
    ).toBeInTheDocument();
  });
});
