import { Badge } from "@/components/ui/badge";
import { percent } from "@/features/image-classifier/percent";

/**
 * Every tile as a row: what it was named, where it sits, and how sure the model was.
 */
export function TileStats({ tiles, floor }) {
  if (!tiles?.length) return null;

  const named = tiles.filter((tile) => tile.known).length;

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-muted-foreground text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
          Every tile
        </p>
        <Badge variant="outline" className="text-[0.65rem]">
          {named}/{tiles.length} at or above {percent(floor)}
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
            {tiles.map((tile) => {
              // The leading candidate either way. When the tile cleared the floor
              // this *is* its symbol; when it did not, it is the best guess the
              // model would have made, which is what the row is here to show.
              const best = tile.predictions?.[0] ?? null;
              const code = tile.known ? tile.symbol : (best?.symbol ?? null);
              const label = tile.known ? tile.label : (best?.label ?? "—");

              return (
                <tr
                  key={tile.name}
                  className={tile.known ? undefined : "bg-muted/40"}
                  title={
                    tile.known
                      ? undefined
                      : `Below the ${percent(floor)} floor — reported as unknown`
                  }
                >
                  <td
                    className={
                      tile.known
                        ? "px-3 py-1.5"
                        : "text-muted-foreground px-3 py-1.5 italic"
                    }
                  >
                    {label}
                  </td>
                  <td
                    className={
                      tile.known
                        ? "px-3 py-1.5 font-mono"
                        : "text-muted-foreground px-3 py-1.5 font-mono"
                    }
                  >
                    {code ?? "—"}
                  </td>
                  <td className="text-muted-foreground px-3 py-1.5 font-mono">
                    r{tile.row}c{tile.column}
                  </td>
                  <td
                    className={
                      tile.known
                        ? "px-3 py-1.5 text-right font-mono tabular-nums"
                        : "text-destructive px-3 py-1.5 text-right font-mono font-medium tabular-nums"
                    }
                  >
                    {percent(tile.confidence)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="text-muted-foreground text-[0.65rem]">
        A greyed row with a red figure fell short of the {percent(floor)} floor, so the
        grids above leave it blank. The symbol shown is what the model would have
        guessed, not a name it committed to.
      </p>
    </div>
  );
}
