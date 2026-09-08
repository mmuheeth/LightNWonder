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

import { useState } from "react";

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
import { Label } from "@/components/ui/label";
import { percent as asPercent } from "@/features/image-classifier/percent";
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

// Whether a tile can be named at all. Lives on this card rather than in the page
// header because it is a fact about the model, and this is the card that changes
// it -- a header badge said "untrained" beside a Train button that was the fix,
// two feet apart.
const STATE_BADGE = {
  ready: "secondary",
  training: "default",
  stale: "outline",
  untrained: "outline",
  not_installed: "destructive",
  disabled: "outline",
  error: "destructive",
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

/** Train the model, and watch it happen. */
export function TrainCard({ status, isFetching, refetch, train, cancel }) {
  const [engine, setEngine] = useState("");
  const engines = status?.architectures ?? [];
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
          Fits a network to the symbol artwork. A few minutes on this machine — it runs
          on the CPU, on half the cores, so the rest of the dashboard keeps working
          while it does. Each engine keeps its own model, so training one leaves the
          other alone and the two can be compared on the same split.
        </CardDescription>
        <CardAction className="flex items-center gap-2">
          {/* One badge, not two: while a run is going the state *is* "training",
              so the pulsing form replaces it rather than sitting beside it. */}
          {active ? (
            <Badge variant="destructive" className="gap-1">
              <Circle className="size-2 animate-pulse fill-current" />
              training
            </Badge>
          ) : status?.state ? (
            <Badge variant={STATE_BADGE[status.state] ?? "outline"}>
              {status.state}
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
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-56 space-y-1.5">
            <Label htmlFor="train-engine" className="text-xs">
              Engine
            </Label>
            {/* A styled native select, as this app does elsewhere rather than
                pulling in another primitive. */}
            <select
              id="train-engine"
              value={engine}
              onChange={(event) => setEngine(event.target.value)}
              disabled={active}
              className="border-input bg-background ring-offset-background focus-visible:ring-ring h-9 w-full rounded-md border px-3 py-1 text-sm disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
            >
              <option value="">
                Default ({engines.find((o) => o.is_default)?.label ?? "none"})
              </option>
              {engines.map((option) => (
                <option key={option.name} value={option.name}>
                  {option.label}
                  {option.trained
                    ? option.holdout_accuracy != null
                      ? ` · trained, ${asPercent(option.holdout_accuracy)}`
                      : " · trained"
                    : " · not trained"}
                </option>
              ))}
            </select>
          </div>

          <Button
            variant="outline"
            size="sm"
            onClick={() => train.mutate(engine ? { architecture: engine } : {})}
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
            <p className="text-sm">
              {run.architecture ? (
                <span className="text-muted-foreground font-mono text-xs">
                  {run.architecture} ·{" "}
                </span>
              ) : null}
              {run.message}
            </p>

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
