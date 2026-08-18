import {
  Camera,
  Circle,
  Pause,
  Play,
  Plug,
  PlugZap,
  RefreshCw,
  Square,
  Video,
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
import {
  useConnectObs,
  useDisconnectObs,
  useObsStatus,
  usePauseRecording,
  useResumeRecording,
  useStartRecording,
  useStopRecording,
  useTakeScreenshot,
} from "@/features/obs/use-obs";
import { cn } from "@/lib/utils";

const STATE_VARIANTS = {
  connected: "default",
  disconnected: "secondary",
};

/** Keep the preview small; a full-resolution data URI runs to several MB. */
const PREVIEW_WIDTH = 1280;

function formatDuration(ms) {
  if (typeof ms !== "number" || ms <= 0) return "0s";
  const total = Math.floor(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

/** Live control panel for OBS Studio, backed by `GET/POST /api/obs/*`. */
export function ObsPanel() {
  const { data, error, isPending, isFetching, refetch } = useObsStatus();

  const connect = useConnectObs();
  const disconnect = useDisconnectObs();
  const start = useStartRecording();
  const stop = useStopRecording();
  const pause = usePauseRecording();
  const resume = useResumeRecording();
  const screenshot = useTakeScreenshot();

  const isConnected = data?.state === "connected";
  const recording = data?.recording ?? null;
  const isRecording = Boolean(recording?.active);
  const isPaused = Boolean(recording?.paused);

  // Only the stop endpoint reports where the file landed; GET /status always
  // leaves output_path null. So read it from the mutation result, which the
  // Record button clears when a new take begins.
  const savedPath = stop.data?.output_path ?? null;

  // Any in-flight action should lock the rest of the controls, so two commands
  // never race on the single OBS socket.
  const busy =
    connect.isPending ||
    disconnect.isPending ||
    start.isPending ||
    stop.isPending ||
    pause.isPending ||
    resume.isPending;

  const actionError =
    connect.error ??
    disconnect.error ??
    start.error ??
    stop.error ??
    pause.error ??
    resume.error ??
    screenshot.error;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Video className="size-4" />
          OBS Studio
        </CardTitle>
        <CardDescription>
          Controlling OBS over <code className="font-mono text-xs">obs-websocket</code>
        </CardDescription>
        <CardAction>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => refetch()}
            disabled={isFetching}
            aria-label="Refresh OBS status"
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
              <StatRow label="Connection">
                <Badge variant={STATE_VARIANTS[data.state] ?? "outline"}>
                  {data.state}
                </Badge>
              </StatRow>
              <StatRow label="Server">
                <span className="font-mono text-xs">{data.url}</span>
              </StatRow>
              {isConnected ? (
                <>
                  <StatRow label="Version">{data.obs_version ?? "—"}</StatRow>
                  <StatRow label="Scene">{data.current_scene ?? "—"}</StatRow>
                  <StatRow label="Recording">
                    {isRecording ? (
                      <Badge variant={isPaused ? "secondary" : "destructive"}>
                        <Circle
                          className={cn(
                            "size-2 fill-current",
                            isPaused || "animate-pulse",
                          )}
                        />
                        {isPaused ? "paused" : "recording"}
                      </Badge>
                    ) : (
                      <Badge variant="outline">idle</Badge>
                    )}
                  </StatRow>
                  {isRecording ? (
                    <StatRow label="Elapsed">
                      {recording.timecode ?? formatDuration(recording.duration_ms)}
                    </StatRow>
                  ) : null}
                </>
              ) : null}
            </div>

            <div className="flex flex-wrap gap-2 border-t pt-4">
              {isConnected ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => disconnect.mutate()}
                  disabled={busy}
                >
                  <PlugZap />
                  Disconnect
                </Button>
              ) : (
                <Button size="sm" onClick={() => connect.mutate()} disabled={busy}>
                  <Plug />
                  Connect
                </Button>
              )}

              {isRecording ? (
                <>
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() => stop.mutate()}
                    disabled={busy}
                  >
                    <Square />
                    Stop
                  </Button>
                  {isPaused ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => resume.mutate()}
                      disabled={busy}
                    >
                      <Play />
                      Resume
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => pause.mutate()}
                      disabled={busy}
                    >
                      <Pause />
                      Pause
                    </Button>
                  )}
                </>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    // Drop the previous take's path before starting a new one.
                    stop.reset();
                    start.mutate();
                  }}
                  disabled={busy || !isConnected}
                >
                  <Circle className="fill-current" />
                  Record
                </Button>
              )}

              <Button
                variant="outline"
                size="sm"
                onClick={() => screenshot.mutate({ width: PREVIEW_WIDTH })}
                disabled={screenshot.isPending || !isConnected}
              >
                <Camera />
                Screenshot
              </Button>
            </div>

            {savedPath ? (
              <p className="text-muted-foreground font-mono text-xs break-all">
                Saved {savedPath}
              </p>
            ) : null}

            {screenshot.data?.image_data ? (
              <img
                src={screenshot.data.image_data}
                alt={`Screenshot of ${screenshot.data.source_name}`}
                className="w-full rounded-md border"
              />
            ) : null}

            {actionError ? <ApiErrorAlert error={actionError} /> : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
