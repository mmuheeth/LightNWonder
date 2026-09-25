import { SPIN_CONTROLS } from "@/features/analyze-spin/controls";
import { SpinAnalysisView } from "@/features/analyze-spin/spin-analysis-view";

/**
 * The same spin, driven over GAF.
 *
 * Its own route rather than a toggle on the other one because the choice is
 * made before the run and never during it, and because the two are worth
 * comparing: the same cabinet, the same maths, the same thirteen steps, with
 * only the spin press and the collect coming from the game's own methods
 * instead of from a panel key and a click at measured coordinates.
 */
export function AnalyzeSpinEgmPage() {
  return (
    <SpinAnalysisView
      control={SPIN_CONTROLS.gaf}
      heading="Analyze Spin with EGM"
      blurb="Spin the active game through its own automation service, follow it to its result, and check what it paid against the maths it loaded."
    />
  );
}
