// Client-only UI state. Anything that lives on the server belongs in
// react-query instead — duplicating it here means stale reads.

import { create } from "zustand";
import { persist } from "zustand/middleware";

export const THEMES = Object.freeze({
  LIGHT: "light",
  DARK: "dark",
});

export const useUiStore = create(
  persist(
    (set, get) => ({
      theme: THEMES.LIGHT,

      /** Toggle light ↔ dark. */
      cycleTheme: () => {
        set({ theme: get().theme === THEMES.LIGHT ? THEMES.DARK : THEMES.LIGHT });
      },
    }),
    {
      name: "lnw-ui",
      version: 3,
      // Drop the removed "system" preference, and the sidebar preference before it.
      migrate: (persistedState) => ({
        theme: persistedState?.theme === THEMES.DARK ? THEMES.DARK : THEMES.LIGHT,
      }),
      partialize: (state) => ({ theme: state.theme }),
    },
  ),
);
