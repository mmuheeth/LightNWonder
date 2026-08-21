import { Grid3x3, RefreshCw, Scissors } from "lucide-react";
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
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useGridLayout, useSplitGrid } from "@/features/grid/use-grid";
import { cn } from "@/lib/utils";

/**
 * The border trim as one short string.
 *
 * Four equal edges is the usual case and reads as one number; anything else is
 * spelled out, because a trim that is not symmetric is worth noticing.
 */
function formatInset(inset) {
  if (!inset || inset.every((edge) => edge === 0)) return "none";
  return inset.every((edge) => edge === inset[0]) ? String(inset[0]) : inset.join(", ");
}

/**
 * The part of the frame the game filled, which `roi.reels` was resolved against.
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

/** Local time, so a frame taken a minute ago reads as one taken a minute ago. */
function formatCapturedAt(value) {
  const at = new Date(value);
  return Number.isNaN(at.getTime()) ? "—" : at.toLocaleTimeString();
}

/**
 * The tiles as a matrix, drawn from the backend's own row/column numbers.
 *
 * Laid out by `gridTemplateColumns` off `columns` rather than by chunking the
 * list: the tiles arrive row-major with their positions on them, so the only
 * thing the page has to know is how wide a row is. Getting that from the
 * response means a game with six reels needs nothing changed here.
 */
function TileMatrix({ tiles, columns }) {
  return (
    <div
      className="grid gap-1"
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {tiles.map((tile) => (
        <figure key={tile.name} className="space-y-1">
          {tile.image_data ? (
            <img
              src={tile.image_data}
              alt={`Tile ${tile.name}`}
              className="bg-muted aspect-square w-full rounded border object-cover"
            />
          ) : (
            <div className="bg-muted aspect-square w-full rounded border" />
          )}
          <figcaption className="text-muted-foreground text-center font-mono text-[0.6rem]">
            {tile.name}
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

/**
 * Split the reels out of the latest screenshot into a matrix of tiles.
 *
 * The grid comes from the active game's `roi.reels` region and its
 * `reel_bounds` block, so the shape shown here is whatever that game declares
 * and never a number typed in this file. The frame is the newest shot in the
 * screenshots directory, which is what the OBS panel's Screenshot button writes
 * — take one there, split it here.
 *
 * A game that declares no reels is a state, not an error: the backend reports it
 * as `error` on a 200 and this says so with Split disabled, the same way the ROI
 * panel handles a region with unusable numbers.
 *
 * The trim override is the one piece of client state here, and it exists for a
 * loop rather than for a preference: try a number against a frame that does not
 * move, look at the tiles, then write the winner into the game config. Left
 * blank it sends nothing and the config's own trim applies.
 */
export function GridPanel() {
  const { data, error, isPending, isFetching, refetch } = useGridLayout();
  const split = useSplitGrid();

  // Blank means "whatever the config says", which is the case that needs no
  // typing. Held as the raw string so a half-typed "0." is not snapped to 0.
  const [inset, setInset] = useState("");

  const frame = data?.latest_frame ?? null;
  const layoutError = data?.error ?? null;
  const trimmed = inset.trim();
  const override = trimmed === "" ? null : Number(trimmed);
  const insetInvalid = override !== null && !(override >= 0 && override < 0.5);
  const canSplit = Boolean(frame) && !layoutError && !insetInvalid;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Grid3x3 className="size-4" />
          Reel grid
        </CardTitle>
        <CardDescription>
          Splitting the reels of the latest screenshot into tiles
        </CardDescription>
        <CardAction>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => refetch()}
            disabled={isFetching}
            aria-label="Refresh grid layout"
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
              <StatRow label="Grid">
                {layoutError ? (
                  <span className="text-muted-foreground text-xs">not configured</span>
                ) : (
                  <span className="font-mono text-xs">
                    {data.rows} × {data.columns} · roi.{data.region}
                  </span>
                )}
              </StatRow>
              <StatRow label="Border trim">
                <span className="font-mono text-xs">{formatInset(data.inset)}</span>
              </StatRow>
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
              <div className="flex flex-wrap items-end justify-between gap-2">
                <div className="space-y-1">
                  <label
                    htmlFor="grid-inset"
                    className="text-muted-foreground block text-[0.65rem] font-semibold tracking-[0.16em] uppercase"
                  >
                    Trim override
                  </label>
                  <Input
                    id="grid-inset"
                    aria-label="Trim override"
                    inputMode="decimal"
                    placeholder="from config"
                    value={inset}
                    onChange={(event) => setInset(event.target.value)}
                    className="h-9 w-32 font-mono text-sm"
                  />
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    split.mutate(override === null ? {} : { inset: override })
                  }
                  disabled={!canSplit || split.isPending}
                >
                  <Scissors />
                  Split
                </Button>
              </div>
              <p className="text-muted-foreground text-xs">
                A fraction of each tile, trimmed off every edge — for finding the number
                to write into the game config. Writes to{" "}
                <span className="font-mono">obs-captured-files/grid/</span>
              </p>
              {insetInvalid ? (
                <p className="text-destructive text-xs">
                  The trim must be a fraction from 0 up to 0.5.
                </p>
              ) : null}
              {layoutError ? (
                <p className="text-destructive text-xs">{layoutError}</p>
              ) : null}
            </div>

            {split.error ? <ApiErrorAlert error={split.error} /> : null}

            {split.data ? (
              <div className="space-y-3 border-t pt-4">
                <StatRow label="Reels">
                  <span className="font-mono text-xs">
                    {split.data.width}×{split.data.height} at [
                    {split.data.box.join(", ")}]
                  </span>
                </StatRow>
                <StatRow label="Game area">
                  <span className="font-mono text-xs">
                    {formatContentBox(split.data.content_box, split.data.letterboxed)}
                  </span>
                </StatRow>
                <StatRow label="Trimmed by">
                  <span className="font-mono text-xs">
                    {formatInset(split.data.inset)}
                  </span>
                </StatRow>
                {/* One size for the whole grid, because every tile shares it --
                    printing it per tile would invite comparing fifteen numbers. */}
                <StatRow label="Tile size">
                  <span className="font-mono text-xs">
                    {split.data.tile_width}×{split.data.tile_height} · all{" "}
                    {split.data.tiles.length} equal
                  </span>
                </StatRow>
                {split.data.crop_image ? (
                  <img
                    src={split.data.crop_image}
                    alt={`Crop of roi.${split.data.region}`}
                    className="bg-muted w-full rounded-md border"
                  />
                ) : null}
                <TileMatrix tiles={split.data.tiles} columns={split.data.columns} />
                {/* Where the files went, because the files are the deliverable
                    and the tiles above are only how they are checked. */}
                <p className="text-muted-foreground font-mono text-[0.65rem] break-all">
                  {split.data.output_dir}
                </p>
              </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
