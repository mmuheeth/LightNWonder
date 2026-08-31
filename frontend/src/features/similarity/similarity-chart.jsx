import { cn } from "@/lib/utils";

/**
 * Every candidate's score as one thin bar, already sorted best-first by the
 * backend, with the threshold as a dashed rule across them — the picture of
 * where the same-symbol and different-symbol clusters sit relative to the cut.
 *
 * Hand-rolled on divs rather than a chart library: one sorted series with a
 * reference line needs no axes machinery, and inline style is used only for
 * the computed heights (the same rule `PaylineGrid` follows). Colour is not
 * the only encoding — the bars are sorted, the rule splits them, and the
 * list below carries every number.
 */
export function SimilarityChart({ results, threshold }) {
  const scored = results.filter((match) => typeof match.score === "number");
  if (scored.length === 0) {
    return null;
  }
  const matches = scored.filter((match) => match.matched).length;

  return (
    <div className="space-y-1.5">
      <div
        role="img"
        aria-label={
          `Similarity of ${scored.length} candidates against the source, ` +
          `best first; ${matches} reached the threshold of ` +
          `${threshold.toFixed(4)}. The list below carries the same numbers.`
        }
        className="bg-muted/20 relative h-48 overflow-hidden rounded-md border"
      >
        <div className="absolute inset-2 flex items-end gap-[2px]">
          {scored.map((match) => (
            <div
              key={match.file_name}
              title={`${match.file_name} — ${match.score.toFixed(4)}${
                match.resized ? " (resized)" : ""
              }`}
              className={cn(
                "min-w-[2px] flex-1 rounded-t-sm",
                match.matched ? "bg-emerald-500" : "bg-muted-foreground/40",
              )}
              style={{ height: `${Math.max(match.score, 0) * 100}%` }}
            />
          ))}
          <div
            aria-hidden="true"
            className="border-foreground/50 absolute inset-x-0 border-t border-dashed"
            style={{ bottom: `${Math.max(threshold, 0) * 100}%` }}
          >
            <span className="bg-background/80 text-muted-foreground absolute -top-2.5 right-0 rounded px-1 font-mono text-[0.6rem]">
              {threshold.toFixed(4)}
            </span>
          </div>
        </div>
      </div>

      <div className="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.65rem]">
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rounded-[2px] bg-emerald-500" />
          reached the threshold
        </span>
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="bg-muted-foreground/40 size-2.5 rounded-[2px]"
          />
          below it
        </span>
        <span>one bar per candidate, best first</span>
      </div>
    </div>
  );
}
