import { useEffect } from "react";

import { THEMES, useUiStore } from "@/store/ui-store";

// Applies the theme as a `.dark` class on `<html>`, which `index.css`'s
// `@custom-variant dark` uses to switch every `dark:` utility.
export function ThemeProvider({ children }) {
  const theme = useUiStore((state) => state.theme);

  useEffect(() => {
    const isDark = theme === THEMES.DARK;
    const root = document.documentElement;
    root.classList.toggle("dark", isDark);
    root.style.colorScheme = isDark ? "dark" : "light";
  }, [theme]);

  return children;
}
