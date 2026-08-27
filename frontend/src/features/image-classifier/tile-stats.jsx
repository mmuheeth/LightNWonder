import { Badge } from "@/components/ui/badge";

/**
 * Every tile as a row: what it was named, where it sits, and how sure the model
 * was.
 *
 * A table rather than fifteen thumbnails, because the pictures are already on
 * screen once -- the ringed overlay above shows *where* each verdict landed, and
 * repeating each tile beside its own row adds a second copy of the same
 * information at a size too small to judge anything by. What a table is better at
 * is the thing the overlay cannot show: the numbers, sortable by eye down a
 * column.
 *
 * A tile below the floor keeps its row rather than being dropped. "Unknown" is a
 * reported outcome here -- the game declares eighteen symbol codes and the model
 * is trained on nine, so a rejection is the expected answer for a cash orb, not a
 * failure to produce one.
 */
export function TileStats({ tiles }) {
  if (!tiles?.length) return null;

  const named = tiles.filter((tile) => tile.known).length;

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <p className="text-muted-foreground text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
          Every tile
        </p>
        <Badge variant="outline" className="text-[0.65rem]">
          {named}/{tiles.length} named
        </Badge>
      </div>

      <div className="border-border/60 overflow-hidden rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr className="text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
              <th className="px-3 py-1.5 text-left">Symbol</th>
              <th className="px-3 py-1.5 text-left">Code</th>
              <th className="px-3 py-1.5 text-left">Position</th>
              <th className="px-3 py-1.5 text-right">Confidence</th>
            </tr>
          </thead>
          <tbody className="divide-border/60 divide-y">
            {tiles.map((tile) => (
              <tr key={tile.name} className={tile.known ? undefined : "bg-amber-500/5"}>
                <td
                  className={
                    tile.known
                      ? "px-3 py-1.5"
                      : "px-3 py-1.5 font-medium text-amber-600 dark:text-amber-400"
                  }
                >
                  {tile.label}
                </td>
                <td className="px-3 py-1.5 font-mono">{tile.symbol ?? "—"}</td>
                <td className="text-muted-foreground px-3 py-1.5 font-mono">
                  r{tile.row}c{tile.column}
                </td>
                <td className="px-3 py-1.5 text-right font-mono tabular-nums">
                  {tile.confidence.toFixed(3)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
