import { AlertCircle, Grid3x3 } from "lucide-react";

import { StatRow } from "@/components/stat-row";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

// Where the applicable set id came from. The paytable folder outranks the
// maths file, because the same math.xml ships in folders that play 5, 20 and
// 40 lines — so saying which authority answered is not a detail.
const RESOLVED_FROM = {
  game_config: "from this paytable's NumberOfLines",
  math_default: "from math.xml's default (this paytable names no line count)",
  unresolved: "neither file says",
};

/**
 * One line drawn on the reels it runs across, rather than written out as pairs.
 *
 * `elements` is `[reel, position]` and both are 0-indexed, which is the form
 * the file uses; the grid below is that literally, so a V-shaped line looks
 * like a V. `rows`/`columns` come from the widest line in the set, so a game
 * with four reels does not get a five-column drawing.
 */
function PaylineGrid({ payline, rows, columns }) {
  const taken = new Set(
    payline.elements.map(([reel, position]) => `${reel}:${position}`),
  );

  return (
    <div
      className="grid gap-1"
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
      aria-hidden="true"
    >
      {Array.from({ length: rows * columns }, (_, index) => {
        const row = Math.floor(index / columns);
        const column = index % columns;
        const on = taken.has(`${column}:${row}`);
        return (
          <span
            key={index}
            className={cn(
              // Square, so the drawing has the reels' own proportions and a
              // V-shaped line reads as a V rather than as a flat zigzag.
              "aspect-square rounded-sm border",
              // `bg-muted` is a near-background surface tint -- against the
              // card it was all but invisible, and an off cell needs to read as
              // an empty reel position, not as nothing at all. So: a bordered
              // well for off, solid primary for on, which holds up in both
              // light and dark without inventing a colour.
              on
                ? "border-primary bg-primary"
                : "border-border/70 bg-muted-foreground/10",
            )}
          />
        );
      })}
    </div>
  );
}

/**
 * The payline set the loaded paytable actually plays, out of the sets
 * `winGeometry.xml` declares for the whole game.
 *
 * That file sits at the GameConfig root and is shared by every paytable, so
 * "which set" is the question this card exists to answer; the drawings below it
 * are the answer's evidence. An unreadable geometry file is carried as an error
 * here rather than failing the page — the symbols, strips and combos above are
 * all still true.
 */
export function WinGeometryCard({ geometry }) {
  const rows =
    Math.max(
      0,
      ...geometry.paylines.flatMap((line) =>
        line.elements.map(([, position]) => position + 1),
      ),
    ) || 3;
  const columns =
    Math.max(
      0,
      ...geometry.paylines.flatMap((line) => line.elements.map(([reel]) => reel + 1)),
    ) || 5;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Grid3x3 className="size-4" />
          Win geometry
        </CardTitle>
        <CardDescription>
          Which payline set is in play, and where each of its lines runs
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <span className="font-mono text-xl font-semibold tracking-tight">
            {geometry.payline_set_id ?? "—"}
          </span>
          <Badge variant="secondary">{geometry.line_count ?? "?"} lines</Badge>
          <span className="text-muted-foreground text-xs">
            {RESOLVED_FROM[geometry.resolved_from] ?? geometry.resolved_from}
          </span>
        </div>

        <div className="space-y-3">
          <StatRow label="Sets in this file">
            {geometry.sets.length ? (
              <span className="flex flex-wrap gap-1">
                {geometry.sets.map((set) => (
                  <Badge
                    key={set.payline_set_id}
                    variant={set.is_applicable ? "default" : "outline"}
                  >
                    {set.payline_set_id} · {set.line_count}
                  </Badge>
                ))}
              </span>
            ) : (
              <span className="text-muted-foreground">—</span>
            )}
          </StatRow>
          <StatRow label="File">
            <span className="font-mono text-[0.7rem] break-all">{geometry.path}</span>
          </StatRow>
        </div>

        {/* Not an `ApiErrorAlert`: the request succeeded, and this is one field
            of it reporting that one file of four could not be read. */}
        {geometry.error ? (
          <Alert variant="destructive">
            <AlertCircle />
            <AlertTitle>No lines to draw</AlertTitle>
            <AlertDescription>{geometry.error}</AlertDescription>
          </Alert>
        ) : null}

        {geometry.paylines.length > 0 ? (
          <div className="grid gap-3 border-t pt-4 sm:grid-cols-3 lg:grid-cols-5">
            {geometry.paylines.map((payline) => (
              <div key={payline.line} className="space-y-1.5">
                <p className="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.16em] uppercase">
                  Line {payline.line}
                </p>
                {/* Capped, so square cells stay reel-sized rather than growing
                    to fill whatever column width the breakpoint hands them. */}
                <div className="max-w-[9rem]">
                  <PaylineGrid payline={payline} rows={rows} columns={columns} />
                </div>
                {/* The same line in the form a game config's `paylines` block
                    uses, so the two can be compared without converting by eye. */}
                <p className="text-muted-foreground font-mono text-[0.65rem]">
                  {payline.grid.map(([row]) => row).join("")}
                </p>
              </div>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
