import { ChevronRight, Eye, TriangleAlert } from "lucide-react";

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

/**
 * The reels as a matrix, laid out the way they sit on screen.
 *
 * Rendered twice -- once in codes, once in display names -- because the codes are
 * what the rest of the system speaks (the maths files, the paylines block, the
 * awards below) and the names are what a person reads. Both matrices come off the
 * response rather than being derived here, so the two cannot end up disagreeing
 * about which cell is which. A dash is a tile nothing cleared the confidence floor
 * for, which is an answer, not a hole.
 */
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

/**
 * The tiles that came back unnamed, and what the model leaned towards anyway.
 *
 * The most useful thing on this card when a spin that plainly paid reports no
 * run. The floor is set above where the classes separate, so a correct reading is
 * rejected whenever the model is only fairly sure — and a line through a rejected
 * tile stops there. Showing the leading candidate with its figure in amber is
 * what makes that a tunable rather than a mystery.
 */
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

/**
 * Every tile's confidence, not only the rejected ones -- collapsed by default
 * since fifteen figures is more than this card needs to lead with, but the
 * full picture for whoever wants to see how close a *passing* tile actually
 * was to the floor.
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

/**
 * What landed, read off the picture by the image classifier.
 *
 * Its own card rather than a block inside the payline one because it is one
 * reading of one picture and is worth having even when the lines could not be
 * checked — a grid of codes beside the reels is readable evidence on a run whose
 * paytable never loaded.
 *
 * It replaced two earlier readings, and the difference is the point of the card:
 *
 * - **cosine similarity between tiles** said which tiles were alike and never
 *   which symbol they were, so an award could only be narrowed to every paytable
 *   row paying at that run length.
 * - **the reel stops in the game's own log** named the symbols by agreeing with
 *   the game, so a reel drawing the wrong symbol could never be caught.
 *
 * Two grids and a list, and deliberately no picture. The ringed reels are still
 * written beside the tiles (`reels.overlay_file`) because a ring over a cell is
 * the cheapest way to check the split was named the way it looks — but the rings
 * only ever meant "the model was sure", which the grids already say in codes, and
 * the reels themselves are on the Analyze Spin card above as a screenshot. What is
 * worth the space here is every *rejected* tile with the candidate it leaned
 * towards, since that is the number a short payline run is explained by.
 */
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

        <AllConfidences tiles={reels.tiles} />

        <p className="text-muted-foreground/70 font-mono text-[0.6rem] break-all">
          split {reels.split}
        </p>
      </CardContent>
    </Card>
  );
}
