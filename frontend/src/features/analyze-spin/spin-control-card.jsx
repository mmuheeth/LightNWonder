import { Circle, Film, Play, Square, Wifi, WifiOff } from "lucide-react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { StatRow } from "@/components/stat-row";
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
import { Switch } from "@/components/ui/switch";
import { spinFrameUrl } from "@/features/analyze-spin/api";
import { cn } from "@/lib/utils";

/** What fixes a refused start; the backend's own message says what happened. */
const ERROR_HINTS = {
  SPIN_ANALYSIS_UNAVAILABLE:
    "Start the game so it begins writing its log, then try again.",
  SPIN_ANALYSIS_ALREADY_RUNNING: "Wait for the run to finish, or cancel it.",
};

const RUN_STATE_LOOKS = {
  running: { variant: "destructive", label: "running" },
  completed: { variant: "secondary", label: "completed" },
  failed: { variant: "destructive", label: "failed" },
  cancelled: { variant: "outline", label: "cancelled" },
};

function formatDuration(ms) {
  if (typeof ms !== "number" || ms <= 0) return "0.0s";
  return `${(ms / 1000).toFixed(1)}s`;
}

/** The two or three screenshots the spin took, in the order it took them. */
function Frames({ frames }) {
  if (frames.length === 0) return null;

  return (
    <div className="space-y-2 border-t pt-4">
      <h3 className="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.16em] uppercase">
        {frames.length} screenshot{frames.length === 1 ? "" : "s"}
      </h3>
      <div className="grid gap-3 sm:grid-cols-3">
        {frames.map((frame) => (
          <figure key={frame.key} className="space-y-1">
            <img
              src={spinFrameUrl(frame.file_name)}
              alt={frame.label}
              loading="lazy"
              className={cn(
                "bg-muted w-full rounded-md border",
                frame.blank && "border-destructive",
              )}
            />
            <figcaption className="text-muted-foreground text-[0.65rem]">
              {frame.label}
              {frame.attempts > 1 ? (
                <span className="text-muted-foreground/70">
                  {" "}
                  · {frame.attempts} tries
                </span>
              ) : null}
            </figcaption>
            {/* Said here as well as on the step, because this is where a reader
                can see that it is true. */}
            {frame.blank ? (
              <p className="text-destructive text-[0.65rem]">
                OBS captured nothing — every reading off this frame is meaningless
              </p>
            ) : null}
          </figure>
        ))}
      </div>
    </div>
  );
}

/**
 * The button, and the run it started.
 *
 * One press drives the whole sequence, so there is one button — everything else
 * on this card is the answer to "what is it doing now". `connected` is shown
 * because progress arrives on a socket: a page that quietly stopped updating
 * looks exactly like a spin that quietly stalled, and only this tells them
 * apart.
 */
export function SpinControlCard({
  run,
  active,
  connected,
  start,
  cancel,
  record,
  onRecordChange,
}) {
  const busy = start.isPending || cancel.isPending;
  const actionError = start.error ?? cancel.error;
  const hint = actionError ? ERROR_HINTS[actionError.code] : null;
  const look = run ? (RUN_STATE_LOOKS[run.state] ?? RUN_STATE_LOOKS.running) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Play className="size-4" />
          Analyze Spin
        </CardTitle>
        <CardDescription>
          Spin once, record it, and validate the meter and the paylines against the
          maths the game has loaded
        </CardDescription>
        <CardAction>
          <Badge
            variant="outline"
            className={cn(
              "font-mono text-[0.65rem]",
              connected ? "text-muted-foreground" : "text-destructive",
            )}
            title={
              connected
                ? "Following the run over its progress stream"
                : "The progress stream is not connected; reconnecting"
            }
          >
            {connected ? <Wifi /> : <WifiOff />}
            {connected ? "live" : "offline"}
          </Badge>
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-4">
          <Button
            size="sm"
            onClick={() => {
              cancel.reset();
              start.mutate({ record });
            }}
            disabled={active || busy}
          >
            <Play className="fill-current" />
            Initiate Spin
          </Button>
          <div className="flex items-center gap-2">
            <Switch
              id="analyze-spin-record"
              checked={record}
              onCheckedChange={onRecordChange}
              disabled={active || busy}
            />
            <Label
              htmlFor="analyze-spin-record"
              className="text-muted-foreground text-sm font-normal"
            >
              <Film className="size-3.5" />
              Record video
            </Label>
          </div>
          {active ? (
            <Button
              variant="destructive"
              size="sm"
              onClick={() => cancel.mutate()}
              disabled={busy}
            >
              <Square />
              Cancel
            </Button>
          ) : null}
        </div>

        {actionError ? (
          <div className="space-y-1">
            <ApiErrorAlert error={actionError} />
            {hint ? <p className="text-muted-foreground text-xs">{hint}</p> : null}
          </div>
        ) : null}

        {run ? (
          <>
            <div className="space-y-3 border-t pt-4">
              <StatRow label="Run">
                {active ? (
                  <Badge variant="destructive">
                    <Circle className="size-2 animate-pulse fill-current" />
                    {run.message}
                  </Badge>
                ) : (
                  <Badge variant={look.variant}>{look.label}</Badge>
                )}
              </StatRow>
              {!active ? (
                <StatRow label="Result">
                  <span className="text-sm">{run.message}</span>
                </StatRow>
              ) : null}
              <StatRow label="Spin">
                {run.outcome === "win" ? (
                  <Badge className="border-emerald-500/30 bg-emerald-500/15 text-emerald-700 dark:text-emerald-400">
                    won
                  </Badge>
                ) : run.outcome === "no-win" ? (
                  <Badge variant="outline" className="text-muted-foreground">
                    paid nothing
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-muted-foreground">
                    unknown
                  </Badge>
                )}
              </StatRow>
              <StatRow label="Game">{run.label}</StatRow>
              <StatRow label="Elapsed">{formatDuration(run.duration_ms)}</StatRow>
              <StatRow label="Id">
                <span className="font-mono text-xs">{run.run_id}</span>
              </StatRow>
              {run.recording?.output_path ? (
                <StatRow label="Recording">
                  <span className="text-muted-foreground inline-flex items-center gap-1.5 font-mono text-[0.7rem] break-all">
                    <Film className="size-3.5 shrink-0" />
                    {run.recording.output_path}
                  </span>
                </StatRow>
              ) : null}
            </div>

            <Frames frames={run.frames ?? []} />

            {/* The failures a step already named, gathered once: a run that
                went wrong in two places should say so without the reader
                having to scan twelve steps for the red ones. */}
            {run.errors?.length > 0 ? (
              <ul className="space-y-1 border-t pt-4">
                {run.errors.map((message) => (
                  <li key={message} className="text-destructive text-xs break-words">
                    {message}
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        ) : (
          <p className="text-muted-foreground text-sm">
            No spin analysed yet. The game and OBS need to be running, and the i-deck
            panel open.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
