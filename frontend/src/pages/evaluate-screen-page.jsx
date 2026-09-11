import { Grid3x3, TriangleAlert } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ReelReadingCard } from "@/features/analyze-spin/reel-reading-card";
import { ScreenControlCard } from "@/features/evaluate-screen/screen-control-card";
import { ScreenMeterCard } from "@/features/evaluate-screen/screen-meter-card";
import { useAnalyzeScreen } from "@/features/evaluate-screen/use-evaluate-screen";
import { GameSelector } from "@/features/games/game-selector";

/**
 * The reels crop with a ring over every cell that was named. Its own card rather
 * than a picture inside the reading: it is the evidence *for* that table, and the
 * fastest check that the grid was cut where it looks like it was.
 */
function GridPictureCard({ image }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Grid3x3 className="size-4" />
          The grid that was read
        </CardTitle>
        <CardDescription>
          Cropped from roi.reels and cut into tiles, with a ring over every cell the
          classifier named — no codes drawn on it, since those are the table below
        </CardDescription>
      </CardHeader>
      <CardContent>
        <img
          src={image}
          alt="The reels crop, with the named cells ringed"
          className="bg-muted w-full rounded-md border"
        />
      </CardContent>
    </Card>
  );
}

/** Why there is no grid reading, when there is none. */
function GridFailureCard({ error }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <TriangleAlert className="size-4 text-amber-600 dark:text-amber-500" />
          The grid could not be read
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-muted-foreground text-sm">{error}</p>
      </CardContent>
    </Card>
  );
}

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
