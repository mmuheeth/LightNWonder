import { ReelReadingCard } from "@/features/analyze-spin/reel-reading-card";
import { GridFailureCard, GridPictureCard } from "@/features/evaluate-screen/grid-picture-card";
import { ScreenControlCard } from "@/features/evaluate-screen/screen-control-card";
import { ScreenMeterCard } from "@/features/evaluate-screen/screen-meter-card";
import { useAnalyzeScreen } from "@/features/evaluate-screen/use-evaluate-screen";
import { GameSelector } from "@/features/games/game-selector";

/** One screen, read: what is on the reels, and what the meter says. */
export function EvaluateScreenPage() {
  const analyze = useAnalyzeScreen();
  const result = analyze.data ?? null;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-end sm:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Evaluate Screen</h1>
          <p className="text-muted-foreground text-sm">
            Read the game&apos;s current screen without spinning it: every symbol on the
            grid, the figure on each scatter, and the cash meter.
          </p>
        </div>
        <GameSelector />
      </div>

      <ScreenControlCard result={result} analyze={analyze} />

      {result?.grid_image ? <GridPictureCard image={result.grid_image} /> : null}

      {/* The same card Analyze Spin renders, over the same reading type — the
          symbols and the scatters on a grid are the same answer whether or not
          a spin put them there. */}
      {result?.reels ? <ReelReadingCard reels={result.reels} /> : null}
      {!result?.reels && result?.reels_error ? (
        <GridFailureCard error={result.reels_error} />
      ) : null}

      {result?.meter ? <ScreenMeterCard meter={result.meter} /> : null}
    </div>
  );
}
