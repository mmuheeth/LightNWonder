import { useEffect } from "react";

import { THEMES, useUiStore } from "@/store/ui-store";

/**
 * Applies the selected theme as a `.dark` class on `<html>`.
 *
 * `index.css` declares `@custom-variant dark (&:is(.dark *))`, so that class is
 * what switches every `dark:` utility. "system" follows the OS setting live.
 */
export function ThemeProvider({ children }) {
  const theme = useUiStore((state) => state.theme);

  useEffect(() => {
    const root = document.documentElement;
    const query = window.matchMedia("(prefers-color-scheme: dark)");

    const apply = () => {
      const isDark =
        theme === THEMES.DARK || (theme === THEMES.SYSTEM && query.matches);
      root.classList.toggle("dark", isDark);
      root.style.colorScheme = isDark ? "dark" : "light";
    };

    apply();

    // Only follow the OS while the user has actually chosen "system".
    if (theme !== THEMES.SYSTEM) return undefined;
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, [theme]);

  return children;
}
