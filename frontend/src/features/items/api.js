/**
 * Example resource client. Mirrors the backend's example `items` endpoints;
 * delete alongside them.
 */

import { routes } from "@/config/env";
import { apiRequest, apiRequestPage } from "@/lib/api";

const ITEMS_URL = `${routes.API}/items`;

/**
 * List items.
 *
 * @param {{page?: number, pageSize?: number, signal?: AbortSignal}} [params]
 * @returns {Promise<{items: object[], pagination: object|null}>}
 */
export function listItems({ page = 1, pageSize = 20, signal } = {}) {
  return apiRequestPage({
    method: "GET",
    url: ITEMS_URL,
    params: { page, page_size: pageSize },
    signal,
  });
}

/** Fetch one item by id. */
export function getItem(id, { signal } = {}) {
  return apiRequest({ method: "GET", url: `${ITEMS_URL}/${id}`, signal });
}

/**
 * Create an item.
 *
 * @param {{name: string, description?: string|null, quantity?: number}} payload
 */
export function createItem(payload) {
  return apiRequest({ method: "POST", url: ITEMS_URL, data: payload });
}

/** Apply a partial update. */
export function updateItem(id, payload) {
  return apiRequest({ method: "PATCH", url: `${ITEMS_URL}/${id}`, data: payload });
}

/** Delete an item. */
export function deleteItem(id) {
  return apiRequest({ method: "DELETE", url: `${ITEMS_URL}/${id}` });
}
