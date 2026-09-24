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
 * How long a Stop may take, overriding the global 15s for this one call.
 *
 * A stop is not instant, and 15s was cutting it off: the backend lets the
 * capture loop finish its frame (up to 10s), lets a clip recovery give way (up
 * to 30s), and then reads every still the last window left unread before it
 * seals the record — at ~3.5s a read under load, a between-spins window left
 * mid-read is minutes of it. The stop completes either way; with the short
 * timeout the card just said TIMEOUT over a run that had actually stopped. The
 * same ceiling as `READ_TIMEOUT_MS`, for the same Vite-proxy reason.
 */
const STOP_TIMEOUT_MS = 290_000;

/**
 * Stop tracking. The resolved value is the finished record, events and clips
 * included.
 */
export function stopCyclic() {
  return apiRequest({
    method: "POST",
    url: `${CYCLIC_URL}/stop`,
    timeout: STOP_TIMEOUT_MS,
  });
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
 * frame a second a 90s clip is ~90 OCR passes, and the default engine
 * (PaddleOCR, recognise-only) reads them one at a time against a single
 * in-process model: **measured at 188s for a 95.4s clip**, reading plus
 * decoding. Tesseract, still selectable, is far quicker but reads this artwork
 * worse.
 *
 * So this is ~1.5x the measured worst case, and deliberately *under* the 300s
 * `requestTimeout` Node gives the Vite dev proxy — past that the proxy cuts the
 * connection first and the reason arrives as a bare network error instead of
 * this timeout. There is no more room to take: a clip long enough to need more
 * than this wants a bigger `interval_seconds`, not a bigger number here.
 */
const READ_TIMEOUT_MS = 290_000;

/**
 * Read the messages out of one clip: the backend cuts it into frames, crops the
 * caption bands out of each and OCRs the ones where the caption changed.
 *
 * Which bands depends on the clip: a win presentation draws one line and the
 * between-spins strip two, and the backend keys that off the clip's own `kind`
 * rather than off a fixed region.
 *
 * Still slow — see `READ_TIMEOUT_MS` — which is why it is a button rather than
 * something the run view fetches on mount. No longer *minutes* slow, though:
 * reading every decoded frame rather than one per caption is what used to put
 * a long clip past the timeout, and `frames_read` beside `frames_sampled` on
 * the reading says what it actually spent.
 *
 * @param {string} runId
 * @param {{cycle?: number, intervalSeconds?: number, signal?: AbortSignal}} [options]
 */
export function readCyclicText(runId, { cycle, intervalSeconds, signal } = {}) {
  const params = new URLSearchParams();
  if (cycle != null) params.set("cycle", String(cycle));
  if (intervalSeconds != null) params.set("interval_seconds", String(intervalSeconds));
  const query = params.toString();
  return apiRequest({
    method: "GET",
    url: `${CYCLIC_URL}/runs/${encodeURIComponent(runId)}/text${query ? `?${query}` : ""}`,
    signal,
    timeout: READ_TIMEOUT_MS,
  });
}

/**
 * URL of one file from a run — a screenshot for an `<img>` src or one win's
 * clip for a `<video>` src. The backend serves both from one route because a
 * run directory holds both, and neither element can unwrap the JSON envelope.
 */
export function cyclicFileUrl(runId, fileName) {
  return `${CYCLIC_URL}/runs/${encodeURIComponent(runId)}/files/${encodeURIComponent(fileName)}`;
}
