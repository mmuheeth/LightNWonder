import { EventCapturePanel } from "@/features/event-capture/event-capture-panel";
import { GridPanel } from "@/features/grid/grid-panel";
import { IDeckPanel } from "@/features/ideck/ideck-panel";
import { ObsPanel } from "@/features/obs/obs-panel";
import { PaylinePanel } from "@/features/paylines/payline-panel";
import { RoiPanel } from "@/features/roi/roi-panel";
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
        <ObsPanel />
        <IDeckPanel />
        <EventCapturePanel />
        <RoiPanel />
        <GridPanel />
      </div>

      {/* Its own row rather than a cell of the two-column grid: the
          annotated reels are a wide picture and the per-line evidence is a
          long list, and neither survives half a row. */}
      <PaylinePanel />
    </div>
  );
}
