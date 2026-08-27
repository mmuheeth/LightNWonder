import {
  Circle,
  CircleCheck,
  CircleMinus,
  CircleX,
  Dumbbell,
  LoaderCircle,
  RefreshCw,
  Square,
} from "lucide-react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Figure, FigureGrid } from "@/components/figure";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

// The same five looks the spin timeline uses, so a stage list reads the same
// wherever it appears. `pending` is drawn, not omitted: the backend creates every
// stage up front precisely so a run that died at stage two shows the three it
// never reached.
const LOOKS = {
  pending: {
    Icon: Circle,
    icon: "text-muted-foreground/40",
    text: "text-muted-foreground/60",
  },
  running: {
    Icon: LoaderCircle,
    icon: "text-primary animate-spin",
    text: "font-medium",
  },
  completed: { Icon: CircleCheck, icon: "text-emerald-500", text: "" },
  skipped: {
    Icon: CircleMinus,
    icon: "text-muted-foreground/50",
    text: "text-muted-foreground",
  },
  failed: {
    Icon: CircleX,
    icon: "text-destructive",
    text: "text-destructive font-medium",
  },
};

const RUN_BADGE = {
  running: "default",
  completed: "secondary",
  cancelled: "outline",
  failed: "destructive",
};

function percent(value) {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
}

function seconds(value) {
  if (typeof value !== "number" || value <= 0) return "—";
  if (value < 90) return `${value.toFixed(0)}s`;
  return `${(value / 60).toFixed(1)} min`;
}

function StageRow({ stage }) {
  const look = LOOKS[stage.state] ?? LOOKS.pending;
  const { Icon } = look;
  return (
    <li className="flex items-center gap-2 text-sm">
      <Icon className={cn("size-4 shrink-0", look.icon)} />
      <span className={cn("flex-1", look.text)}>{stage.label}</span>
      {stage.duration_ms ? (
        <span className="text-muted-foreground font-mono text-[0.65rem] tabular-nums">
          {(stage.duration_ms / 1000).toFixed(1)}s
        </span>
      ) : null}
      {stage.error ? (
        <span className="text-destructive text-[0.65rem]">{stage.error}</span>
      ) : null}
    </li>
  );
}

/**
 * Train the model, and watch it happen.
 *
 * The run is minutes long, so this shows the stage list and the per-epoch figures
 * rather than a bare spinner: a four-minute wait with no detail is
 * indistinguishable from a hang.
 */
export function TrainCard({ status, isFetching, refetch, train, cancel }) {
  const run = status?.training ?? null;
  const active = Boolean(status?.active);
  const canTrain =
    !active &&
    status?.state !== "disabled" &&
    status?.state !== "not_installed" &&
    Boolean(status?.dataset?.exists);
  const busy = train.isPending || cancel.isPending;
  const actionError = train.error ?? cancel.error;
  const latest = run?.epochs?.length ? run.epochs[run.epochs.length - 1] : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Dumbbell className="size-4" />
          Training
        </CardTitle>
        <CardDescription>
          Fits EfficientNet-B0 to the symbol artwork. About four minutes on this machine
          — it runs on the CPU, on half the cores, so the rest of the dashboard keeps
          working while it does.
        </CardDescription>
        <CardAction className="flex items-center gap-2">
          {active ? (
            <Badge variant="destructive" className="gap-1">
              <Circle className="size-2 animate-pulse fill-current" />
              training
            </Badge>
          ) : null}
          <Button
            variant="ghost"
            size="icon"
            onClick={() => refetch()}
            disabled={isFetching}
            aria-label="Refresh classifier status"
          >
            <RefreshCw className={cn("size-4", isFetching && "animate-spin")} />
          </Button>
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => train.mutate({})}
            disabled={!canTrain || busy}
          >
            <Dumbbell />
            {train.isPending ? "Starting…" : "Train"}
          </Button>
          {active ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => cancel.mutate()}
              disabled={busy || run?.cancel_requested}
            >
              <Square />
              {run?.cancel_requested ? "Stopping…" : "Cancel"}
            </Button>
          ) : null}
          {run ? (
            <Badge variant={RUN_BADGE[run.state] ?? "outline"}>{run.state}</Badge>
          ) : null}
        </div>

        {actionError ? <ApiErrorAlert error={actionError} /> : null}

        {run ? (
          <div className="space-y-3 border-t pt-4">
            <p className="text-sm">{run.message}</p>

            {/* Hand-rolled rather than a new dependency, the same way this app
                hand-rolls its selects and uses native <details>. */}
            <div className="bg-muted h-1.5 w-full overflow-hidden rounded-full">
              <div
                className="bg-primary h-full rounded-full transition-[width] duration-500"
                style={{ width: `${Math.round((run.progress ?? 0) * 100)}%` }}
              />
            </div>

            <FigureGrid>
              <Figure
                label="Epoch"
                value={`${run.epoch}/${run.epoch_total}`}
                hint={latest ? latest.stage : "not started"}
              />
              <Figure
                label="Loss"
                value={latest ? latest.loss.toFixed(4) : "—"}
                hint="lower is better"
              />
              <Figure
                label="Batch accuracy"
                value={latest ? percent(latest.accuracy) : "—"}
                hint="on augmented training pictures"
              />
            </FigureGrid>

            <ul className="space-y-1.5">
              {run.stages.map((stage) => (
                <StageRow key={stage.key} stage={stage} />
              ))}
            </ul>

            {run.error ? (
              <p className="text-destructive text-xs">
                {run.error}
                {run.error_code ? (
                  <span className="text-muted-foreground font-mono">
                    {" "}
                    · {run.error_code}
                  </span>
                ) : null}
              </p>
            ) : null}

            {run.state === "running" ? (
              <p className="text-muted-foreground text-xs">
                Roughly {seconds(run.estimated_seconds)} in total.
              </p>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
