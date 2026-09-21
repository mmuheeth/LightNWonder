import { useEffect, useRef, useState } from "react";

import {
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CircleMinus,
  History,
  Loader2,
  RefreshCw,
} from "lucide-react";

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
import { Skeleton } from "@/components/ui/skeleton";
import { replayImageUrl } from "@/features/replay/api";
import { useReplayStatus, useRunReplay } from "@/features/replay/use-replay";
import { cn } from "@/lib/utils";

const STATE_VARIANTS = {
  ready: "default",
  not_found: "secondary",
  access_denied: "destructive",
  unsupported: "outline",
};

/** How each window reads in the readiness rows. */
const WINDOW_LABELS = {
  devtool: "DevTool",
  "system-admin": "System Admin",
  game: "Game",
};

/** Why a window's state blocks a run, in the user's terms. */
const STATE_HINTS = {
  not_found: "is not running, so the sequence has no window to drive.",
  unsupported: "control needs the Windows API, which this host lacks.",
  access_denied:
    "runs at a higher integrity level than the backend, so Windows refuses it " +
    "input. Restart the backend elevated (start.ps1 does).",
};

/**
 * The System Admin window is only opened *by* the sequence, so it being
 * closed says nothing about whether a run can start.
 */
const REQUIRED_WINDOWS = new Set(["devtool", "game"]);

const STEP_ICONS = {
  completed: CircleCheck,
  failed: CircleAlert,
  skipped: CircleMinus,
  running: Loader2,
  pending: CircleDashed,
};

const STEP_TONES = {
  completed: "text-emerald-600 dark:text-emerald-500",
  failed: "text-destructive",
  skipped: "text-muted-foreground",
  running: "text-primary",
  pending: "text-muted-foreground/50",
};

const LOG_TONES = {
  info: "text-muted-foreground",
  warning: "text-amber-600 dark:text-amber-500",
  error: "text-destructive",
};

function formatDuration(ms) {
  if (typeof ms !== "number" || ms <= 0) return null;
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

/** Wall-clock time of one log line. Naive timestamps, so local either way. */
function formatTime(value) {
  const at = new Date(value);
  return Number.isNaN(at.valueOf()) ? "" : at.toLocaleTimeString([], { hour12: false });
}

/** One step of the run: how it went, and what it proved. */
function StepRow({ step }) {
  const Icon = STEP_ICONS[step.state] ?? CircleDashed;
  const elapsed = formatDuration(step.duration_ms);

  return (
    <li className="flex items-start gap-2 text-sm">
      <Icon
        className={cn(
          "mt-0.5 size-4 shrink-0",
          STEP_TONES[step.state],
          step.state === "running" && "animate-spin",
        )}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex items-baseline justify-between gap-2">
          <span
            className={cn(
              "truncate",
              step.state === "pending" && "text-muted-foreground",
            )}
          >
            {step.label}
          </span>
          <span className="text-muted-foreground shrink-0 font-mono text-xs">
            {elapsed}
          </span>
        </div>
        {step.error ? (
          <p className="text-destructive text-xs">{step.error}</p>
        ) : step.detail ? (
          <p className="text-muted-foreground text-xs">
            {/* A completed step that proved nothing is worth saying out loud.
                One step has nothing it can read back: what View starts happens
                in the game's window, which the menu's page knows nothing of. */}
            {step.state === "completed" && !step.confirmed ? "unconfirmed — " : null}
            {step.detail}
          </p>
        ) : null}
      </div>
    </li>
  );
}

/**
 * The run's own commentary as it happens.
 *
 * Kept scrolled to the newest line while a run is live, because that is the
 * one being waited on — a log that has to be scrolled to be current is not a
 * progress indicator.
 */
function RunLog({ logs, live }) {
  const box = useRef(null);
  const last = logs.length > 0 ? logs[logs.length - 1].sequence : -1;

  useEffect(() => {
    const node = box.current;
    if (node && live) node.scrollTop = node.scrollHeight;
  }, [last, live]);

  if (logs.length === 0) return null;

  return (
    <ol
      ref={box}
      aria-label="Replay progress log"
      className="bg-muted/40 max-h-48 space-y-1 overflow-y-auto rounded-md border p-2 font-mono text-xs"
    >
      {logs.map((entry) => (
        <li key={entry.sequence} className="flex gap-2">
          <span className="text-muted-foreground/70 shrink-0">
            {formatTime(entry.at)}
          </span>
          <span className={cn("break-words", LOG_TONES[entry.level])}>
            {entry.message}
          </span>
        </li>
      ))}
    </ol>
  );
}

/**
 * The picture a run took, served as a file by the backend.
 *
 * A file the backend will not serve is worth saying out loud rather than
 * leaving as a broken-image icon: the record says a screenshot was written and
 * not blank, so a reader looking at a broken picture has no way to tell that
 * from "the replay itself came out empty".
 */
function ReplayShot({ shot }) {
  const [failed, setFailed] = useState(false);

  if (failed) {
    return (
      <p className="text-destructive text-xs">
        OBS wrote <span className="font-mono break-all">{shot.file_name}</span> but the
        backend would not serve it. If it was reloaded part-way through a change,
        restart it with <code className="font-mono">start.ps1</code>; otherwise the file
        is no longer in the replay capture directory.
      </p>
    );
  }

  return (
    <figure className="space-y-1">
      <img
        src={replayImageUrl(shot.file_name)}
        alt={`The replayed game, captured from ${shot.source_name}`}
        onError={() => setFailed(true)}
        className="w-full rounded-md border"
      />
      <figcaption className="text-muted-foreground font-mono text-xs break-all">
        {shot.file_name}
        {shot.attempts > 1 ? ` · ${shot.attempts} attempts` : null}
      </figcaption>
    </figure>
  );
}

/**
 * Replay control panel, backed by `/api/replay/*`. One button runs the whole
 * sequence; the record below it fills in while the sequence walks, and the
 * screenshot appears on it the moment it is taken.
 */
export function ReplayPanel() {
  const { data, error, isPending, isFetching, refetch } = useReplayStatus();
  const replay = useRunReplay();

  const windows = data?.windows ?? [];
  const blocked = windows.filter(
    (window) => REQUIRED_WINDOWS.has(window.window) && window.state !== "ready",
  );
  const menu = data?.menu ?? null;
  // Only worth saying when a run would actually try to exit. With exiting off
  // the in-game Exit is never pressed, so a game that has not measured that
  // target is not missing anything.
  const missingExit = Boolean(
    data?.exits_after_screenshot && !data.game_exit_configured,
  );

  // The live record, not the mutation's: starting a run resolves immediately
  // with step one, and everything after that arrives on the status poll.
  const run = data?.run ?? replay.data ?? null;
  const running = Boolean(data?.running) || run?.state === "running";
  const busy = replay.isPending || running;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <History className="size-4" />
          Replay
        </CardTitle>
        <CardDescription>
          Walking the attendant menu to the latest game-play record
        </CardDescription>
        <CardAction>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => refetch()}
            disabled={isFetching}
            aria-label="Refresh replay status"
          >
            <RefreshCw className={cn("size-4", isFetching && "animate-spin")} />
          </Button>
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-4">
        {isPending ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-9 w-full" />
          </div>
        ) : error ? (
          <ApiErrorAlert error={error} onRetry={() => refetch()} />
        ) : (
          <>
            <div className="space-y-3">
              {windows.map((window) => (
                <StatRow
                  key={window.window}
                  label={WINDOW_LABELS[window.window] ?? window.window}
                >
                  <Badge variant={STATE_VARIANTS[window.state] ?? "outline"}>
                    {window.state}
                  </Badge>
                </StatRow>
              ))}
              <StatRow label="Game">{data.game || "—"}</StatRow>
            </div>

            <div className="flex flex-wrap gap-2 border-t pt-4">
              <Button size="sm" onClick={() => replay.mutate()} disabled={busy}>
                {busy ? <Loader2 className="animate-spin" /> : <History />}
                Replay
              </Button>
            </div>

            {blocked.length > 0 ? (
              <div className="text-muted-foreground space-y-1 text-xs">
                {blocked.map((window) => (
                  <p key={window.window}>
                    {WINDOW_LABELS[window.window] ?? window.window}{" "}
                    {STATE_HINTS[window.state] ?? `is ${window.state}.`}
                  </p>
                ))}
              </div>
            ) : null}

            {/* Only when the page was actually read. A live run leaves it
                alone — its own log says what the menu is doing. */}
            {menu?.probed && !menu.reachable ? (
              <p className="text-muted-foreground text-xs">
                The attendant menu is a web page, driven through its browser at{" "}
                <span className="font-mono">{menu.cdp_url}</span>, which did not answer:{" "}
                {menu.error}
              </p>
            ) : null}

            {/* Only once the menu is actually showing something. A closed
                menu still has a page -- the app sits on a blank route -- and
                reporting "0 labels" for the normal state is noise, where the
                labels of an *open* menu are what a failing step is diagnosed
                against. */}
            {menu?.reachable && menu.label_count > 0 ? (
              <p className="text-muted-foreground text-xs">
                Menu open at <span className="font-mono">{menu.page_url}</span>, showing{" "}
                {menu.label_count} label
                {menu.label_count === 1 ? "" : "s"}
              </p>
            ) : null}

            {missingExit ? (
              <p className="text-muted-foreground text-xs">
                {data.game || "The active game"} declares no{" "}
                <code className="font-mono">{data.game_exit_target}</code> button
                target, so the game-play view cannot be exited yet. That button only
                exists while a replay is on screen, so it has to be measured off a frame
                captured during one.
              </p>
            ) : null}

            {run ? (
              <div className="space-y-2 border-t pt-4">
                <div className="flex items-center justify-between gap-2">
                  <Badge
                    variant={
                      run.state === "failed"
                        ? "destructive"
                        : run.state === "running"
                          ? "secondary"
                          : "default"
                    }
                  >
                    {run.state}
                  </Badge>
                  <span className="text-muted-foreground font-mono text-xs">
                    {formatDuration(run.duration_ms)}
                  </span>
                </div>
                <p className="text-muted-foreground text-xs">{run.message}</p>

                {/* The picture first: it is what a reader of a replay came for,
                    and it lands here as soon as it is taken, while the steps
                    after it are still running. Served as a file, so the record
                    it came from stays cheap enough to poll. */}
                {run.screenshot?.file_name ? (
                  // Keyed on the file, so a new run's picture starts with a
                  // clean slate rather than inheriting the last one's verdict.
                  <ReplayShot key={run.screenshot.file_name} shot={run.screenshot} />
                ) : null}

                {/* A black frame looks like a result, so it is called one thing
                    it is not: OBS reports writing it happily. */}
                {run.screenshot?.blank ? (
                  <p className="text-destructive text-xs">
                    The screenshot came back empty, so it shows nothing of the replay.
                    Check that the OBS window-capture source is pointed at the game.
                  </p>
                ) : null}

                <ul className="space-y-2">
                  {run.steps.map((step) => (
                    <StepRow key={step.key} step={step} />
                  ))}
                </ul>

                <RunLog logs={run.logs ?? []} live={run.state === "running"} />
              </div>
            ) : null}

            {/* A refusal to start at all -- a run already in progress, or a host
                with no Windows API. A failed *step* arrives as data, not an error. */}
            {replay.error ? <ApiErrorAlert error={replay.error} /> : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
