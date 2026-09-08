/** Image classifier client. */

import { routes } from "@/config/env";
import { apiRequest } from "@/lib/api";

const CLASSIFIER_URL = `${routes.API}/image-classifier`;

/**
 * The first status call of a session opens every training image to read its size and
 * opacity, which is about twelve seconds for two hundred files.
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

/** Name every tile of a split. */
export function classifyTiles(payload = {}, { signal } = {}) {
  return apiRequest({
    method: "POST",
    url: `${CLASSIFIER_URL}/classify`,
    data: payload,
    timeout: CLASSIFY_TIMEOUT_MS,
    signal,
  });
}
