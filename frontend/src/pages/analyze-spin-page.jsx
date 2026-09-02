import { ApiErrorAlert } from "@/components/api-error-alert";
import { Skeleton } from "@/components/ui/skeleton";
import { AwardComparisonCard } from "@/features/analyze-spin/award-comparison-card";
import { MeterValidationCard } from "@/features/analyze-spin/meter-validation-card";
import { PaylineValidationCard } from "@/features/analyze-spin/payline-validation-card";
import { ReelReadingCard } from "@/features/analyze-spin/reel-reading-card";
import { SpinControlCard } from "@/features/analyze-spin/spin-control-card";
import { SpinTimeline } from "@/features/analyze-spin/spin-timeline";
import { TileClipsCard } from "@/features/analyze-spin/tile-clips-card";
import {
  useCancelSpin,
  useSpinView,
  useStartSpin,
} from "@/features/analyze-spin/use-analyze-spin";
import { GameSelector } from "@/features/games/game-selector";

/**
 * One spin, driven and then graded.
 *
 * Its own route rather than a dashboard card for the same reason Game Config
 * is: the sequence is a thirteen-row list, the meter is three readings and five
 * relations, the reel reading is two grids of fifteen cells, and the paylines are
 * up to forty rows with a picture each. None of that survives half a row.
 *
 * Read down: the button and what the spin did, then how it got there, then what
 * it produced — the symbols the classifier read off the reels, the lines those
 * symbols paid, and the cash meter. Each appears as its own step finishes, which
 * is why they are rendered from the run rather than gated on it being over: a
 * completed payline check is worth reading while the cash meter is still being
 * read.
 *
 * The reel reading sits above the paylines because the paylines are read *from*
 * it. A spin whose lines report no run and whose reading shows half its tiles
 * unnamed is a confidence-floor question, and that only reads in that order.
 *
 * The cash meter sits below the paylines because it now reads last, once there
 * is a picture-priced award to check it against — see the ordering note on
 * `services/analyze_spin.py`. The award comparison is last of all on purpose:
 * everything above it is a *reading* — the symbols, the lines, the meter — and
 * this is the one place two independently measured numbers are put against
 * each other: the credits the paytable owed for what landed, and the WIN cell
 * OCR read off the glass. Putting it first would make it a headline; last, it
 * is a conclusion with its working above it.
 */
export function AnalyzeSpinPage() {
  const { run, detailed, active, connected, error, isPending } = useSpinView();
  const start = useStartSpin();
  const cancel = useCancelSpin();

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-end sm:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Analyze Spin</h1>
          <p className="text-muted-foreground text-sm">
            Press spin on the active game, follow it to its result, and check what it
            paid against the maths it loaded.
          </p>
        </div>
        <GameSelector />
      </div>

      {/* The stream carries the run, so an unreachable report endpoint is worth
          saying but never worth hiding the button for. */}
      {error && !run ? <ApiErrorAlert error={error} /> : null}

      {isPending ? (
        <div className="space-y-6">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      ) : (
        <>
          <SpinControlCard
            run={run}
            active={active}
            connected={connected}
            start={start}
            cancel={cancel}
          />

          {run ? <SpinTimeline run={run} /> : null}
          {run?.reels ? <ReelReadingCard reels={run.reels} /> : null}
          {/* Below the reading it belongs beside: the codes say what the model
              thought landed, and these say what the cabinet did about it.
              Rendered only when a run that was recording won something, so it
              is absent rather than empty on every other spin. */}
          {run?.tile_clips?.clips?.length ? (
            <TileClipsCard runId={run.run_id} clips={run.tile_clips} />
          ) : null}
          {run?.paylines ? (
            <PaylineValidationCard
              paylines={run.paylines}
              detailed={detailed?.paylines}
            />
          ) : null}
          {run?.meter ? (
            <MeterValidationCard meter={run.meter} detailed={detailed?.meter} />
          ) : null}
          {run?.paylines?.expected ? (
            <AwardComparisonCard expected={run.paylines.expected} meter={run.meter} />
          ) : null}
        </>
      )}
    </div>
  );
}
