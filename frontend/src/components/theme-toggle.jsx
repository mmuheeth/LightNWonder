import { Monitor, Moon, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";
import { THEMES, useUiStore } from "@/store/ui-store";

const ICONS = {
  [THEMES.LIGHT]: Sun,
  [THEMES.DARK]: Moon,
  [THEMES.SYSTEM]: Monitor,
};

/** Cycles light → dark → system. */
export function ThemeToggle() {
  const theme = useUiStore((state) => state.theme);
  const cycleTheme = useUiStore((state) => state.cycleTheme);
  const Icon = ICONS[theme] ?? Monitor;

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={cycleTheme}
      aria-label={`Theme: ${theme}. Click to change.`}
      title={`Theme: ${theme}`}
    >
      <Icon className="size-4" />
    </Button>
  );
}
