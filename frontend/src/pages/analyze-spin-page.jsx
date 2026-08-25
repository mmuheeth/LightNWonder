import { ApiErrorAlert } from "@/components/api-error-alert";
import { Skeleton } from "@/components/ui/skeleton";
import { MeterValidationCard } from "@/features/analyze-spin/meter-validation-card";
import { PaylineValidationCard } from "@/features/analyze-spin/payline-validation-card";
import { SpinControlCard } from "@/features/analyze-spin/spin-control-card";
import { SpinTimeline } from "@/features/analyze-spin/spin-timeline";
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
 * is: the sequence is a twelve-row list, the meter is three readings and five
 * relations, and the paylines are up to forty rows with a picture each. None of
 * that survives half a row.
 *
 * Read down: the button and what the spin did, then how it got there, then the
 * two validations over what it produced. The validations appear as their steps
 * finish, which is why they are rendered from the run rather than gated on it
 * being over — a completed cash-meter check is worth reading while the paylines
 * are still being compared.
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
          {run?.meter ? (
            <MeterValidationCard meter={run.meter} detailed={detailed?.meter} />
          ) : null}
          {run?.paylines ? (
            <PaylineValidationCard
              paylines={run.paylines}
              detailed={detailed?.paylines}
            />
          ) : null}
        </>
      )}
    </div>
  );
}
