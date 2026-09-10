import { useNavigate, useParams } from "react-router-dom";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { CyclicMessagesPanel } from "@/features/cyclic-messages/cyclic-messages-panel";
import { CyclicRunView } from "@/features/cyclic-messages/cyclic-run-view";
import { useCyclicRuns } from "@/features/cyclic-messages/use-cyclic-messages";
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
          {run.message_count}
        </Badge>
      </span>
      <span className="text-muted-foreground block font-mono text-xs">
        {formatRunId(run.run_id)}
      </span>
    </button>
  );
}

// Its own route rather than a dashboard card, for the same reason Event Capture
// is: a run is a list of full-width frames, and the message strip is a thin
// band of small text that does not survive half a row.
//
// The selected run lives in the URL, not state, so a run can be linked to —
// the panel above does exactly that after a run finishes.
export function CyclicMessagesPage() {
  const { runId } = useParams();
  const navigate = useNavigate();
  const { data: runs, error, isPending, refetch } = useCyclicRuns();

  const selected = runId ?? runs?.[0]?.run_id ?? null;

  return (
    <div className="space-y-6">
      <div className="space-y-1 border-b pb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Cyclic Messages</h1>
        <p className="text-muted-foreground text-sm">
          Every cyclic message run, with the frame taken for each message the
          strip showed.
        </p>
      </div>

      <CyclicMessagesPanel />

      {isPending ? (
        <Skeleton className="h-64 w-full" />
      ) : error ? (
        <ApiErrorAlert error={error} onRetry={() => refetch()} />
      ) : runs.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No runs yet. Start one from the Cyclic Messages card above.
        </p>
      ) : (
        <div className="grid gap-6 md:grid-cols-[16rem_1fr]">
          <nav className="space-y-2" aria-label="Cyclic message runs">
            {runs.map((run) => (
              <RunButton
                key={run.run_id}
                run={run}
                isSelected={run.run_id === selected}
                onSelect={(id) => navigate(`/cyclic-messages/${id}`)}
              />
            ))}
          </nav>

          {selected ? <CyclicRunView runId={selected} /> : null}
        </div>
      )}
    </div>
  );
}
