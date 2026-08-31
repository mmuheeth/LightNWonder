import { Images, ScanSearch } from "lucide-react";
import { useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { SimilarityChart } from "@/features/similarity/similarity-chart";
import { SimilarityResults } from "@/features/similarity/similarity-results";
import { useCompareSimilarity } from "@/features/similarity/use-similarity";

/** A similarity score at the precision the clusters are actually apart by. */
function score(value) {
  return typeof value === "number" ? value.toFixed(4) : "—";
}

/** One labelled number in the statistics block — a grid to scan, not a `StatRow` list. */
function Figure({ label, value, hint }) {
  return (
    <div className="space-y-0.5">
      <p className="text-muted-foreground text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
        {label}
      </p>
      <p className="font-mono text-sm font-medium">{value}</p>
      {hint ? <p className="text-muted-foreground text-[0.65rem]">{hint}</p> : null}
    </div>
  );
}

const LABEL =
  "text-muted-foreground block text-[0.65rem] font-semibold tracking-[0.16em] uppercase";

/**
 * Score one picture against a folder of references and read the spread.
 * Blank fields defer to the backend's configured defaults, since both paths
 * live on its machine, not the browser's. `threshold` isn't intuitive:
 * cosine similarity of unrelated symbols scores 0.6–0.9, so tune it against
 * `stats.matched_min`/`rejected_max`, not intuition.
 */
export function SimilarityPanel() {
  const compare = useCompareSimilarity();

  // Raw strings, not parsed values, so a half-typed "0." isn't snapped to 0.
  const [source, setSource] = useState("");
  const [directory, setDirectory] = useState("");
  const [threshold, setThreshold] = useState("");

  const trimmed = threshold.trim();
  const override = trimmed === "" ? null : Number(trimmed);
  const thresholdInvalid =
    override !== null &&
    !(Number.isFinite(override) && override >= -1 && override <= 1);

  const result = compare.data ?? null;
  const stats = result?.stats ?? null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Images className="size-4" />
          Similarity check
        </CardTitle>
        <CardDescription>
          Cosine similarity of one picture against every image in a folder
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="space-y-2">
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-56 flex-1 space-y-1">
              <label htmlFor="similarity-source" className={LABEL}>
                Source image
              </label>
              <Input
                id="similarity-source"
                aria-label="Source image"
                placeholder="symbol-validation/r1c2.png"
                value={source}
                onChange={(event) => setSource(event.target.value)}
                className="h-9 font-mono text-sm"
              />
            </div>

            <div className="min-w-56 flex-1 space-y-1">
              <label htmlFor="similarity-directory" className={LABEL}>
                Candidates folder
              </label>
              <Input
                id="similarity-directory"
                aria-label="Candidates folder"
                placeholder="symbol-validation"
                value={directory}
                onChange={(event) => setDirectory(event.target.value)}
                className="h-9 font-mono text-sm"
              />
            </div>

            <div className="space-y-1">
              <label htmlFor="similarity-threshold" className={LABEL}>
                Match threshold
              </label>
              <Input
                id="similarity-threshold"
                aria-label="Match threshold"
                inputMode="decimal"
                placeholder="backend default"
                value={threshold}
                onChange={(event) => setThreshold(event.target.value)}
                className="h-9 w-32 font-mono text-sm"
              />
            </div>

            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                compare.mutate({
                  ...(source.trim() === "" ? {} : { source: source.trim() }),
                  ...(directory.trim() === "" ? {} : { directory: directory.trim() }),
                  ...(override === null ? {} : { threshold: override }),
                })
              }
              disabled={thresholdInvalid || compare.isPending}
            >
              <ScanSearch />
              Compare
            </Button>
          </div>

          {thresholdInvalid ? (
            <p className="text-destructive text-xs">
              The threshold must be a number from -1 to 1.
            </p>
          ) : null}
          <p className="text-muted-foreground text-xs">
            Paths are on the backend&apos;s machine, absolute or relative to its working
            directory. Blank fields use the configured defaults.
          </p>
        </div>

        {compare.error ? <ApiErrorAlert error={compare.error} /> : null}

        {result ? (
          <div className="space-y-4 border-t pt-4">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <Badge variant="outline" className="font-mono text-[0.65rem]">
                threshold {score(result.threshold)}
              </Badge>
              <span className="text-muted-foreground font-mono text-[0.65rem] break-all">
                {result.source.file_name} ({result.source.width}×{result.source.height})
                against {result.directory}
              </span>
            </div>

            <p className="text-sm">{result.summary}</p>

            <SimilarityChart results={result.results} threshold={result.threshold} />

            <div className="border-border/60 bg-muted/20 grid grid-cols-2 gap-x-3 gap-y-4 rounded-lg border p-3 sm:grid-cols-4">
              <Figure label="Candidates" value={stats.candidates} />
              <Figure label="Compared" value={stats.compared} />
              <Figure label="Errors" value={stats.errors} hint="unreadable files" />
              <Figure
                label="Resized"
                value={stats.resized}
                hint="to the source's size"
              />
              <Figure label="Matched" value={stats.matches} />
              <Figure
                label="Score range"
                value={`${score(stats.score_min)}–${score(stats.score_max)}`}
              />
              {/* Threshold should sit between these two. */}
              <Figure
                label="Lowest match"
                value={score(stats.matched_min)}
                hint="counted as the same"
              />
              <Figure
                label="Highest reject"
                value={score(stats.rejected_max)}
                hint="counted as different"
              />
            </div>

            <SimilarityResults results={result.results} source={result.source} />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
