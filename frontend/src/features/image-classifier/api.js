/**
 * Image classifier client.
 *
 * Two calls here are slow in ways the shared 15s ceiling does not allow for, and
 * each says why it gets its own.
 */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const CLASSIFIER_URL = `${routes.API}/image-classifier`;

/**
 * The first status call of a session opens every training image to read its size
 * and opacity, which is about twelve seconds for two hundred files. The backend
 * caches the result on the dataset's fingerprint, so only the first one is slow —
 * but that first one would time out at the default and look like a dead endpoint.
 */
const STATUS_TIMEOUT_MS = 60_000;

/**
 * Classifying is one forward pass over fifteen tiles, but on CPU with the model
 * still to be read off disk that is comfortably past 15s the first time.
 */
const CLASSIFY_TIMEOUT_MS = 120_000;

/** Engine state, the trained model, the dataset, and any live training run. */
export function getClassifierStatus({ signal } = {}) {
  return apiRequest({
    method: "GET",
    url: `${CLASSIFIER_URL}/status`,
    timeout: STATUS_TIMEOUT_MS,
    signal,
  });
}

/** Per-class counts, and the warnings a file listing cannot show. */
export function getDataset({ signal } = {}) {
  return apiRequest({
    method: "GET",
    url: `${CLASSIFIER_URL}/dataset`,
    timeout: STATUS_TIMEOUT_MS,
    signal,
  });
}

/** Every split the reel grid has written, newest first. */
export function getSplits({ signal } = {}) {
  return apiRequest({
    method: "GET",
    url: `${CLASSIFIER_URL}/splits`,
    signal,
  });
}

/**
 * Start a training run. Returns as soon as it is under way, not when it is done —
 * the run takes minutes, so its progress is read from `/status`.
 */
export function trainClassifier(payload = {}) {
  return apiRequest({
    method: "POST",
    url: `${CLASSIFIER_URL}/train`,
    data: payload,
  });
}

/** Ask the run in progress to stop. */
export function cancelTraining() {
  return apiRequest({ method: "POST", url: `${CLASSIFIER_URL}/train/cancel` });
}

/**
 * Name every tile of a split.
 *
 * Confidences are softmax probabilities over the *trained* symbols only, so a
 * tile showing something the model has no class for cannot come back as "none of
 * these" — it comes back below `min_confidence`, as `symbol: null`, with its
 * ranked candidates still attached.
 */
export function classifyTiles(payload = {}, { signal } = {}) {
  return apiRequest({
    method: "POST",
    url: `${CLASSIFIER_URL}/classify`,
    data: payload,
    timeout: CLASSIFY_TIMEOUT_MS,
    signal,
  });
}
