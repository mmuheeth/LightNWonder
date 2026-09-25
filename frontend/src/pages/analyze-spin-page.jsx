import { SPIN_CONTROLS } from "@/features/analyze-spin/controls";
import { SpinAnalysisView } from "@/features/analyze-spin/spin-analysis-view";

/** One spin, pressed on the i-deck and collected with a click into the game. */
export function AnalyzeSpinPage() {
  return (
    <SpinAnalysisView
      control={SPIN_CONTROLS.ideck}
      heading="Analyze Spin"
      blurb="Press spin on the active game, follow it to its result, and check what it paid against the maths it loaded."
    />
  );
}
