import { ChevronDown, Crop, RefreshCw, Scissors } from "lucide-react";
import { useState } from "react";

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
import { useExtractRoi, useRoiRegions } from "@/features/roi/use-roi";
import { cn } from "@/lib/utils";

/** Local time, so a frame taken a minute ago reads as one taken a minute ago. */
function formatCapturedAt(value) {
  const at = new Date(value);
  return Number.isNaN(at.getTime()) ? "—" : at.toLocaleTimeString();
}

/**
 * The part of the frame the game filled, which the region's fractions were
 * resolved against.
 *
 * Worth a line of its own: OBS writes every frame at its canvas size and fits
 * the game window inside it, so a resized simulator arrives as a different
 * rectangle of an identical-looking file. When a crop looks misplaced this is
 * the number that says whether the region is wrong or the detection is.
 */
function formatContentBox(box, letterboxed) {
  if (!Array.isArray(box) || box.length !== 4) return "—";
  const [left, top, right, bottom] = box;
  const size = `${right - left}×${bottom - top}`;
  return letterboxed ? `${size} at [${box.join(", ")}]` : `${size} · whole frame`;
}

/**
 * Cut a configured region out of the latest screenshot and show it.
 *
 * The regions come from the active game's `roi` block, so the dropdown is
 * whatever that game declares and never a rectangle typed in here. The frame is
 * the newest shot in the screenshots directory, which is what the OBS panel's
 * Screenshot button writes -- take one there, extract here.
 */
export function RoiPanel() {
  const { data, error, isPending, isFetching, refetch } = useRoiRegions();
  const extract = useExtractRoi();

  // Uncontrolled until the catalog arrives, then the first region. Kept as the
  // name rather than an index so a config reload cannot silently re-point it.
  const [region, setRegion] = useState("");
  const regions = data?.regions ?? [];
  const selected = region || regions[0]?.region || "";
  const selectedRegion = regions.find((option) => option.region === selected);
  const frame = data?.latest_frame ?? null;

  // A region declared with unusable numbers is in the dropdown with its reason,
  // so say why Extract is refused rather than just disabling it.
  const regionError = selectedRegion?.error ?? null;
  const canExtract = Boolean(frame) && Boolean(selected) && !regionError;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Crop className="size-4" />
          Extract ROI
        </CardTitle>
        <CardDescription>
          Cropping a configured region out of the latest screenshot
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
              <label
                htmlFor="roi-region"
                className="text-muted-foreground block text-[0.65rem] font-semibold tracking-[0.16em] uppercase"
              >
                Region
              </label>
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-0 flex-1">
                  <select
                    id="roi-region"
                    aria-label="Region"
                    value={selected}
                    onChange={(event) => setRegion(event.target.value)}
                    disabled={regions.length === 0}
                    className="border-input bg-background text-foreground hover:bg-accent/50 focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] h-9 w-full min-w-0 cursor-pointer appearance-none rounded-md border px-3 pr-9 font-mono text-sm shadow-xs transition-[color,box-shadow,background-color] outline-none disabled:cursor-not-allowed disabled:opacity-60 dark:bg-input/30"
                  >
                    {regions.length === 0 ? (
                      <option value="">No regions configured</option>
                    ) : (
                      regions.map((option) => (
                        <option
                          key={option.region}
                          value={option.region}
                          className="bg-background text-foreground"
                        >
                          {option.label}
                        </option>
                      ))
                    )}
                  </select>
                  <span className="text-muted-foreground pointer-events-none absolute inset-y-0 right-3 flex items-center">
                    <ChevronDown className="size-4" />
                  </span>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => extract.mutate({ region: selected })}
                  disabled={!canExtract || extract.isPending}
                >
                  <Scissors />
                  Extract
                </Button>
              </div>
              {regionError ? (
                <p className="text-destructive text-xs">{regionError}</p>
              ) : null}
            </div>

            {extract.error ? <ApiErrorAlert error={extract.error} /> : null}

            {extract.data ? (
              <div className="space-y-2 border-t pt-4">
                <StatRow label="Crop">
                  <span className="font-mono text-xs">
                    {extract.data.width}×{extract.data.height} at [
                    {extract.data.box.join(", ")}]
                  </span>
                </StatRow>
                <StatRow label="Game area">
                  <span className="font-mono text-xs">
                    {formatContentBox(
                      extract.data.content_box,
                      extract.data.letterboxed,
                    )}
                  </span>
                </StatRow>
                <img
                  src={extract.data.image_data}
                  alt={`Crop of roi.${extract.data.region}`}
                  className="bg-muted w-full rounded-md border"
                />
              </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
