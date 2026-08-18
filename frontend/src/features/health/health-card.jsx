import { Activity, RefreshCw } from "lucide-react";

import { ApiErrorAlert } from "@/components/api-error-alert";
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
import { useHealth } from "@/features/health/use-health";
import { cn } from "@/lib/utils";

const STATUS_VARIANTS = {
  healthy: "default",
  degraded: "secondary",
  unhealthy: "destructive",
};

function formatUptime(seconds) {
  if (typeof seconds !== "number") return "—";
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${total % 60}s`;
  return `${total}s`;
}

function Row({ label, children }) {
  return (
    <div className="flex items-baseline justify-between gap-4 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{children}</span>
    </div>
  );
}

/** Live view of `GET /health`, and the end-to-end check that wiring works. */
export function HealthCard() {
  const { data, error, isPending, isFetching, refetch } = useHealth();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity className="size-4" />
          Backend health
        </CardTitle>
        <CardDescription>
          Polling <code className="font-mono text-xs">GET /health</code> every 30s
        </CardDescription>
        <CardAction>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => refetch()}
            disabled={isFetching}
            aria-label="Refresh health"
          >
            <RefreshCw className={cn("size-4", isFetching && "animate-spin")} />
          </Button>
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-3">
        {isPending ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-4 w-3/5" />
          </div>
        ) : error ? (
          <ApiErrorAlert error={error} onRetry={() => refetch()} />
        ) : (
          <>
            <Row label="Status">
              <Badge variant={STATUS_VARIANTS[data.status] ?? "outline"}>
                {data.status}
              </Badge>
            </Row>
            <Row label="Service">{data.service}</Row>
            <Row label="Version">{data.version}</Row>
            <Row label="Environment">{data.environment}</Row>
            <Row label="Uptime">{formatUptime(data.uptime_seconds)}</Row>

            {data.checks.length > 0 ? (
              <div className="space-y-2 border-t pt-3">
                <p className="text-muted-foreground text-xs font-medium uppercase">
                  Dependencies
                </p>
                {data.checks.map((check) => (
                  <Row key={check.name} label={check.name}>
                    <span className="flex items-center gap-2">
                      {check.latency_ms != null ? (
                        <span className="text-muted-foreground font-mono text-xs">
                          {check.latency_ms}ms
                        </span>
                      ) : null}
                      <Badge variant={check.healthy ? "default" : "destructive"}>
                        {check.healthy ? "up" : "down"}
                      </Badge>
                    </span>
                  </Row>
                ))}
              </div>
            ) : (
              <p className="text-muted-foreground border-t pt-3 text-xs">
                No dependency probes registered yet.
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
