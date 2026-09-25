import { Coins, Plug, PlugZap, RefreshCw, RotateCw, Trophy } from "lucide-react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { ApiError } from "@/lib/api-error";
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
import {
  useConnectGaf,
  useDisconnectGaf,
  useGafStatus,
  useSpinGaf,
  useTakeWinGaf,
} from "@/features/gaf/use-gaf";
import { cn } from "@/lib/utils";

const STATE_VARIANTS = {
  ready: "default",
  disconnected: "secondary",
  files_missing: "destructive",
  unreachable: "destructive",
  not_configured: "outline",
};

/** Only these two can be driven; `disconnected` opens a session on demand. */
const DRIVABLE = new Set(["ready", "disconnected"]);

/**
 * Why a state blocks driving, in the user's terms. `detail` off the payload
 * already says the specifics — these are the standing fix, which the backend
 * cannot know is the fix rather than the symptom.
 */
const STATE_HINTS = {
  not_configured:
    "The active game declares no `gaf` block, so it has no automation " +
    "endpoint and no object-query dictionary to name its controls.",
  unreachable:
    "NRobot.Server.exe is not answering, or is not hosting the automation " +
    "keywords. It is started by NRobotStartUpScript.bat and is not launched " +
    "or supervised by this app.",
  files_missing:
    "The AGTF object-query files are not where the game config points. " +
    "Sync the Perforce workspace, or fix `gaf.object_query_root`.",
};

/** How a finished spin reads at a glance. */
const OUTCOME_VARIANTS = {
  win_offered: "default",
  idle: "secondary",
  timeout: "destructive",
};

const OUTCOME_LABELS = {
  win_offered: "win to collect",
  idle: "no win",
  timeout: "did not settle",
};

/**
 * The three meters as the game itself spells them. Strings, not parsed: the
 * whole point of reading them here rather than by OCR is that they are the
 * game's own text.
 */
function Meters({ meters }) {
  if (!meters) return null;
  const cells = [
    ["Credit", meters.credit],
    ["Bet", meters.bet],
    ["Win", meters.win],
  ].filter(([, value]) => value);
  if (!cells.length) return null;

  return (
    <div className="grid grid-cols-3 gap-2 border-t pt-4">
      {cells.map(([label, value]) => (
        <div key={label} className="space-y-0.5">
          <p className="text-muted-foreground text-[0.65rem] tracking-wide uppercase">
            {label}
          </p>
          <p className="font-mono text-sm font-semibold">{value}</p>
        </div>
      ))}
    </div>
  );
}

/**
 * Live control panel for driving the game through GAF, backed by
 * `/api/gaf/*`. Two actions, because they are the two halves of one spin: a
 * win *holds* the machine in play until it is collected, so a spin that pays
 * is only finished once take-win has run.
 */
export function GafPanel() {
  const { data, error, isPending, isFetching, refetch } = useGafStatus();

  const connect = useConnectGaf();
  const disconnect = useDisconnectGaf();
  const spin = useSpinGaf();
  const takeWin = useTakeWinGaf();

  const state = data?.state ?? "not_configured";
  const canDrive = DRIVABLE.has(state);
  const busy =
    spin.isPending || takeWin.isPending || connect.isPending || disconnect.isPending;

  // Whichever action ran last owns the result area, so a stale success is
  // never shown beside a fresh failure.
  const lastResult = spin.data ?? null;
  const lastCollect = takeWin.data ?? null;
  const actionError =
    spin.error ?? takeWin.error ?? connect.error ?? disconnect.error ?? null;

  // A win holds the game in play, so this stays true from the spin that paid
  // until the collect that releases it.
  const winWaiting =
    Boolean(lastResult?.win_offered) || data?.idle_state === "statePlaying";

  // The backend refuses to spin on top of a spin that has not finished — an
  // uncollected win, or a bonus still running. Offering the override here
  // rather than a standing checkbox keeps the hazard attached to the moment
  // it applies.
  // Normalised the same way `ApiErrorAlert` does it: a mutation's `error` is
  // whatever the transport rejected with, not necessarily an `ApiError`.
  const blockedByPlay =
    spin.error != null && ApiError.from(spin.error).code === "GAF_NOT_IDLE";

  function run(mutation, payload) {
    spin.reset();
    takeWin.reset();
    connect.reset();
    disconnect.reset();
    mutation.mutate(payload);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <RotateCw className="size-4" />
          GAF
        </CardTitle>
        <CardDescription>
          Driving the game by calling its own methods, not by clicking at it
        </CardDescription>
        <CardAction>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => refetch()}
            disabled={isFetching}
            aria-label="Refresh GAF status"
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
            <Skeleton className="h-16 w-full" />
          </div>
        ) : error ? (
          <ApiErrorAlert error={error} onRetry={() => refetch()} />
        ) : (
          <>
            <div className="space-y-3">
              <StatRow label="Automation">
                <Badge variant={STATE_VARIANTS[state] ?? "outline"}>{state}</Badge>
              </StatRow>
              <StatRow label="Game">{data.game}</StatRow>
              <StatRow label="Endpoint">
                <span className="font-mono text-xs">
                  {data.host}:{data.port}
                </span>
              </StatRow>
              {data.idle_state ? (
                <StatRow label="State">
                  <span className="font-mono text-xs">{data.idle_state}</span>
                </StatRow>
              ) : null}
              {data.object_count ? (
                <StatRow label="Objects">
                  <span className="font-mono text-xs">{data.object_count} named</span>
                </StatRow>
              ) : null}
            </div>

            {STATE_HINTS[state] ? (
              <div className="text-muted-foreground space-y-1 border-l-2 pl-3 text-xs">
                <p>{STATE_HINTS[state]}</p>
                {data.detail ? <p className="font-mono">{data.detail}</p> : null}
              </div>
            ) : null}

            <div className="flex flex-wrap gap-2 border-t pt-4">
              <Button
                size="sm"
                onClick={() => run(spin)}
                disabled={busy || !canDrive}
                title="Press the mechanical spin button and wait for the result"
              >
                <RotateCw className={cn(spin.isPending && "animate-spin")} />
                {spin.isPending ? "Spinning…" : "Spin"}
              </Button>
              <Button
                size="sm"
                variant={winWaiting ? "default" : "outline"}
                onClick={() => run(takeWin)}
                disabled={busy || !canDrive}
                title="Collect a win that is waiting to be taken"
              >
                <Trophy />
                {takeWin.isPending ? "Collecting…" : "Take win"}
              </Button>
              {data.connected ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => run(disconnect)}
                  disabled={busy}
                  title="Close the session; one left open blocks the next client"
                >
                  <Plug />
                  Disconnect
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => run(connect)}
                  disabled={busy || !canDrive}
                  title="Open the session now, so the cold start is not charged to a spin"
                >
                  <PlugZap />
                  Connect
                </Button>
              )}
            </div>

            {lastResult ? (
              <div className="space-y-2">
                <p className="text-xs">
                  <Badge variant={OUTCOME_VARIANTS[lastResult.outcome] ?? "outline"}>
                    {OUTCOME_LABELS[lastResult.outcome] ?? lastResult.outcome}
                  </Badge>{" "}
                  <span className="text-muted-foreground font-mono">
                    {lastResult.idle_state ?? "unknown"} in {lastResult.elapsed_ms}ms
                  </span>
                </p>
                <p className="text-muted-foreground text-xs">{lastResult.detail}</p>
                <Meters meters={lastResult.meters} />
              </div>
            ) : null}

            {lastCollect ? (
              <div className="space-y-2">
                <p className="text-xs">
                  <Badge variant={lastCollect.pressed ? "default" : "secondary"}>
                    {lastCollect.pressed ? "collected" : "nothing to collect"}
                  </Badge>{" "}
                  <span className="text-muted-foreground font-mono">
                    {lastCollect.button} in {lastCollect.elapsed_ms}ms
                  </span>
                </p>
                <p className="text-muted-foreground text-xs">{lastCollect.detail}</p>
                <Meters meters={lastCollect.meters} />
              </div>
            ) : null}

            {connect.data || disconnect.data ? (
              <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
                <Coins className="size-3" />
                {(disconnect.data ?? connect.data).detail}
              </p>
            ) : null}

            {actionError ? <ApiErrorAlert error={actionError} /> : null}

            {blockedByPlay ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() => run(spin, { force: true })}
                disabled={busy}
                title="Press the spin button even though the game is still playing"
              >
                <RotateCw />
                Spin anyway
              </Button>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
