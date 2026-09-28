import { ChevronRight, Eye, Sparkles, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/** A probability at the precision the classes actually separate by. */
function percent(value) {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
}

/** The reels as a matrix, laid out the way they sit on screen. */
function Grid({ grid, title }) {
  if (!grid?.length) return null;

  return (
    <div className="space-y-1.5">
      <p className="text-muted-foreground text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
        {title}
      </p>
      <div className="border-border/60 inline-block overflow-hidden rounded-lg border">
        <table className="text-xs">
          <tbody>
            {/* Keyed by index because the index *is* the identity here: this is a
                fixed grid of positions, not a reorderable list. */}
            {grid.map((row, rowIndex) => (
              <tr key={rowIndex} className="divide-border/60 divide-x">
                {row.map((value, columnIndex) => (
                  <td
                    key={columnIndex}
                    className={[
                      "px-2.5 py-1.5 text-center font-mono whitespace-nowrap",
                      value ? "bg-muted/20" : "bg-muted/40 text-muted-foreground",
                    ].join(" ")}
                  >
                    {value || "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** The tiles that came back unnamed, and what the model leaned towards anyway. */
function Rejected({ tiles, floor }) {
  const rejected = tiles.filter((tile) => !tile.known);
  if (rejected.length === 0) return null;

  return (
    <div className="space-y-1.5">
      <p className="flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-500">
        <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
        {rejected.length} tile{rejected.length === 1 ? "" : "s"} below the{" "}
        {percent(floor)} floor, so every line through one stops there
      </p>
      <ul className="flex flex-wrap gap-x-4 gap-y-1">
        {rejected.map((tile) => (
          <li key={tile.name} className="flex items-baseline gap-1.5 font-mono text-xs">
            <span className="text-muted-foreground">{tile.name}</span>
            <span>{tile.leading ?? "—"}</span>
            <span className="text-amber-600 tabular-nums dark:text-amber-500">
              {percent(tile.confidence)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A number read off a scatter tile, at the precision the tile was drawn with. */
function figure(value) {
  return typeof value === "number" ? String(value) : null;
}

/**
 * The scatters that landed. Rendered as its own block rather than as marks on the
 * grid above, because what there is to say differs per scatter: a feature scatter
 * has only a position, while a prize orb also carries the number OCR read off it.
 */
function Scatters({ scatters }) {
  if (!scatters?.length) return null;

  return (
    <div className="border-t pt-3 space-y-1.5">
      <p className="flex items-center gap-1.5 text-[0.6rem] font-semibold tracking-[0.14em] text-muted-foreground uppercase">
        <Sparkles className="size-3.5 shrink-0" />
        Scatters
        <Badge variant="secondary" className="font-mono text-[0.65rem] tracking-normal">
          {scatters.length}
        </Badge>
      </p>
      <ul className="space-y-1">
        {scatters.map((scatter) => {
          const value = figure(scatter.value);
          return (
            <li
              key={scatter.name}
              className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs"
            >
              <Badge variant="outline" className="font-mono text-[0.65rem]">
                {scatter.symbol}
              </Badge>
              <span>{scatter.label}</span>
              <span className="text-muted-foreground font-mono">
                at {scatter.name} (row {scatter.row}, col {scatter.column})
              </span>
              {/* Only the tiles that carry a figure get one. A free-games scatter
                  is drawn without a number, so "—" would suggest a failed read
                  where there was nothing to read. */}
              {value ? (
                <span className="font-mono tabular-nums font-semibold text-emerald-600 dark:text-emerald-500">
                  value {value}
                </span>
              ) : null}
              {scatter.error ? (
                <span className="flex items-baseline gap-1 text-amber-600 dark:text-amber-500">
                  <TriangleAlert className="size-3 shrink-0 translate-y-0.5" />
                  {scatter.error}
                </span>
              ) : null}
            </li>
          );
        })}
      </ul>
      {/* What the engine actually read, kept out of the line above but reachable:
          a value that looks wrong is only diagnosable beside the raw text. */}
      <details className="group">
        <summary className="text-muted-foreground hover:text-foreground flex cursor-pointer list-none items-center gap-1.5 text-[0.65rem] select-none">
          <ChevronRight className="size-3 transition-transform group-open:rotate-90" />
          What OCR read
        </summary>
        <ul className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
          {scatters.map((scatter) => (
            <li
              key={scatter.name}
              className="flex items-baseline gap-1.5 font-mono text-[0.65rem]"
            >
              <span className="text-muted-foreground">{scatter.name}</span>
              <span className="break-all">
                {scatter.text ? JSON.stringify(scatter.text) : "no text"}
              </span>
              {typeof scatter.ocr_confidence === "number" ? (
                <span className="text-muted-foreground tabular-nums">
                  {scatter.ocr_confidence.toFixed(0)}%
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

/**
 * Every tile's confidence, not only the rejected ones -- collapsed by default since
 * fifteen figures is more than this card needs to lead with, but the full picture for
 */
function AllConfidences({ tiles }) {
  if (tiles.length === 0) return null;

  return (
    <details className="group border-t pt-3">
      <summary className="text-muted-foreground hover:text-foreground flex cursor-pointer list-none items-center gap-1.5 text-xs select-none">
        <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
        Confidence, every tile
        <Badge variant="secondary" className="font-mono text-[0.65rem]">
          {tiles.length}
        </Badge>
      </summary>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
        {tiles.map((tile) => (
          <li key={tile.name} className="flex items-baseline gap-1.5 font-mono text-xs">
            <span className="text-muted-foreground">{tile.name}</span>
            <span>{(tile.known ? tile.symbol : tile.leading) ?? "—"}</span>
            <span
              className={
                tile.known
                  ? "text-muted-foreground tabular-nums"
                  : "text-amber-600 tabular-nums dark:text-amber-500"
              }
            >
              {percent(tile.confidence)}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

/** What landed, read off the picture by the image classifier. */
export function ReelReadingCard({ reels }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Eye className="size-4" />
          Symbols on the reels
        </CardTitle>
        <CardDescription>
          Read off the result screenshot by the image classifier — not from the
          game&apos;s log, and not by comparing tiles to each other
        </CardDescription>
        <CardAction>
          <Badge variant="outline" className="font-mono text-[0.65rem]">
            {reels.named}/{reels.tiles.length} named
          </Badge>
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <Badge variant="secondary" className="font-mono text-[0.65rem]">
            {reels.label || reels.architecture}
          </Badge>
          <span className="text-muted-foreground text-[0.65rem]">
            floor {percent(reels.min_confidence)}
            {reels.trained_at ? ` · trained ${reels.trained_at}` : ""}
          </span>
        </div>

        {/* No error branch: this card is rendered only when a reading exists.
            A failure is on the timeline's `classify` step and again on the
            payline card, which is where it has to explain something. */}
        <div className="flex flex-wrap gap-x-6 gap-y-3">
          <Grid grid={reels.symbol_grid} title="Codes" />
          <Grid grid={reels.label_grid} title="Names" />
        </div>

        <Rejected tiles={reels.tiles} floor={reels.min_confidence} />

        <Scatters scatters={reels.scatters} />

        <AllConfidences tiles={reels.tiles} />

        <p className="text-muted-foreground/70 font-mono text-[0.6rem] break-all">
          split {reels.split}
        </p>
      </CardContent>
    </Card>
  );
}
