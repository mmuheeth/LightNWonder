/**
 * Client-only UI state.
 *
 * Zustand holds state the UI owns. Anything that lives on the server belongs in
 * react-query instead — duplicating server data here means two sources of truth
 * and stale reads.
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export const THEMES = Object.freeze({
  LIGHT: "light",
  DARK: "dark",
  SYSTEM: "system",
});

export const useUiStore = create(
  persist(
    (set, get) => ({
      theme: THEMES.SYSTEM,

      /** Cycle light → dark → system, so every option is reachable. */
      cycleTheme: () => {
        const order = [THEMES.LIGHT, THEMES.DARK, THEMES.SYSTEM];
        const next = order[(order.indexOf(get().theme) + 1) % order.length];
        set({ theme: next });
      },
    }),
    {
      name: "lnw-ui",
      version: 2,
      // Discard the removed sidebar preference while preserving the theme.
      migrate: (persistedState) => ({
        theme: persistedState?.theme ?? THEMES.SYSTEM,
      }),
      partialize: (state) => ({ theme: state.theme }),
    },
  ),
);
