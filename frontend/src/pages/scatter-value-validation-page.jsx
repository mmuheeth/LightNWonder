import { ReelReadingCard } from "@/features/analyze-spin/reel-reading-card";
import { GridFailureCard, GridPictureCard } from "@/features/evaluate-screen/grid-picture-card";
import { GameSelector } from "@/features/games/game-selector";
import { BetInfoCard } from "@/features/scatter-validation/bet-info-card";
import { ScatterChecksCard } from "@/features/scatter-validation/scatter-checks-card";
import { ScatterValidationControlCard } from "@/features/scatter-validation/scatter-validation-control-card";
import { useAnalyzeScatterValidation } from "@/features/scatter-validation/use-scatter-validation";

/**
 * One screen, read and judged: what landed on the grid, the figure on each
 * scatter, and whether that figure is one the loaded maths actually declares --
 * beside the live bet and denomination it was checked against.
 */
export function ScatterValueValidationPage() {
  const analyze = useAnalyzeScatterValidation();
  const result = analyze.data ?? null;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-end sm:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">
            Scatter Value Validation
          </h1>
          <p className="text-muted-foreground text-sm">
            Read the game&apos;s current screen, name every symbol, OCR the figure on
            each scatter, and check it against the value range the loaded maths
            declares at the live bet.
          </p>
        </div>
        <GameSelector />
      </div>

      <ScatterValidationControlCard result={result} analyze={analyze} />

      {result?.grid_image ? <GridPictureCard image={result.grid_image} /> : null}

      {/* The same card Evaluate Screen and Analyze Spin render, over the same
          reading type -- the symbols and scatters on a grid are the same answer
          everywhere they are read. */}
      {result?.reels ? <ReelReadingCard reels={result.reels} /> : null}
      {!result?.reels && result?.reels_error ? (
        <GridFailureCard error={result.reels_error} />
      ) : null}

      {result ? (
        <BetInfoCard betInfo={result.bet_info} error={result.bet_info_error} />
      ) : null}

      {result ? <ScatterChecksCard checks={result.checks} /> : null}
    </div>
  );
}
