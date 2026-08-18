/**
 * The configured axios instance. Prefer the helpers in `@/lib/api` over using
 * this directly — they unwrap the response envelope for you.
 */

import axios from "axios";

import { env } from "@/config/env";
import { ApiError } from "@/lib/api-error";

/** Generate a correlation id in the format the backend uses (hex, no dashes). */
function newRequestId() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID().replaceAll("-", "");
  }
  return Array.from({ length: 32 }, () =>
    Math.floor(Math.random() * 16).toString(16),
  ).join("");
}

export const http = axios.create({
  baseURL: env.apiBaseUrl,
  timeout: env.apiTimeoutMs,
  withCredentials: env.apiWithCredentials,
  headers: { Accept: "application/json" },
});

// Send a request id the backend will adopt and echo, so one id ties a browser
// action to its server logs.
http.interceptors.request.use((config) => {
  config.headers["X-Request-ID"] ??= newRequestId();
  return config;
});

// Every rejection becomes an ApiError, so callers never see an AxiosError.
http.interceptors.response.use(
  (response) => response,
  (error) => Promise.reject(ApiError.from(error)),
);
