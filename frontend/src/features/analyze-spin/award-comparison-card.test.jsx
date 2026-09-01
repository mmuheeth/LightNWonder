import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AwardComparisonCard } from "@/features/analyze-spin/award-comparison-card";

/**
 * The one card that is a check rather than a reading, and the one that has been
 * wrong three times: multiplying the award by the stake per line, multiplying by
 * the denomination instead of by what a credit is worth, and presenting both
 * units as though OCR had read them. So what these tests hold in place is not the
 * layout but the invariants behind it:
 *
 * * **only one unit is ever read.** The meter draws credits or money, so one of
 *   the two "read by OCR" columns is empty for the whole run and the other unit's
 *   figures are calculated from it.
 * * **a pair means a check.** A calculated figure beside a read one in the same
 *   unit is two independent measurements; a lone figure is not, which is why an
 *   opening balance has no calculated counterpart in its own unit.
 * * **every row above the total is a step that actually happened** — on a credit
 *   meter nothing is converted, so no `×` row is drawn at all.
 * * **the verdict is the backend's**, combined here and not recomputed: the
 *   tolerances live there, and two places deciding the same thing is how they
 *   come to disagree.
 */

/**
 * A 2c cabinet: 5000 credits on the meter, 88 bet, 50 won, so 4962 at the end.
 *
 * `collected: false` drops the third frame, which is what a losing spin looks
 * like -- take-win is skipped, so the result screenshot is the end of the spin.
 */
function meter({ mode = "cash", collected = true } = {}) {
  const credits = {
    initial: { balance: 5000, bet: 88, win: null },
    outcome: { balance: 4912, bet: 88, win: 50 },
    collected: { balance: 4962, bet: 88, win: 50 },
  };
  const cash = {
    initial: { balance: 100, bet: 1.76, win: null },
    outcome: { balance: 98.24, bet: 1.76, win: 1.0 },
    collected: { balance: 99.24, bet: 1.76, win: 1.0 },
  };
  const frames = collected
    ? ["initial", "outcome", "collected"]
    : ["initial", "outcome"];
  const drawn = mode === "credits" ? credits : cash;
  // The closing-balance check, as `_unit_checks` emits it in each unit. Only
  // `balance-reconciled` when the win was collected; on a losing spin `bet-deducted`
  // is asking the same question, since with nothing won the balance after the bet
  // *is* the balance at the end.
  const ending = (unit, values) =>
    collected
      ? {
          key: `balance-reconciled-${unit}`,
          unit,
          verdict: "passed",
          expected: values.collected.balance,
          actual: values.collected.balance,
        }
      : {
          key: `bet-deducted-${unit}`,
          unit,
          verdict: "passed",
          expected: values.outcome.balance,
          actual: values.outcome.balance,
        };
  return {
    mode,
    currency: mode === "cash" ? "$" : null,
    denomination: { label: "2c", money_per_credit: 0.02, agrees: true },
    tolerance: 0.005,
    credit_tolerance: 0.5,
    verdict: "passed",
    checks: [ending("credits", credits), ending("cash", cash)],
    readings: frames.map((frame) => ({
      frame,
      label: frame,
      file_name: `${frame}.png`,
      balance: drawn[frame].balance,
      win: drawn[frame].win,
      bet: drawn[frame].bet,
      credits: credits[frame],
      cash: cash[frame],
    })),
  };
}

function expected({ unit = "cash", ...overrides } = {}) {
  return {
    paying_lines: 1,
    credits: 50,
    line_count: 40,
    denomination_label: "2c",
    money_per_credit: 0.02,
    total_bet: unit === "credits" ? 88 : 1.76,
    bet_credits: 88,
    credits_per_line: 2.2,
    // 88 a spin at one credit a unit, which is the minimum bet -- the case
    // where an award and its paytable row are the same number.
    bet_per_unit: 1,
    cash: 1.0,
    unit,
    observed_win: unit === "credits" ? 50 : 1.0,
    observed_credits: 50,
    observed_cash: 1.0,
    verdict: "passed",
    detail: "1 line(s) pay 50 credits",
    ...overrides,
  };
}

describe("AwardComparisonCard", () => {
  /**
   * One table row as `{ calcCredits, calcCash, ocrCredits, ocrCash }`, which is
   * the order the four value columns appear in.
   */
  function row(label) {
    const cells = [
      ...screen.getByRole("rowheader", { name: new RegExp(`^${label}`) }).parentElement
        .children,
    ].map((cell) => cell.textContent.trim());
    const [, calcCredits, calcCash, ocrCredits, ocrCash] = cells;
    return { calcCredits, calcCash, ocrCredits, ocrCash };
  }

  it("reads only the unit the meter drew, and calculates the other", () => {
    render(
      <AwardComparisonCard expected={expected()} meter={meter({ mode: "cash" })} />,
    );

    // Cash mode: the balance was read in money and the credits figure is what
    // that divides to at 0.02 a credit. Claiming OCR saw both would claim two
    // independent readings where there is one.
    expect(row("Before the spin")).toEqual({
      calcCredits: "5000",
      calcCash: "",
      ocrCredits: "",
      ocrCash: "100.00",
    });
    expect(row("Bet value")).toEqual({
      calcCredits: "88",
      calcCash: "",
      ocrCredits: "",
      ocrCash: "1.76",
    });
  });

  it("shows the stake as its own step, since it is what prices the lines", () => {
    // 5 a bet unit turns the same 50-credit rate into 250 -- the multiplication
    // that is invisible at the minimum bet, which is why it is a step and not a
    // hint.
    render(
      <AwardComparisonCard
        expected={expected({ bet_per_unit: 5, credits: 250, cash: 5.0 })}
        meter={meter({ mode: "cash" })}
      />,
    );

    expect(screen.getByText("Line rates")).toBeInTheDocument();
    expect(screen.getByText("Bet per unit")).toBeInTheDocument();
    // The rate total is the award over the stake: 250 / 5.
    expect(screen.getByText("50")).toBeInTheDocument();
    expect(screen.getByText(/from the bet the meter drew/)).toBeInTheDocument();
  });

  it("says the award needs a stake when none was given", () => {
    render(
      <AwardComparisonCard
        expected={expected({ bet_per_unit: null, credits: 0, verdict: "indeterminate" })}
        meter={meter({ mode: "cash" })}
      />,
    );

    expect(screen.queryByText("Bet per unit")).not.toBeInTheDocument();
    expect(screen.getByText(/needs a bet per unit to price/)).toBeInTheDocument();
  });

  it("leaves the whole credits OCR column empty on a cash meter", () => {
    render(
      <AwardComparisonCard expected={expected()} meter={meter({ mode: "cash" })} />,
    );

    for (const label of ["Before the spin", "Bet value", "Won", "After the spin"]) {
      expect(row(label).ocrCredits).toBe("");
    }
  });

  it("leaves the whole cash OCR column empty on a credit meter", () => {
    render(
      <AwardComparisonCard
        expected={expected({ unit: "credits" })}
        meter={meter({ mode: "credits" })}
      />,
    );

    for (const label of ["Before the spin", "Bet value", "Won", "After the spin"]) {
      expect(row(label).ocrCash).toBe("");
    }
    // And the credits column is the one carrying the readings.
    expect(row("Before the spin").ocrCredits).toBe("5000");
  });

  it("has a calculated figure beside a read one only where a check exists", () => {
    render(
      <AwardComparisonCard expected={expected()} meter={meter({ mode: "cash" })} />,
    );

    // The award: priced off the symbols by the paytable, and read off the glass
    // by OCR. Two independent measurements, so the pair is a real check.
    expect(row("Won")).toEqual({
      calcCredits: "50",
      calcCash: "1.00",
      ocrCredits: "",
      ocrCash: "1.00",
    });
    // The closing balance: before − bet + won against what the meter ended at.
    expect(row("After the spin")).toEqual({
      calcCredits: "4962",
      calcCash: "99.24",
      ocrCredits: "",
      ocrCash: "99.24",
    });
    // An opening balance is an input -- nothing calculates it in its own unit.
    expect(row("Before the spin").calcCash).toBe("");
  });

  it("marks the read column rather than labelling either as derived", () => {
    render(
      <AwardComparisonCard expected={expected()} meter={meter({ mode: "cash" })} />,
    );

    // Two group headings, and the unit headings under them.
    expect(screen.getByText("Calculated")).toBeInTheDocument();
    expect(screen.getByText("Read by OCR")).toBeInTheDocument();
  });

  it("fails when a calculated figure and the read one disagree", () => {
    // The backend already decided this: the card takes the worst of the verdicts
    // it was handed rather than re-comparing the numbers itself.
    render(
      <AwardComparisonCard
        expected={expected({ verdict: "failed" })}
        meter={meter({ mode: "cash" })}
      />,
    );

    expect(screen.getByText(/failed/i)).toBeInTheDocument();
  });

  it("fails when the balance arithmetic disagrees even if the award matched", () => {
    const broken = meter({ mode: "cash" });
    broken.checks = broken.checks.map((check) =>
      check.key === "balance-reconciled-cash" ? { ...check, verdict: "failed" } : check,
    );

    render(<AwardComparisonCard expected={expected()} meter={broken} />);

    expect(screen.getByText(/failed/i)).toBeInTheDocument();
  });

  it("shows no difference row -- the badge is the answer", () => {
    render(
      <AwardComparisonCard expected={expected()} meter={meter({ mode: "cash" })} />,
    );

    expect(screen.queryByText("Difference")).not.toBeInTheDocument();
    // The tolerance behind that badge is not self-evident from the pair, so it
    // does still say itself.
    expect(screen.getByText(/within 0.005/)).toBeInTheDocument();
  });

  it("draws no conversion row on a credit meter, because none happens", () => {
    render(
      <AwardComparisonCard
        expected={expected({ unit: "credits" })}
        meter={meter({ mode: "credits" })}
      />,
    );

    // The rate is still named below as context -- it is only the *step* that
    // goes, because a `x 0.02` above a total that did not use it is the same
    // ladder-does-not-add-up problem the card was fixed for.
    expect(screen.queryByText("Money per credit")).not.toBeInTheDocument();
    expect(screen.getByText(/nothing is converted/)).toBeInTheDocument();
  });

  it("draws the conversion row on a cash meter, because one does happen", () => {
    render(
      <AwardComparisonCard expected={expected()} meter={meter({ mode: "cash" })} />,
    );

    expect(screen.getByText("Money per credit")).toBeInTheDocument();
    expect(screen.getByText("0.02")).toBeInTheDocument();
  });

  it("never shows the stake per line", () => {
    render(
      <AwardComparisonCard expected={expected()} meter={meter({ mode: "cash" })} />,
    );

    expect(screen.queryByText("Staked per line")).not.toBeInTheDocument();
    expect(screen.queryByText(/over 40 lines/)).not.toBeInTheDocument();
  });

  it("takes the credit tolerance when the comparison is in credits", () => {
    render(
      <AwardComparisonCard
        expected={expected({ unit: "credits" })}
        meter={meter({ mode: "credits" })}
      />,
    );

    expect(screen.getByText(/0.5 of a credit/)).toBeInTheDocument();
  });

  it("falls back to the bet-deducted check when the win was never collected", () => {
    // Take-win is skipped on a losing spin, so there is no collected frame and no
    // reconciliation check -- but with nothing won, the balance after the bet
    // already *is* the balance at the end.
    render(
      <AwardComparisonCard
        expected={expected()}
        meter={meter({ mode: "cash", collected: false })}
      />,
    );

    expect(row("After the spin")).toEqual({
      calcCredits: "4912",
      calcCash: "98.24",
      ocrCredits: "",
      ocrCash: "98.24",
    });
  });

  it("survives a meter that could not be read at all", () => {
    render(<AwardComparisonCard expected={expected()} meter={null} />);

    // Nothing was read, so every cell that would hold a reading is blank rather
    // than zero. The award itself is still the paytable's own statement.
    expect(screen.getByText("Credits awarded")).toBeInTheDocument();
    expect(row("Before the spin")).toEqual({
      calcCredits: "",
      calcCash: "",
      ocrCredits: "",
      ocrCash: "",
    });
    expect(row("Won").calcCredits).toBe("50");
  });
});
