import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SPIN_CONTROLS } from "@/features/analyze-spin/controls";
import { SpinControlCard } from "@/features/analyze-spin/spin-control-card";
import { renderWithProviders } from "@/test/utils";

/** The two mutation objects the card drives, with nothing in flight. */
function idle() {
  return { mutate: vi.fn(), reset: vi.fn(), isPending: false, error: null };
}

function renderCard({ control, run = null, active = false } = {}) {
  const start = idle();
  const cancel = idle();
  renderWithProviders(
    <SpinControlCard
      run={run}
      active={active}
      connected
      start={start}
      cancel={cancel}
      control={control}
    />,
  );
  return { start, cancel };
}

/** A finished run, carrying only what this card reads. */
function finishedRun(control) {
  return {
    run_id: "2026-09-24_18-00-00",
    label: "Fortune Ox",
    control,
    state: "completed",
    outcome: "win",
    message: "The spin won",
    duration_ms: 21400,
    frames: [],
    errors: [],
  };
}

describe("SpinControlCard", () => {
  it("asks the backend for the channel its page is for", async () => {
    // The whole of the EGM tab: the same request with `control: "gaf"`, which
    // is what stops the backend reaching for the i-deck.
    const { start } = renderCard({ control: SPIN_CONTROLS.gaf });

    await userEvent.click(screen.getByRole("button", { name: /initiate spin/i }));

    expect(start.mutate).toHaveBeenCalledWith(
      expect.objectContaining({ control: "gaf" }),
    );
  });

  it("still asks for the i-deck on the page that always did", async () => {
    const { start } = renderCard({ control: SPIN_CONTROLS.ideck });

    await userEvent.click(screen.getByRole("button", { name: /initiate spin/i }));

    expect(start.mutate).toHaveBeenCalledWith(
      expect.objectContaining({ control: "ideck" }),
    );
  });

  it("says which channel drove the run it is showing", () => {
    // One backend service holds every run and there is one cabinet, so this
    // page can be showing a run the other one started. Without this the two
    // tabs are indistinguishable after the fact.
    renderCard({ control: SPIN_CONTROLS.gaf, run: finishedRun("ideck") });

    const badge = screen.getByTitle(/started from the other/i);
    expect(badge).toHaveTextContent("i-deck");
  });

  it("marks a run this page started as its own", () => {
    renderCard({ control: SPIN_CONTROLS.gaf, run: finishedRun("gaf") });

    expect(screen.getByTitle(/started from this page/i)).toHaveTextContent("GAF");
  });

  it("names the chain each channel needs before anything has run", () => {
    // The empty state is the only place a reader is told what to start, and
    // NRobot is the one a GAF run dies without.
    const { unmount } = renderWithProviders(
      <SpinControlCard
        run={null}
        active={false}
        connected
        start={idle()}
        cancel={idle()}
        control={SPIN_CONTROLS.gaf}
      />,
    );
    expect(screen.getByText(/NRobot\.Server\.exe/)).toBeInTheDocument();
    unmount();

    renderCard({ control: SPIN_CONTROLS.ideck });
    expect(screen.getByText(/i-deck panel open/)).toBeInTheDocument();
  });
});
