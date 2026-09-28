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
 * Shared by both Analyze Spin routes rather than copied, because the channel
 * that presses spin and collects the win is the *only* difference between
 * them — same thirteen steps, same screenshots, same three readings at the
 * end. Two pages rendering this with a different `control` is what keeps that
 * true: a card added here appears on both, and a fix cannot land on one.
 *
 * @param {{control: object, heading: string, blurb: string}} props `control` is
 *   one entry of `SPIN_CONTROLS`.
 */
export function SpinAnalysisView({ control, heading, blurb }) {
  const { run, detailed, active, connected, error, isPending } = useSpinView();
  const start = useStartSpin();
  const cancel = useCancelSpin();

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-end sm:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">{heading}</h1>
          <p className="text-muted-foreground text-sm">{blurb}</p>
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
            control={control}
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
