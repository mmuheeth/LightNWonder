import { Crop, RefreshCw, Scissors } from "lucide-react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { StatRow } from "@/components/stat-row";
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
import { MeterValues } from "@/features/roi/meter-values";
import { useExtractRoi, useRoiRegions } from "@/features/roi/use-roi";
import { cn } from "@/lib/utils";

/** Local time, so a frame taken a minute ago reads as one taken a minute ago. */
function formatCapturedAt(value) {
  const at = new Date(value);
  return Number.isNaN(at.getTime()) ? "—" : at.toLocaleTimeString();
}

const CASH_METER_REGION = "cash_meter";

/**
 * Cut the cash meter out of the latest screenshot and show it. The region
 * comes from the active game's `roi.cash_meter`; the frame is the newest shot
 * the OBS panel's Screenshot button wrote. Extracting it also reads the
 * meter, so `<MeterValues>` renders below the picture rather than in its own
 * card.
 */
export function RoiPanel() {
  const { data, error, isPending, isFetching, refetch } = useRoiRegions();
  const extract = useExtractRoi();

  const regions = data?.regions ?? [];
  const selectedRegion = regions.find((option) => option.region === CASH_METER_REGION);
  const frame = data?.latest_frame ?? null;

  // Says why Extract is refused rather than just disabling it.
  const regionError = selectedRegion?.error ?? null;
  const canExtract = Boolean(frame) && Boolean(selectedRegion) && !regionError;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Crop className="size-4" />
          Extract Cash Meter
        </CardTitle>
        <CardDescription>
          Cropping the cash meter out of the latest screenshot
        </CardDescription>
        <CardAction>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => refetch()}
            disabled={isFetching}
            aria-label="Refresh regions"
          >
            <RefreshCw className={cn("size-4", isFetching && "animate-spin")} />
          </Button>
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-4">
        {isPending ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : error ? (
          <ApiErrorAlert error={error} onRetry={() => refetch()} />
        ) : (
          <>
            <div className="space-y-3">
              <StatRow label="Game">{data.game}</StatRow>
              <StatRow label="Frame">
                {frame ? (
                  <span className="font-mono text-xs break-all">
                    {frame.width}×{frame.height} · {formatCapturedAt(frame.captured_at)}
                  </span>
                ) : (
                  <span className="text-muted-foreground text-xs">
                    none yet — take one from the OBS panel
                  </span>
                )}
              </StatRow>
            </div>

            <div className="space-y-2 border-t pt-4">
              <Button
                variant="outline"
                size="sm"
                onClick={() => extract.mutate({ region: CASH_METER_REGION })}
                disabled={!canExtract || extract.isPending}
              >
                <Scissors />
                Extract
              </Button>
              {regionError ? (
                <p className="text-destructive text-xs">{regionError}</p>
              ) : null}
            </div>

            {extract.error ? <ApiErrorAlert error={extract.error} /> : null}

            {extract.data ? (
              <div className="space-y-2 border-t pt-4">
                <img
                  src={extract.data.image_data}
                  alt={`Crop of roi.${extract.data.region}`}
                  className="bg-muted w-full rounded-md border"
                />
                <MeterValues meter={extract.data.meter} />
              </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
