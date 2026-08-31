import { ChevronDown, CircleCheck, CircleSlash } from "lucide-react";

import { Badge } from "@/components/ui/badge";

/** A similarity score at the precision the clusters are actually apart by. */
function score(value) {
  return typeof value === "number" ? value.toFixed(4) : "—";
}

const CAPTION =
  "text-muted-foreground text-[0.65rem] font-semibold tracking-[0.16em] uppercase";

/** One side of a comparison: a labelled picture, skipped when there is none. */
function ComparedImage({ label, src, alt }) {
  if (!src) {
    return null;
  }
  return (
    <figure className="space-y-1">
      <figcaption className={CAPTION}>{label}</figcaption>
      {/* Pixelated: the picture is tile-sized, and a crisp zoom of the exact
          pixels the measure saw beats a smoothed one. */}
      <img
        src={src}
        alt={alt}
        className="bg-muted w-full rounded-md border [image-rendering:pixelated]"
      />
    </figure>
  );
}

/**
 * One candidate as a dropdown: the verdict on the summary line, the two
 * pictures the score was computed from inside. Native `<details>` for the same
 * reason the payline rows use it — ninety comparisons are not something to
 * look at all at once, and the element already is this behaviour with the
 * keyboard and screen-reader support written.
 */
function ResultRow({ match, source }) {
  return (
    <details className="group border-border/60 hover:border-border overflow-hidden rounded-lg border transition-colors">
      <summary className="hover:bg-accent/40 flex cursor-pointer list-none items-center gap-3 px-3 py-2 text-sm [&::-webkit-details-marker]:hidden">
        <ChevronDown className="text-muted-foreground size-4 shrink-0 transition-transform group-open:rotate-180" />
        {match.matched ? (
          <CircleCheck className="size-3.5 shrink-0 text-emerald-500" />
        ) : (
          <CircleSlash className="text-muted-foreground size-3.5 shrink-0" />
        )}
        <span
          className="max-w-64 truncate font-mono text-xs"
          title={match.file_name}
        >
          {match.file_name}
        </span>
        <span className="font-mono text-xs tabular-nums">{score(match.score)}</span>
        {match.trimmed ? (
          <Badge variant="outline" className="text-[0.65rem]">
            trimmed
          </Badge>
        ) : null}
        {match.resized ? (
          <Badge variant="outline" className="text-[0.65rem]">
            resized
          </Badge>
        ) : null}
        <span className="text-muted-foreground ml-auto hidden font-mono text-[0.65rem] sm:block">
          {match.width ? `${match.width}×${match.height}` : "—"}
        </span>
      </summary>

      <div className="bg-muted/20 space-y-3 border-t px-3 py-3">
        {match.error ? (
          <p className="text-destructive text-xs">{match.error}</p>
        ) : null}
        <div className="grid grid-cols-2 gap-3">
          <ComparedImage
            label="Source"
            src={source.image_data}
            alt={`The source picture, ${source.file_name}`}
          />
          <ComparedImage
            label="Candidate, as compared"
            src={match.image_data}
            alt={`${match.file_name} as it was scored${
              match.resized ? ", resized to the source's size" : ""
            }`}
          />
        </div>
      </div>
    </details>
  );
}

/**
 * Every candidate as a dropdown row, in the order the backend sent them:
 * scored ones best-first, unreadable ones last with their reason. The summary
 * lines are the data view behind the chart; opening one shows the pictures
 * the score was computed from — the candidate *after* any resize, because
 * what the measure saw is the only picture a surprising score can be judged by.
 */
export function SimilarityResults({ results, source }) {
  return (
    <div className="max-h-[32rem] space-y-1.5 overflow-y-auto pr-1">
      {results.map((match) => (
        <ResultRow key={match.file_name} match={match} source={source} />
      ))}
    </div>
  );
}
