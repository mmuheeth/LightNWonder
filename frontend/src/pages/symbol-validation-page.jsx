import { Activity, BarChart3, Images, Shapes } from "lucide-react";
import { useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { DEFAULT_SOURCE_DIR } from "@/features/symbol-validation/api";
import { ComparisonList } from "@/features/symbol-validation/comparison-list";
import { ScoreBarChart } from "@/features/symbol-validation/score-bar-chart";
import { ScoreCurveChart } from "@/features/symbol-validation/score-curve-chart";
import { SymbolCompareForm } from "@/features/symbol-validation/symbol-compare-form";
import { SymbolGroupTable } from "@/features/symbol-validation/symbol-group-table";
import { SymbolVerdict } from "@/features/symbol-validation/symbol-verdict";
import { useCompareSymbols } from "@/features/symbol-validation/use-symbol-validation";

/**
 * What one picture actually is, decided by putting it next to artwork that has a
 * name on it.
 *
 * The payline check asks whether two tiles of the same screenshot are alike;
 * this asks which *symbol* one tile is. Same cosine measure, so the numbers are
 * comparable — and just as un-intuitive: unrelated pictures already score well
 * above 0, so no single score means anything on its own. That is why two charts
 * follow the verdict rather than one. The bars say which symbol won; the curve
 * says how each symbol's frames scored across its own animation, which is what
 * decides how much the bars are worth.
 *
 * Everything below the verdict is in **source order** — the order the folder was
 * read — charts and list alike, so a symbol's frames stay together and the three
 * views line up with each other. Score order rides along on each comparison's
 * `rank`, and who won is on the verdict, so nothing is lost by not sorting.
 *
 * A whole route rather than a dashboard card: a comparison is two charts, a
 * table and a few hundred pictures, and none of those survives half a row.
 */
export function SymbolValidationPage() {
  const [candidatePath, setCandidatePath] = useState("");
  const [sourceDir, setSourceDir] = useState(DEFAULT_SOURCE_DIR);
  const [includeImages, setIncludeImages] = useState(true);
  const compare = useCompareSymbols();

  const result = compare.data ?? null;

  return (
    <div className="space-y-6">
      <div className="space-y-1 border-b pb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Symbol Validation</h1>
        <p className="text-muted-foreground text-sm">
          One picture against a folder of symbols, by cosine similarity — each source
          trimmed of its black bars and resized to the candidate first.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Shapes className="size-4" />
            Compare
          </CardTitle>
          <CardDescription>
            Point it at a tile and at the artwork to identify it from
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <SymbolCompareForm
            candidatePath={candidatePath}
            onCandidatePathChange={setCandidatePath}
            sourceDir={sourceDir}
            onSourceDirChange={setSourceDir}
            includeImages={includeImages}
            onIncludeImagesChange={setIncludeImages}
            isPending={compare.isPending}
            onCompare={() =>
              compare.mutate({
                candidate_path: candidatePath.trim(),
                ...(sourceDir.trim() === "" ? {} : { source_dir: sourceDir.trim() }),
                include_images: includeImages,
              })
            }
          />

          {compare.error ? <ApiErrorAlert error={compare.error} /> : null}

          {compare.isPending ? (
            <div className="space-y-3 border-t pt-4">
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-4 w-1/2" />
            </div>
          ) : result ? (
            <div className="border-t pt-4">
              <SymbolVerdict result={result} />
            </div>
          ) : null}
        </CardContent>
      </Card>

      {result ? (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <BarChart3 className="size-4" />
                Best per symbol
              </CardTitle>
              <CardDescription>
                Each symbol&apos;s highest-scoring source, in the order the folders were
                read, from a zero baseline — so no column&apos;s height is borrowed from
                a cropped axis
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <ScoreBarChart
                groups={result.groups}
                bestGroup={result.stats.best_group}
              />
              <SymbolGroupTable
                groups={result.groups}
                bestGroup={result.stats.best_group}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Activity className="size-4" />
                The whole field
              </CardTitle>
              <CardDescription>
                Every source in the order it was read, so each symbol&apos;s stretch of
                the curve is that animation scored frame by frame — its peak is the
                frame whose pose the screenshot caught
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ScoreCurveChart comparisons={result.comparisons} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Images className="size-4" />
                Comparisons
              </CardTitle>
              <CardDescription>
                In source order, like the charts — open one for the candidate and the
                source side by side, as they were actually measured
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ComparisonList
                comparisons={result.comparisons}
                groups={result.groups}
                candidate={result.candidate}
              />
            </CardContent>
          </Card>
        </>
      ) : null}
    </div>
  );
}
