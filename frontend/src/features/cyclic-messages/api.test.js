import { afterEach, describe, expect, it, vi } from "vitest";

import {
  readCyclicText,
  readWholeCyclicText,
  stitchCyclicText,
} from "@/features/cyclic-messages/api";
import { http } from "@/lib/http";

const META = { request_id: "r1", timestamp: "2026-09-09T22:12:53Z" };

function respond(data) {
  return vi
    .spyOn(http, "request")
    .mockResolvedValue({
      status: 200,
      data: { success: true, message: "ok", data, error: null, meta: META },
    });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("readCyclicText", () => {
  it("allows minutes rather than the client's global 15s timeout", async () => {
    // The regression this guards: reading a 90s clip is ~90 OCR calls, and on
    // Tesseract at twice the sampling rate that measured 46s, so the default
    // 15s timeout failed it every time with "The request timed out" and no
    // reading at all.
    const request = respond({ messages: [] });

    await readCyclicText("2026-09-09_22-12-53");

    const [config] = request.mock.calls[0];
    expect(config.timeout).toBeGreaterThanOrEqual(120_000);
    // Under the 300s requestTimeout Node gives the Vite dev proxy, or the
    // proxy cuts it first and the reason arrives as a bare network error.
    expect(config.timeout).toBeLessThan(300_000);
  });

  it("names the clip and the interval only when asked to", async () => {
    const request = respond({ messages: [] });

    await readCyclicText("run-1");
    expect(request.mock.calls[0][0].url).toMatch(/\/runs\/run-1\/text$/);

    await readCyclicText("run-1", { cycle: 4, intervalSeconds: 0.25 });
    expect(request.mock.calls[1][0].url).toContain("cycle=4");
    expect(request.mock.calls[1][0].url).toContain("interval_seconds=0.25");
  });

  it("escapes a run id rather than pasting it into the path", async () => {
    const request = respond({ messages: [] });

    await readCyclicText("a/../b");

    expect(request.mock.calls[0][0].url).toContain("a%2F..%2Fb");
  });
});

/** One window's reading, with only the fields the stitching reads. */
function window_({ from, to, duration, messages }) {
  return {
    from_seconds: from,
    to_seconds: to,
    duration_seconds: duration,
    frames_sampled: messages.length,
    frames_read: messages.length,
    frames_unreadable: 0,
    frames: messages.map((m) => ({ at_seconds: m.first_seen, text: m.text })),
    messages,
    captions: [],
  };
}

function message(key, text, first, last, confidence = 90) {
  return {
    key,
    text,
    first_seen: first,
    last_seen: last,
    frames: 1,
    confidence,
    reliable: true,
  };
}

describe("stitchCyclicText", () => {
  it("joins a caption that straddled the seam into one showing", () => {
    // The window boundary is an artefact of how the clip was read: a caption
    // on screen when one window ends is still on screen when the next
    // begins. Counted twice, a 40-line win reports ~45 showings it never
    // made and the caption list says every line paid twice.
    const first = window_({
      from: 0,
      to: 20,
      duration: 40,
      messages: [message("line1pays15", "Line 1 Pays 15", 18, 19.5)],
    });
    const second = window_({
      from: 20,
      to: 40,
      duration: 40,
      messages: [
        message("line1pays15", "Line 1 Pays 15", 20, 20.5),
        message("line2pays15", "Line 2 Pays 15", 21, 23),
      ],
    });

    const stitched = stitchCyclicText(first, second);

    expect(stitched.messages).toHaveLength(2);
    expect(stitched.messages[0].first_seen).toBe(18);
    expect(stitched.messages[0].last_seen).toBe(20.5);
    expect(stitched.messages[0].frames).toBe(2);
    expect(stitched.captions.map((c) => c.showings)).toEqual([1, 1]);
  });

  it("joins on the key rather than on the text, which can differ per frame", () => {
    // A message shows the best-scoring *variant* of the readings behind it,
    // so two frames of one caption need not agree character for character.
    // Joining on the text would leave the seam unjoined exactly when the
    // recogniser wavered, which is when it matters.
    const first = window_({
      from: 0,
      to: 20,
      duration: 40,
      messages: [message("line1pays15", "Line 1 Pays 15", 19, 19.5, 80)],
    });
    const second = window_({
      from: 20,
      to: 40,
      duration: 40,
      messages: [message("line1pays15", "LINE 1 PAYS 15", 20, 21, 95)],
    });

    const stitched = stitchCyclicText(first, second);

    expect(stitched.messages).toHaveLength(1);
    // And the surer reading is the one shown, as it is inside a window.
    expect(stitched.messages[0].text).toBe("LINE 1 PAYS 15");
    expect(stitched.messages[0].confidence).toBe(95);
  });

  it("counts a caption the strip repeated in two windows as two showings", () => {
    // The strip loops, so the between-spins captions come up once per lap.
    // Not a seam: there is a different caption between them.
    const first = window_({
      from: 0,
      to: 20,
      duration: 40,
      messages: [
        message("gameover", "Game Over", 1, 2),
        message("play880credits", "Play 880 Credits", 3, 4),
      ],
    });
    const second = window_({
      from: 20,
      to: 40,
      duration: 40,
      messages: [message("gameover", "Game Over", 21, 22)],
    });

    const stitched = stitchCyclicText(first, second);

    expect(stitched.messages).toHaveLength(3);
    const gameOver = stitched.captions.find((c) => c.key === "gameover");
    expect(gameOver.showings).toBe(2);
    expect(gameOver.first_seen).toBe(1);
    expect(gameOver.last_seen).toBe(22);
  });

  it("reports the whole stretch read, not just the last window", () => {
    const first = window_({ from: 0, to: 20, duration: 40, messages: [] });
    const second = window_({ from: 20, to: 40, duration: 40, messages: [] });

    const stitched = stitchCyclicText(first, second);

    expect(stitched.from_seconds).toBe(0);
    expect(stitched.to_seconds).toBe(40);
  });
});

describe("readWholeCyclicText", () => {
  function pages(...windows) {
    let call = 0;
    return vi.spyOn(http, "request").mockImplementation(() => {
      const data = windows[Math.min(call, windows.length - 1)];
      call += 1;
      return Promise.resolve({
        status: 200,
        data: { success: true, message: "ok", data, error: null, meta: META },
      });
    });
  }

  it("pages until the server says it has reached the end of the clip", async () => {
    // Paging follows `to_seconds` off each answer rather than a window size
    // of its own: the budget is the server's and a client that guessed it
    // would either leave the tail of a clip unread or time out asking for
    // too much.
    const request = pages(
      window_({ from: 0, to: 20, duration: 50, messages: [message("a", "A", 1, 2)] }),
      window_({ from: 20, to: 40, duration: 50, messages: [message("b", "B", 21, 22)] }),
      window_({ from: 40, to: 50, duration: 50, messages: [message("c", "C", 41, 42)] }),
    );

    const reading = await readWholeCyclicText("run-1");

    expect(request).toHaveBeenCalledTimes(3);
    expect(request.mock.calls[0][0].url).toContain("from_seconds=0");
    expect(request.mock.calls[1][0].url).toContain("from_seconds=20");
    expect(request.mock.calls[2][0].url).toContain("from_seconds=40");
    expect(reading.messages.map((m) => m.text)).toEqual(["A", "B", "C"]);
  });

  it("reports each window as it lands rather than only at the end", async () => {
    // The whole reason for paging: a window is a minute or two of OCR, so a
    // reading that only appeared at the end would leave the card blank for
    // as long as the single request used to take.
    pages(
      window_({ from: 0, to: 20, duration: 40, messages: [message("a", "A", 1, 2)] }),
      window_({ from: 20, to: 40, duration: 40, messages: [message("b", "B", 21, 22)] }),
    );
    const seen = [];

    await readWholeCyclicText("run-1", { onWindow: (r) => seen.push(r.messages.length) });

    expect(seen).toEqual([1, 2]);
  });

  it("stops rather than spinning when a window does not advance", async () => {
    // A clip whose header understates its duration would otherwise ask for
    // the same window forever.
    const request = pages(
      window_({ from: 0, to: 20, duration: 999, messages: [] }),
      window_({ from: 20, to: 20, duration: 999, messages: [] }),
    );

    await readWholeCyclicText("run-1");

    expect(request).toHaveBeenCalledTimes(2);
  });
});
