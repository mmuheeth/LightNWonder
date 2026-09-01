import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PaylineValidationCard } from "@/features/analyze-spin/payline-validation-card";

/**
 * The card that shows what the reels landed and what the paytable pays for it.
 *
 * What these tests hold in place is the wild, because it is the one thing on this
 * card a reader cannot check by counting the codes in front of them:
 *
 * * **a run can be longer than the codes suggest.** `AA AA WC AA AA` matches five
 *   with only four Ox on screen, so the wild is marked and named once for the
 *   whole card -- otherwise a five over four visible codes reads as a bug.
 * * **a run can be shorter than the codes suggest.** `AA WC BB BB BB` matches two
 *   even though the wild sits happily beside a Pisces, so the join that broke it
 *   has to say what the run was paying as.
 * * **the length that pays is not always the length that landed.** A wild-led run
 *   is priced as whichever combo is worth more, so the derivation shows
 *   `combo_pays`, not `pays`.
 */

/** One line of the validation payload, with the fields the card actually reads. */
function line({
  name = "1",
  symbols,
  pays,
  symbol,
  leadingWilds = 0,
  awarded = true,
  comboPays = pays,
  credits = 100,
  note = null,
  breakPosition = null,
} = {}) {
  const positions = symbols.map((_code, index) => `r2c${index + 1}`);
  // `line_symbol` is what the run was paying as at each counted step, which is
  // the field the substitution is legible through.
  const steps = symbols.slice(0, -1).map((left, index) => ({
    left: positions[index],
    right: positions[index + 1],
    left_symbol: left,
    right_symbol: symbols[index + 1],
    line_symbol: index < pays ? symbol : null,
    matched: index + 1 < pays,
    counted: index < Math.max(pays, 1),
    similarity: null,
  }));
  return {
    line: name,
    label: `Line ${name}`,
    positions,
    elements: [],
    pays,
    paying: pays >= 2,
    awarded,
    steps,
    color: "#ff0000",
    break_position: breakPosition,
    symbols,
    symbol,
    symbol_name: symbol === "WC" ? "WILD" : "Ox",
    leading_wilds: leadingWilds,
    combo_id: 4,
    combo_symbols: [symbol ?? "AA", "ANY"],
    combo_pays: awarded ? comboPays : null,
    combo_value: credits,
    credits,
    min_pay_length: 3,
    note,
    image_data: null,
  };
}

/** A finished payline validation carrying those lines. */
function validation(lines, { wild = "WC" } = {}) {
  return {
    frame: "b.png",
    paytable_id: "FortuneOx-1101YX-1c-90",
    paytable_origin: "log",
    payline_set_id: "5",
    resolved_from: "game_config",
    line_count: 5,
    min_confidence: 0.85,
    wild_symbol: wild,
    pay_lengths: [5, 4, 3, 2],
    split: "screenshot-1",
    summary: "",
    runs_found: lines.filter((one) => one.paying).length,
    awarded_lines: lines.filter((one) => one.awarded).length,
    unnamed_positions: [],
    lines,
    stats: null,
    expected: null,
    output_dir: "",
    output_file: "",
    overlay_image: null,
    error: null,
  };
}

describe("PaylineValidationCard", () => {
  it("names the wild once for the card when it stood in on a line", () => {
    render(
      <PaylineValidationCard
        paylines={validation([
          line({
            symbols: ["AA", "AA", "WC", "AA", "AA"],
            pays: 5,
            symbol: "AA",
            leadingWilds: 0,
          }),
          line({
            name: "2",
            symbols: ["WC", "WC", "AA", "AA", "AA"],
            pays: 5,
            symbol: "AA",
            leadingWilds: 2,
          }),
        ])}
      />,
    );

    // Only line 2 leads with wilds, which is the one worth naming: it is the
    // case where the run is longer than a reader would count off the codes.
    const note = screen.getByText(/stood in on/);
    expect(note).toHaveTextContent("stood in on 1 line (Line 2)");
  });

  it("says nothing about a wild on a game that substitutes none", () => {
    render(
      <PaylineValidationCard
        paylines={validation(
          [line({ symbols: ["AA", "AA", "AA", "AA", "AA"], pays: 5, symbol: "AA" })],
          { wild: null },
        )}
      />,
    );

    expect(screen.queryByText(/stood in on/)).toBeNull();
  });

  it("prices a wild-led run at the combo that paid, not the run it landed", () => {
    // Four wilds then an Ox: five Ox or four wilds, and the wild's own combo is
    // worth more -- so the derivation reads "4 × WILD", never "5 × WILD".
    render(
      <PaylineValidationCard
        paylines={validation([
          line({
            symbols: ["WC", "WC", "WC", "WC", "AA"],
            pays: 5,
            symbol: "WC",
            leadingWilds: 4,
            comboPays: 4,
            credits: 150,
            note: "4 x WILD pays 150 a bet unit, more than 5 x Ox at 100",
          }),
        ])}
      />,
    );

    // The derivation is drawn twice -- in the Awarded block and again in the
    // line's dropdown -- and neither may say five.
    expect(screen.getAllByText(/4 ×/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/5 ×/)).toBeNull();
    // The run itself is still reported, and is still five.
    expect(screen.getAllByText(/matches 5/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/more than 5 x Ox/).length).toBeGreaterThan(0);
  });

  it("shows the run's own symbol on the join a wild broke", () => {
    // `AA WC BB` matches two. Both codes of the second join are on screen and
    // the join says the run stopped, which without "as AA" reads as a bug.
    render(
      <PaylineValidationCard
        paylines={validation([
          line({
            symbols: ["AA", "WC", "BB", "BB", "BB"],
            pays: 2,
            symbol: "AA",
            awarded: false,
            credits: null,
            breakPosition: "r2c3",
          }),
        ])}
      />,
    );

    expect(screen.getByText("as AA")).toBeInTheDocument();
  });
});
