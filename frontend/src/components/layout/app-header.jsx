import { Lightbulb } from "lucide-react";
import { NavLink } from "react-router-dom";

import { ThemeToggle } from "@/components/theme-toggle";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  // `end` only for "/", which would otherwise match every route. The others
  // want their child routes to keep them highlighted -- /captures/:runId is
  // still the Captures page.
  { to: "/", label: "Dashboard", end: true },
  { to: "/event-captures", label: "Event Capture" },
  { to: "/game-config", label: "Game Config" },
  { to: "/analyze-spin", label: "Analyze Spin" },
  { to: "/symbol-validation", label: "Symbol Validation" },
];

export function AppHeader() {
  return (
    <header className="bg-background/80 sticky top-0 z-10 border-b backdrop-blur">
      <div className="mx-auto flex w-full max-w-5xl items-center gap-6 px-4 py-3">
        <NavLink to="/" className="flex items-center gap-2 font-semibold">
          <Lightbulb className="size-5" />
          LightNWonder
        </NavLink>

        <nav className="flex flex-1 items-center gap-1">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end ?? false}
              className={({ isActive }) =>
                cn(
                  "rounded-md px-3 py-1.5 text-sm transition-colors",
                  isActive
                    ? "bg-secondary text-secondary-foreground font-medium"
                    : "text-muted-foreground hover:text-foreground",
                )
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <ThemeToggle />
      </div>
    </header>
  );
}
