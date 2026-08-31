import { SimilarityPanel } from "@/features/similarity/similarity-panel";

/**
 * One picture scored against a reference library, with the spread charted.
 *
 * A whole page rather than a dashboard card because the result is ~90 rows and
 * a chart, and neither survives half a row. No `GameSelector` — the check is
 * two paths and a threshold, nothing about the active game.
 */
export function SimilarityPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-1 border-b pb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Similarity</h1>
        <p className="text-muted-foreground text-sm">
          Cosine similarity of one image against every image in a folder — the spread to
          tune the match threshold against.
        </p>
      </div>

      <SimilarityPanel />
    </div>
  );
}
