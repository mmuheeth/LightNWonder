import { HealthCard } from "@/features/health/health-card";
import { ItemsPanel } from "@/features/items/items-panel";

export function DashboardPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-muted-foreground text-sm">
          Scaffold running. The cards below talk to the FastAPI backend on port 8001
          through the Vite dev proxy.
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <HealthCard />
        <ItemsPanel />
      </div>
    </div>
  );
}
