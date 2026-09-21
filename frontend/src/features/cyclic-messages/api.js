/**
 * Cyclic Messages client. Tracking runs in the backend (it follows the game's
 * log, drives OBS and records a clip per win), so this only starts/stops a run
 * and reads what it produced.
 *
 * Same shape as `features/event-capture/api.js`, with one route fewer: a run
 * directory holds screenshots *and* the clips, so both come off one file route
 * rather than an images route and a video one.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const CYCLIC_URL = `${routes.API}/cyclic-messages`;

/**
 * Fetch the state of the run in progress. Resolves either way — check `active`
 * rather than catching.
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<{active: boolean, run_id: string|null, game: string|null,
 *   started_at: string|null, duration_ms: number, message_count: number,
 *   sampled_count: number, cycle_count: number, recording: boolean,
 *   video_count: number, sampling: boolean, recent_events: Array<object>,
 *   errors: string[]}>}
 */
export function getCyclicStatus({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${CYCLIC_URL}/status`, signal });
}

/**
 * Fetch the win presentation being captured right now — every frame of it, each
 * with whatever the backend has made of its caption so far.
 *
 * Separate from `getCyclicStatus` because it answers a different question at a
 * different size: status is a handful of counters, this is up to a few hundred
 * frames. Read off the live run rather than off its manifest, so a frame is
 * here as soon as OBS wrote it.
 *
 * Capturing and reading never happen at once — capturing every frame on
 * schedule is the backend's only priority while a pass is open, so nothing
 * reads a caption until the pass closes, and then every one of its frames is
 * read in one batch. `capturing` and `reading` are never both true; a frame's
 * `readings` is empty until that batch reaches it, and `queue_depth` counts
 * down from `frame_count` to 0 while it does. Empty is the only thing that
 * says "not yet": a frame the batch has reached carries one entry per region
 * even where the band was blank, so a read frame that said nothing is
 * non-empty and must not be rendered as one still waiting.
 *
 * `recovering` is the third state and the one that finishes the list. The
 * stills cannot keep up with either strip — an OBS screenshot costs seconds
 * while a recording is running and a caption stays up for about one — so each
 * window's own clip is read back afterwards and the captions the stills went
 * past arrive as `cyclic-message-recovered` frames. `recovery_pending` is how
 * many clips are still waiting; non-zero while a window is open is normal,
 * because recovery gives way to capture.
 *
 * `strip` says which of the two strips these frames are of, so a view of the
 * between-spins strip is not captioned as a win presentation.
 *
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<{active: boolean, run_id: string|null, game: string|null,
 *   cycle: number|null, strip: string|null, capturing: boolean,
 *   reading: boolean, recovering: boolean, recovery_pending: number,
 *   queue_depth: number, read_count: number, sample_rate: number,
 *   frame_count: number, frames: Array<object>, errors: string[]}>}
 */
export function getCyclicLive({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${CYCLIC_URL}/live`, signal });
}

/**
 * Start tracking the active game's cyclic messages. Connects to OBS first, so
 * this rejects with `OBS_CONNECTION_FAILED` or
 * `CYCLIC_MESSAGES_LOG_UNAVAILABLE`.
 */
export function startCyclic() {
  return apiRequest({ method: "POST", url: `${CYCLIC_URL}/start` });
}

/**
 * Stop tracking. The resolved value is the finished record, events and clips
 * included.
 */
export function stopCyclic() {
  return apiRequest({ method: "POST", url: `${CYCLIC_URL}/stop` });
}

/** Every run on disk, newest first. */
export function listCyclicRuns({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${CYCLIC_URL}/runs`, signal });
}

/** One run and all of its events. */
export function getCyclicRun(runId, { signal } = {}) {
  return apiRequest({
    method: "GET",
    url: `${CYCLIC_URL}/runs/${encodeURIComponent(runId)}`,
    signal,
  });
}

/**
 * How long to allow the clip reading, overriding the client's global
 * `VITE_API_TIMEOUT_MS` (15s) for this one call.
 *
 * It is the only request in the app that is *supposed* to take minutes. At one
 * frame a second a 90s clip is ~90 frames, and on a clip drawing two caption
 * bands that is ~160 OCR passes at ~2.8s each against a single in-process
 * PaddleOCR model — **measured at 492s**, which is why a request no longer
 * reads a whole clip.
 *
 * The backend caps a request at `CYCLIC_MESSAGES_TEXT_WINDOW_SECONDS` (20s) of
 * footage and `readWholeCyclicText` below pages through the rest, so what this
 * has to cover is one window: measured at 50-115s across the five windows of
 * that same clip. It stays at ~1.5x the *whole-clip* worst case anyway, and
 * deliberately *under* the 300s `requestTimeout` Node gives the Vite dev proxy
 * — past that the proxy cuts the connection first and the reason arrives as a
 * bare network error instead of this timeout. The headroom is the point: a
 * slower host makes a window take longer, not a request fail.
 */
const READ_TIMEOUT_MS = 290_000;

/**
 * Read one window of a clip: the backend cuts it into frames, crops the
 * caption bands out of each and OCRs the ones showing anything.
 *
 * Which bands depends on the clip: a win presentation draws one line and the
 * between-spins strip two, and the backend keys that off the clip's own `kind`
 * rather than off a fixed region.
 *
 * **A window, not the clip.** `fromSeconds` says where to start and the
 * reading says where it stopped; the backend clamps the window to its own
 * budget, so there is nothing to configure here and `readWholeCyclicText` is
 * the loop. Prefer that over calling this directly — on its own this reads
 * only the first window of a clip.
 *
 * @param {string} runId
 * @param {{cycle?: number, intervalSeconds?: number, fromSeconds?: number,
 *   toSeconds?: number, signal?: AbortSignal}} [options]
 */
export function readCyclicText(
  runId,
  { cycle, intervalSeconds, fromSeconds, toSeconds, signal } = {},
) {
  const params = new URLSearchParams();
  if (cycle != null) params.set("cycle", String(cycle));
  if (intervalSeconds != null) params.set("interval_seconds", String(intervalSeconds));
  if (fromSeconds != null) params.set("from_seconds", String(fromSeconds));
  if (toSeconds != null) params.set("to_seconds", String(toSeconds));
  const query = params.toString();
  return apiRequest({
    method: "GET",
    url: `${CYCLIC_URL}/runs/${encodeURIComponent(runId)}/text${query ? `?${query}` : ""}`,
    signal,
    timeout: READ_TIMEOUT_MS,
  });
}

/**
 * Stitch a window's reading onto the ones already read.
 *
 * Two joins, and both are done **by the `key` the backend puts on every
 * message and caption** rather than by the text shown. That key is the text
 * with case, spacing and punctuation taken out, which is the same rule the
 * backend groups frames by — it travels on the payload precisely so that
 * stitching here cannot drift from grouping there. The text shown is the
 * best-scoring *variant* of the readings behind it, so two showings of one
 * caption can differ character for character and joining on it would count
 * them twice.
 *
 * * **The seam between two windows.** A caption on screen when a window ends
 *   is still on screen when the next begins, so it is one showing reported
 *   twice. Merged when the trailing message's key matches the leading one's.
 * * **Captions across the whole clip.** The strip loops, so the same caption
 *   comes up in several windows, and `showings` counts them.
 */
export function stitchCyclicText(previous, next) {
  if (!previous) return next;

  const messages = [...previous.messages];
  const incoming = [...next.messages];
  const tail = messages.at(-1);
  const head = incoming[0];
  if (tail && head && tail.key && tail.key === head.key) {
    // One showing that straddled the seam, not two: the window boundary is an
    // artefact of how the clip was read and has no counterpart on the strip.
    messages[messages.length - 1] = {
      ...tail,
      text: head.confidence > tail.confidence ? head.text : tail.text,
      last_seen: head.last_seen,
      frames: tail.frames + head.frames,
      confidence: Math.max(tail.confidence, head.confidence),
      reliable: tail.reliable || head.reliable,
    };
    incoming.shift();
  }
  messages.push(...incoming);

  // Rebuilt from the merged messages rather than stitched from either side's
  // own caption list: `showings` is a count over the whole timeline, and the
  // seam merge above can have just turned two of them into one.
  const captions = [];
  const byKey = new Map();
  for (const message of messages) {
    const key = message.key || message.text;
    const seen = byKey.get(key);
    if (!seen) {
      const caption = {
        text: message.text,
        key,
        showings: 1,
        frames: message.frames,
        first_seen: message.first_seen,
        last_seen: message.last_seen,
        confidence: message.confidence,
        reliable: message.reliable,
      };
      byKey.set(key, caption);
      captions.push(caption);
      continue;
    }
    if (message.confidence > seen.confidence) seen.text = message.text;
    seen.showings += 1;
    seen.frames += message.frames;
    seen.last_seen = Math.max(seen.last_seen, message.last_seen);
    seen.confidence = Math.max(seen.confidence, message.confidence);
    seen.reliable = seen.reliable || message.reliable;
  }

  return {
    ...next,
    // The stretch read so far, which is what the progress line reports.
    from_seconds: previous.from_seconds,
    frames_sampled: previous.frames_sampled + next.frames_sampled,
    frames_read: previous.frames_read + next.frames_read,
    frames_unreadable: previous.frames_unreadable + next.frames_unreadable,
    frames: [...previous.frames, ...next.frames],
    messages,
    captions,
  };
}

/**
 * Read a whole clip, one window at a time, reporting each window as it lands.
 *
 * The backend caps a request at `CYCLIC_MESSAGES_TEXT_WINDOW_SECONDS` of
 * footage because a long clip cannot be read inside one — a 90s clip drawing
 * two caption bands is ~160 OCR passes and was measured at 492s against a
 * client that gives up at 290s. The budget therefore lives on the server and
 * this follows it rather than duplicating it: ask from where the last window
 * stopped, stop when it reaches the end of the clip.
 *
 * `onWindow` is called with the reading stitched *so far*, so the caller can
 * show captions filling in rather than a spinner for eight minutes.
 *
 * @param {string} runId
 * @param {{cycle?: number, intervalSeconds?: number, signal?: AbortSignal,
 *   onWindow?: (reading: object) => void}} [options]
 */
export async function readWholeCyclicText(
  runId,
  { cycle, intervalSeconds, signal, onWindow } = {},
) {
  let stitched = null;
  let from = 0;
  // Bounded rather than `while (true)`: every window has to advance past the
  // one before it, and a server that answered without advancing would
  // otherwise spin here forever. The cap is generous against 20s windows over
  // the longest clip a run can legitimately record.
  for (let window = 0; window < 200; window += 1) {
    const reading = await readCyclicText(runId, {
      cycle,
      intervalSeconds,
      fromSeconds: from,
      signal,
    });
    stitched = stitchCyclicText(stitched, reading);
    onWindow?.(stitched);
    // `to_seconds` is the server's answer rather than what was asked for, so
    // it is what paging follows. Both exits matter: reaching the end of the
    // clip is the ordinary one, and a window that did not advance is the
    // guard against a clip whose header understates its duration.
    if (reading.to_seconds >= reading.duration_seconds) break;
    if (reading.to_seconds <= from) break;
    from = reading.to_seconds;
  }
  return stitched;
}

/**
 * URL of one file from a run — a screenshot for an `<img>` src or one win's
 * clip for a `<video>` src. The backend serves both from one route because a
 * run directory holds both, and neither element can unwrap the JSON envelope.
 */
export function cyclicFileUrl(runId, fileName) {
  return `${CYCLIC_URL}/runs/${encodeURIComponent(runId)}/files/${encodeURIComponent(fileName)}`;
}
