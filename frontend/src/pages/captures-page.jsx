import { useNavigate, useParams } from "react-router-dom";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { CaptureRunView } from "@/features/event-capture/capture-run-view";
import { EventCapturePanel } from "@/features/event-capture/event-capture-panel";
import { useCaptureRuns } from "@/features/event-capture/use-event-capture";
import { cn } from "@/lib/utils";

const STATUS_VARIANTS = {
  completed: "secondary",
  running: "destructive",
  interrupted: "outline",
};

/** A run id is `YYYY-MM-DD_HH-MM-SS`; show it as something readable. */
function formatRunId(runId) {
  const [date, time] = runId.split("_");
  return time ? `${date} ${time.replaceAll("-", ":")}` : runId;
}

function RunButton({ run, isSelected, onSelect }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(run.run_id)}
      className={cn(
        "w-full rounded-md border px-3 py-2 text-left text-sm transition-colors",
        isSelected ? "bg-secondary border-foreground/20" : "hover:bg-accent",
      )}
    >
      <span className="flex items-center justify-between gap-2">
        <span className="font-medium">{run.game}</span>
        <Badge variant={STATUS_VARIANTS[run.status] ?? "outline"}>
          {run.event_count}
        </Badge>
      </span>
      <span className="text-muted-foreground block font-mono text-xs">
        {formatRunId(run.run_id)}
      </span>
    </button>
  );
}

// The selected run lives in the URL, not state, so a run can be linked to —
// the capture card above does exactly that after a run finishes.
export function CapturesPage() {
  const { runId } = useParams();
  const navigate = useNavigate();
  const { data: runs, error, isPending, refetch } = useCaptureRuns();

  const selected = runId ?? runs?.[0]?.run_id ?? null;

  return (
    <div className="space-y-6">
      <div className="space-y-1 border-b pb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Event Capture</h1>
        <p className="text-muted-foreground text-sm">
          Every event-based capture run, with the screenshot taken for each event.
        </p>
      </div>

      <EventCapturePanel />

      {isPending ? (
        <Skeleton className="h-64 w-full" />
      ) : error ? (
        <ApiErrorAlert error={error} onRetry={() => refetch()} />
      ) : runs.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No runs yet. Start one from the Event Based Capture card above.
        </p>
      ) : (
        <div className="grid gap-6 md:grid-cols-[16rem_1fr]">
          <nav className="space-y-2" aria-label="Capture runs">
            {runs.map((run) => (
              <RunButton
                key={run.run_id}
                run={run}
                isSelected={run.run_id === selected}
                onSelect={(id) => navigate(`/captures/${id}`)}
              />
            ))}
          </nav>

          {selected ? <CaptureRunView runId={selected} /> : null}
        </div>
      )}
    </div>
  );
}
