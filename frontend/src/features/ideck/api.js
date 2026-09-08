/**
 * Virtual OLED i-deck client. The browser never touches the panel window —
 * which window, its layout, and press confirmation all come from the backend.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const IDECK_URL = `${routes.API}/ideck`;

/**
 * Fetch the panel status. Resolves even with the panel closed — check `state` rather
 * than catching. `access_denied` means the backend needs elevation.
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<{state:
 *   "unsupported"|"not_found"|"minimized"|"access_denied"|"ready", window_title: string,
 *   window_class: string, hwnd: number|null, client_width: number, client_height: number,
 *   panel_id: string|null, panel_width: number|null, panel_height: number|null, game:
 *   string, button_count: number, panel_xml: string, log_path: string, verify_presses:
 *   boolean}>}
 */
export function getIDeckStatus({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${IDECK_URL}/status`, signal });
}

/**
 * List every key on the deck, in layout order. `client_x`/`client_y` are populated only
 * while the panel is open and restored.
 * @param {{signal?: AbortSignal}} [options]
 * @returns {Promise<Array<{xml_id: string, button_id: number, panel_x: number, panel_y:
 *   number, width: number, height: number, client_x: number|null, client_y:
 *   number|null}>>}
 */
export function getIDeckButtons({ signal } = {}) {
  return apiRequest({ method: "GET", url: `${IDECK_URL}/buttons`, signal });
}

/**
 * Press one key by its layout name (case-insensitive). Unless verification is off,
 * resolves only once the panel's own log shows the press landing.
 * @param {{button: string, verify?: boolean, hold_seconds?: number}} payload
 * @returns {Promise<{button: string, xml_id: string, button_id: number, client_x:
 *   number, client_y: number, confirmed: boolean, verified: boolean, evidence:
 *   string|null, restored: boolean, refocused: boolean, elapsed_ms: number}>}
 */
export function pressIDeckButton(payload) {
  return apiRequest({ method: "POST", url: `${IDECK_URL}/press`, data: payload });
}

/**
 * Press several keys in order. Stops at the first failure.
 * @param {{buttons: string[], delay_seconds?: number, verify?: boolean}} payload
 */
export function pressIDeckSequence(payload) {
  return apiRequest({ method: "POST", url: `${IDECK_URL}/sequence`, data: payload });
}

/**
 * Check the panel accepts posted input, without pressing anything — posts a mouse move
 * only, so it's free of game side effects.
 * @returns {Promise<{supported: boolean, window_found: boolean, posted: boolean,
 *   observed: boolean, evidence: string|null, detail: string}>}
 */
export function probeIDeck() {
  return apiRequest({ method: "POST", url: `${IDECK_URL}/probe` });
}
