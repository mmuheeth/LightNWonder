import { Activity, Gamepad2, RefreshCw } from "lucide-react";
import { useMemo } from "react";

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
  useIDeckButtons,
  useIDeckStatus,
  usePressIDeckButton,
  useProbeIDeck,
} from "@/features/ideck/use-ideck";
import { cn } from "@/lib/utils";

const STATE_VARIANTS = {
  ready: "default",
  minimized: "secondary",
  not_found: "secondary",
  access_denied: "destructive",
  unsupported: "outline",
};

/** Only `ready` and `minimized` can be pressed; minimized restores on demand. */
const PRESSABLE = new Set(["ready", "minimized"]);

/** Why a state blocks pressing, in the user's terms. */
const STATE_HINTS = {
  not_found: "OledPanelSvc is not running, so there is no panel to drive.",
  unsupported: "i-deck control needs the Windows API, which this host lacks.",
  access_denied:
    "Windows is refusing input because the panel runs at a higher integrity " +
    "level than the backend. Restart the backend elevated (Run as administrator).",
};

/** Turn a layout key name into a compact, readable button label. */
function keyLabel({ xml_id }) {
  return xml_id.replace(/_/g, " ").toUpperCase();
}

/** Live control panel for the Virtual OLED i-deck, backed by `/api/ideck/*`. */
export function IDeckPanel() {
  const { data, error, isPending, isFetching, refetch } = useIDeckStatus();
  const { data: buttons } = useIDeckButtons();

  const press = usePressIDeckButton();
  const probe = useProbeIDeck();

  const state = data?.state ?? "not_found";
  const canPress = PRESSABLE.has(state);
  const busy = press.isPending || probe.isPending;
  const actionError = press.error ?? probe.error;

  // Grouped by the layout's own rows, so the deck reads the way it looks.
  const rows = useMemo(() => {
    const ordered = [...(buttons ?? [])].sort(
      (a, b) => a.panel_y - b.panel_y || a.panel_x - b.panel_x,
    );
    const grouped = new Map();
    for (const key of ordered) {
      const row = grouped.get(key.panel_y) ?? [];
      row.push(key);
      grouped.set(key.panel_y, row);
    }
    return [...grouped.values()];
  }, [buttons]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Gamepad2 className="size-4" />
          i-deck
        </CardTitle>
        <CardDescription>
          Pressing the <code className="font-mono text-xs">Virtual OLED</code> button
          panel
        </CardDescription>
        <CardAction>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => refetch()}
            disabled={isFetching}
            aria-label="Refresh i-deck status"
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
              <StatRow label="Panel">
                <Badge variant={STATE_VARIANTS[state] ?? "outline"}>{state}</Badge>
              </StatRow>
              <StatRow label="Game">{data.game}</StatRow>
              {data.panel_width ? (
                <StatRow label="Layout">
                  <span className="font-mono text-xs">
                    {data.panel_width}&times;{data.panel_height} &middot;{" "}
                    {data.button_count} keys
                  </span>
                </StatRow>
              ) : null}
            </div>

            {STATE_HINTS[state] ? (
              <p className="text-muted-foreground border-l-2 pl-3 text-xs">
                {STATE_HINTS[state]}
              </p>
            ) : null}

            {rows.length ? (
              <div className="space-y-1 border-t pt-4">
                {rows.map((row) => (
                  <div key={row[0].panel_y} className="flex gap-1">
                    {row.map((key) => (
                      <Button
                        key={key.xml_id}
                        variant="outline"
                        size="sm"
                        // Weighted by the key's real width, so differently sized
                        // keys stay proportional.
                        style={{ flexGrow: key.width, flexBasis: 0 }}
                        className="min-w-0 px-1 text-[0.65rem] font-semibold"
                        disabled={busy || !canPress}
                        onClick={() => press.mutate({ button: key.xml_id })}
                        title={`${key.xml_id} — switch ${key.button_id}`}
                      >
                        <span className="truncate">{keyLabel(key)}</span>
                      </Button>
                    ))}
                  </div>
                ))}
              </div>
            ) : null}

            <div className="flex flex-wrap gap-2 border-t pt-4">
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  press.reset();
                  probe.mutate();
                }}
                disabled={busy}
              >
                <Activity />
                Probe
              </Button>
            </div>

            {probe.data ? (
              <p className="text-muted-foreground text-xs">{probe.data.detail}</p>
            ) : null}

            {press.data ? (
              <p className="text-xs">
                <Badge variant={press.data.confirmed ? "default" : "secondary"}>
                  {press.data.confirmed ? "confirmed" : "unverified"}
                </Badge>{" "}
                <span className="text-muted-foreground font-mono">
                  {press.data.xml_id} @ {press.data.client_x},{press.data.client_y} in{" "}
                  {press.data.elapsed_ms}ms
                </span>
              </p>
            ) : null}

            {actionError ? <ApiErrorAlert error={actionError} /> : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
