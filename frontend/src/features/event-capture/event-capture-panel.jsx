import { Circle, Images, RefreshCw, ScanEye, Square } from "lucide-react";
import { Link } from "react-router-dom";

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
import {
  useCaptureStatus,
  useStartCapture,
  useStopCapture,
} from "@/features/event-capture/use-event-capture";
import { cn } from "@/lib/utils";

/**
 * Why a start failed, in terms of what to go and do about it. The backend's own
 * message says what happened; these say what fixes it.
 */
const ERROR_HINTS = {
  EVENT_CAPTURE_LOG_UNAVAILABLE:
    "Start the game so it begins writing its log, then try again.",
  OBS_CONNECTION_FAILED:
    "Open OBS Studio and enable its WebSocket server, then try again.",
  OBS_NOT_CONNECTED: "Connect to OBS from the card above, then try again.",
};

function formatDuration(ms) {
  if (typeof ms !== "number" || ms <= 0) return "0s";
  const total = Math.floor(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

/**
 * Start and stop log-driven capture, backed by `GET/POST /api/event-capture/*`.
 *
 * The run lives in the backend, so this card reports rather than owns it: a
 * reload mid-run shows it still going, and two tabs agree.
 */
export function EventCapturePanel() {
  const { data, error, isPending, isFetching, refetch } = useCaptureStatus();

  const start = useStartCapture();
  const stop = useStopCapture();

  const isActive = Boolean(data?.active);
  const busy = start.isPending || stop.isPending;
  const actionError = start.error ?? stop.error;
  const hint = actionError ? ERROR_HINTS[actionError.code] : null;

  // Only the stop endpoint returns the finished record; status never carries
  // it. So it is read from the mutation result, which Start clears.
  const finished = stop.data ?? null;
  const recent = data?.recent_events ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ScanEye className="size-4" />
          Event Based Capture
        </CardTitle>
        <CardDescription>
          Screenshotting gameplay events from the active game&apos;s log
        </CardDescription>
        <CardAction>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => refetch()}
            disabled={isFetching}
            aria-label="Refresh capture status"
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
              <StatRow label="Tracking">
                {isActive ? (
                  <Badge variant="destructive">
                    <Circle className="size-2 animate-pulse fill-current" />
                    recording events
                  </Badge>
                ) : (
                  <Badge variant="outline">idle</Badge>
                )}
              </StatRow>
              {isActive ? (
                <>
                  <StatRow label="Game">{data.game ?? "—"}</StatRow>
                  <StatRow label="Run">
                    <span className="font-mono text-xs">{data.run_id}</span>
                  </StatRow>
                  <StatRow label="Elapsed">{formatDuration(data.duration_ms)}</StatRow>
                  <StatRow label="Events">{data.event_count}</StatRow>
                </>
              ) : null}
            </div>

            {isActive && recent.length > 0 ? (
              <div className="space-y-2 border-t pt-4">
                <p className="text-muted-foreground text-xs">Latest events</p>
                <ul className="space-y-1">
                  {recent
                    .slice()
                    .reverse()
                    .map((event) => (
                      <li
                        key={event.sequence}
                        className="flex items-baseline gap-2 text-xs"
                      >
                        <Badge variant="secondary">{event.event}</Badge>
                        <span className="text-muted-foreground truncate">
                          {event.summary}
                        </span>
                      </li>
                    ))}
                </ul>
              </div>
            ) : null}

            <div className="flex flex-wrap gap-2 border-t pt-4">
              {isActive ? (
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => stop.mutate()}
                  disabled={busy}
                >
                  <Square />
                  Stop tracking
                </Button>
              ) : (
                <Button
                  size="sm"
                  onClick={() => {
                    // Drop the previous run's summary before starting a new one.
                    stop.reset();
                    start.mutate();
                  }}
                  disabled={busy}
                >
                  <Circle className="fill-current" />
                  Start tracking
                </Button>
              )}

              <Button variant="outline" size="sm" asChild>
                <Link to="/captures">
                  <Images />
                  View captures
                </Link>
              </Button>
            </div>

            {finished ? (
              <p className="text-muted-foreground text-xs">
                Saved run <span className="font-mono">{finished.run_id}</span> with{" "}
                {finished.event_count} events.{" "}
                <Link to={`/captures/${finished.run_id}`} className="underline">
                  Open it
                </Link>
              </p>
            ) : null}

            {isActive && data.errors?.length > 0 ? (
              <p className="text-destructive text-xs">
                {data.errors.length} capture problem
                {data.errors.length === 1 ? "" : "s"}: {data.errors[0]}
              </p>
            ) : null}

            {actionError ? (
              <div className="space-y-1">
                <ApiErrorAlert error={actionError} />
                {hint ? <p className="text-muted-foreground text-xs">{hint}</p> : null}
              </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
