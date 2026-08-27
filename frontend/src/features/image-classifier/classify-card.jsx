import { useState } from "react";
import { RefreshCw, ScanSearch } from "lucide-react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Figure, FigureGrid } from "@/components/figure";
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
import { SymbolGrid } from "@/features/image-classifier/symbol-grid";
import { TileStats } from "@/features/image-classifier/tile-stats";
import { cn } from "@/lib/utils";

/**
 * Name the tiles of one reel split.
 *
 * The split is chosen from what the reel grid has already written rather than
 * taken fresh, which is what makes an answer repeatable: the same split reads the
 * same way twice, and a threshold can be tried again after the game has moved on.
 */
export function ClassifyCard({ status, splits, classify }) {
  const [split, setSplit] = useState("");
  const [floor, setFloor] = useState("");

  const ready = status?.state === "ready" || status?.state === "stale";
  const available = splits.data?.splits ?? [];
  const result = classify.data ?? null;

  function run() {
    classify.mutate({
      ...(split ? { split } : {}),
      ...(floor === "" ? {} : { min_confidence: Number(floor) }),
      // The per-tile pictures are no longer rendered -- the ringed overlay is the
      // one picture shown -- and fifteen base64 PNGs are most of the payload.
      include_images: false,
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ScanSearch className="size-4" />
          Classify a split
        </CardTitle>
        <CardDescription>
          Reads the tiles the reel grid wrote and names each one. Confidence is a
          probability over the {status?.model?.classes?.length ?? 0} trained symbols
          only — a tile showing something with no training artwork cannot come back as
          &ldquo;none of these&rdquo;, so it comes back below the floor as unknown.
        </CardDescription>
        <CardAction>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => splits.refetch()}
            disabled={splits.isFetching}
            aria-label="Refresh the list of splits"
          >
            <RefreshCw className={cn("size-4", splits.isFetching && "animate-spin")} />
          </Button>
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 flex-1 space-y-1.5">
            <Label htmlFor="classifier-split" className="text-xs">
              Split
            </Label>
            {/* A styled native select, as this app does elsewhere rather than
                pulling in another primitive. */}
            <select
              id="classifier-split"
              value={split}
              onChange={(event) => setSplit(event.target.value)}
              className="border-input bg-background ring-offset-background focus-visible:ring-ring h-9 w-full rounded-md border px-3 py-1 text-sm focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
            >
              <option value="">Newest ({splits.data?.latest ?? "none yet"})</option>
              {available.map((entry) => (
                <option key={entry.name} value={entry.name}>
                  {entry.name} · {entry.rows}×{entry.columns}
                </option>
              ))}
            </select>
          </div>

          <div className="w-32 space-y-1.5">
            <Label htmlFor="classifier-floor" className="text-xs">
              Floor
            </Label>
            <input
              id="classifier-floor"
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={floor}
              placeholder={String(status?.min_confidence ?? 0.7)}
              onChange={(event) => setFloor(event.target.value)}
              className="border-input bg-background ring-offset-background focus-visible:ring-ring h-9 w-full rounded-md border px-3 py-1 font-mono text-sm tabular-nums focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
            />
          </div>

          <Button
            variant="outline"
            size="sm"
            onClick={run}
            disabled={!ready || classify.isPending || !available.length}
          >
            <ScanSearch />
            {classify.isPending ? "Classifying…" : "Classify"}
          </Button>
        </div>

        {!ready ? (
          <p className="text-muted-foreground text-xs">
            {status?.detail ?? "Train a model first."}
          </p>
        ) : null}

        {splits.data?.error ? (
          <p className="text-muted-foreground text-xs">{splits.data.error}</p>
        ) : null}

        {classify.error ? <ApiErrorAlert error={classify.error} /> : null}

        {result ? (
          <div className="space-y-4 border-t pt-4">
            <p className="text-sm">{result.summary}</p>

            <FigureGrid>
              <Figure
                label="Split"
                value={result.split}
                hint={`${result.rows}×${result.columns}`}
              />
              <Figure
                label="Named"
                value={`${result.named}/${result.named + result.unknown}`}
                hint={`${result.unknown} below the floor`}
              />
              <Figure
                label="Floor"
                value={result.min_confidence.toFixed(2)}
                hint="probability, not similarity"
              />
            </FigureGrid>

            <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
              <SymbolGrid grid={result.symbol_grid} title="Codes" />
              <SymbolGrid grid={result.label_grid} title="Symbols" mono={false} />
            </div>

            {result.overlay_image ? (
              <div className="space-y-2">
                <p className="text-muted-foreground text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
                  On the reels
                </p>
                <img
                  src={result.overlay_image}
                  alt={`Symbols named on ${result.split}`}
                  className="bg-muted/40 w-full rounded-md border object-contain"
                />
                <p className="text-muted-foreground text-[0.65rem]">
                  A green ring is a named tile, amber is below the floor. Also written
                  to{" "}
                  <span className="font-mono">
                    {result.output_dir}/{result.overlay_file}
                  </span>
                </p>
              </div>
            ) : null}

            <TileStats tiles={result.tiles} />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
