import { HealthCard } from "@/features/health/health-card";
import { IDeckPanel } from "@/features/ideck/ideck-panel";
import { ItemsPanel } from "@/features/items/items-panel";
import { ObsPanel } from "@/features/obs/obs-panel";
import { GameSelector } from "@/features/games/game-selector";

export function DashboardPage() {
  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-end sm:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-muted-foreground text-sm">
            One active game drives the i-deck and OBS window target.
          </p>
        </div>
        <GameSelector />
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <HealthCard />
        <ItemsPanel />
        <ObsPanel />
        <IDeckPanel />
      </div>
    </div>
  );
}
