import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AwardComparisonCard } from "@/features/analyze-spin/award-comparison-card";

/**
 * The one card that is a check rather than a reading, and the one that has been
 * arithmetically wrong twice: once multiplying the award by the stake per line,
 * once multiplying by the denomination instead of by what a credit is worth. So
 * what these tests hold in place is not the layout but the two invariants behind
 * it:
 *
 * * **every row above the total is a step that actually happened** — on a credit
 *   meter nothing is converted, so no `×` row is drawn at all;
 * * **both units are always accounted for, and only one of them is evidence** —
 *   the side the meter drew was read, the other is derived from it.
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
  return {
    mode,
    currency: mode === "cash" ? "$" : null,
    denomination: { label: "2c", money_per_credit: 0.02, agrees: true },
    tolerance: 0.005,
    credit_tolerance: 0.5,
    verdict: "passed",
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
  /** One table row, as `[label, credits, cash]`. */
  function row(label) {
    const cells = screen.getByRole("rowheader", { name: new RegExp(`^${label}`) })
      .parentElement.children;
    return [...cells].map((cell) => cell.textContent.trim());
  }

  it("shows every figure of the spin in both units, one row each", () => {
    render(
      <AwardComparisonCard expected={expected()} meter={meter({ mode: "cash" })} />,
    );

    // Credits and cash on the same row, so the two are comparable across as well
    // as down: 5000 credits is 100.00 at 0.02 a credit.
    expect(row("Before the spin")).toEqual(["Before the spin", "5000", "100.00"]);
    expect(row("Bet value")).toEqual(["Bet value", "88", "1.76"]);
    expect(row("Won")).toEqual(["Won", "50", "1.00"]);
    // 4912 credits after the bet came off, plus the 50 won when it was collected.
    expect(row("After the spin")).toEqual(["After the spin", "4962", "99.24"]);
  });

  it("puts the paytable's claim and the OCR reading in the same table", () => {
    render(
      <AwardComparisonCard expected={expected()} meter={meter({ mode: "cash" })} />,
    );

    expect(row("Expected win")).toEqual([
      "Expected winfrom the paytable",
      "50",
      "1.00",
    ]);
    expect(row("WIN cell")).toEqual(["WIN cellread by OCR", "50", "1.00"]);
  });

  it("marks the unit the meter drew as the validated column", () => {
    render(
      <AwardComparisonCard expected={expected()} meter={meter({ mode: "cash" })} />,
    );

    // Only one of the two is evidence: the other is converted through the
    // denomination and cannot disagree with it.
    const [, creditsColumn, cashColumn] = screen.getAllByRole("columnheader");
    expect(creditsColumn).toHaveTextContent(/CreditsDerived/i);
    expect(cashColumn).toHaveTextContent(/Cashvalidated/i);
  });

  it("validates against the credit column on a credit meter", () => {
    render(
      <AwardComparisonCard
        expected={expected({ unit: "credits" })}
        meter={meter({ mode: "credits" })}
      />,
    );

    const [, creditsColumn, cashColumn] = screen.getAllByRole("columnheader");
    expect(creditsColumn).toHaveTextContent(/Creditsvalidated/i);
    expect(cashColumn).toHaveTextContent(/Cashderived/i);
  });

  it("shows no difference row -- the badge is the answer", () => {
    render(
      <AwardComparisonCard expected={expected()} meter={meter({ mode: "cash" })} />,
    );

    expect(screen.queryByText("Difference")).not.toBeInTheDocument();
    // The tolerance behind that badge is not self-evident from the pair, so it
    // does still say itself.
    expect(screen.getByText(/Equal within 0.005/)).toBeInTheDocument();
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

  it("falls back to the result frame when the win was never collected", () => {
    // Take-win is skipped on a losing spin, so there is no collected frame and
    // the result screenshot already *is* the end of the spin.
    render(
      <AwardComparisonCard
        expected={expected()}
        meter={meter({ mode: "cash", collected: false })}
      />,
    );

    expect(row("After the spin")).toEqual(["After the spin", "4912", "98.24"]);
  });

  it("survives a meter that could not be read at all", () => {
    render(<AwardComparisonCard expected={expected()} meter={null} />);

    // Nothing to summarise, but the award itself is still the paytable's own
    // statement and is worth showing beside an em dash.
    expect(screen.getByText("Credits awarded")).toBeInTheDocument();
    expect(row("Before the spin")).toEqual(["Before the spin", "—", "—"]);
  });
});
