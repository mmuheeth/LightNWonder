/**
 * Client-only UI state (theme, sidebar).
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
      sidebarOpen: true,

      setTheme: (theme) => set({ theme }),

      /** Cycle light → dark → system, so every option is reachable. */
      cycleTheme: () => {
        const order = [THEMES.LIGHT, THEMES.DARK, THEMES.SYSTEM];
        const next = order[(order.indexOf(get().theme) + 1) % order.length];
        set({ theme: next });
      },

      toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
      setSidebarOpen: (sidebarOpen) => set({ sidebarOpen }),
    }),
    {
      name: "lnw-ui",
      version: 1,
      // Persist only real preferences; transient flags should reset on reload.
      partialize: (state) => ({ theme: state.theme, sidebarOpen: state.sidebarOpen }),
    },
  ),
);
