import { Scale } from "lucide-react";

import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { VerdictBadge } from "@/features/analyze-spin/verdict-badge";
import { cn } from "@/lib/utils";

/** Money, at the two decimals the glass draws it to. */
function money(value) {
  return typeof value === "number" ? value.toFixed(2) : "—";
}

/** A credit figure or a multiplier: an integer stays one. */
function count(value) {
  if (typeof value !== "number") return "—";
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)));
}

/**
 * One step of the conversion, or one side of the comparison.
 *
 * `operator` is the arithmetic that got here from the line above — shown in the
 * gutter rather than implied, because the whole point of listing the steps is
 * that a wrong verdict is nearly always one of them rather than the pay itself.
 */
function Step({ operator, label, value, hint, emphasis }) {
  return (
    <div
      className={cn(
        "flex items-baseline gap-3 py-1.5",
        emphasis && "border-border/60 border-t pt-2.5",
      )}
    >
      <span className="text-muted-foreground w-4 shrink-0 text-center font-mono text-sm">
        {operator ?? ""}
      </span>
      <span className={cn("text-sm", emphasis && "font-semibold")}>{label}</span>
      {hint ? (
        <span className="text-muted-foreground truncate text-[0.65rem]">{hint}</span>
      ) : null}
      <span
        className={cn(
          "ml-auto shrink-0 font-mono tabular-nums",
          emphasis ? "text-base font-semibold" : "text-sm font-medium",
        )}
      >
        {value}
      </span>
    </div>
  );
}

/** Which of the two columns the verdict was actually reached on. */
function UnitHeading({ title, validated }) {
  return (
    <div className="flex items-baseline justify-end gap-1.5">
      <span className="text-foreground text-[0.65rem] font-semibold tracking-wide uppercase">
        {title}
      </span>
      {validated ? (
        <span
          className="text-primary text-[0.55rem] font-semibold tracking-wide uppercase"
          title="The meter was drawing this unit, so this is the side the OCR reading was validated against"
        >
          validated
        </span>
      ) : (
        <span
          className="text-muted-foreground/60 text-[0.55rem] tracking-wide uppercase"
          title="Converted through the denomination rather than read, so not what the verdict compared"
        >
          derived
        </span>
      )}
    </div>
  );
}

/**
 * The spin in both units, side by side.
 *
 * A table rather than two lists because the comparison a reader makes is
 * *across* the units as often as down them — the award is priced in credits and
 * the glass may be drawing money, and one row holding both is what makes those
 * two comparable at a glance. Column headings mark which side was read: the other
 * is converted through the denomination and so cannot disagree with it.
 *
 * The last two rows are the check rather than the reading, hence the rule above
 * them: everything over it came off the meter, and what is under it is the
 * paytable's own claim against the WIN cell OCR saw.
 */
function SpinTable({ rows, inCredits }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr>
          <th className="w-1/2" />
          <th className="px-2 pb-1.5 text-right font-normal">
            <UnitHeading title="Credits" validated={inCredits} />
          </th>
          <th className="pb-1.5 text-right font-normal">
            <UnitHeading title="Cash" validated={!inCredits} />
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr
            key={row.label}
            className={cn(
              row.rule && "border-border/60 border-t",
              row.emphasis && "font-medium",
            )}
          >
            <th
              scope="row"
              className={cn(
                "py-1 text-left font-normal",
                row.rule && "pt-2.5",
                row.emphasis ? "text-foreground" : "text-muted-foreground",
              )}
            >
              {row.label}
              {row.hint ? (
                <span className="text-muted-foreground/70 ml-2 text-[0.65rem]">
                  {row.hint}
                </span>
              ) : null}
            </th>
            <td
              className={cn(
                "px-2 py-1 text-right font-mono tabular-nums",
                row.rule && "pt-2.5",
              )}
            >
              {count(row.credits)}
            </td>
            <td
              className={cn(
                "py-1 text-right font-mono tabular-nums",
                row.rule && "pt-2.5",
              )}
            >
              {money(row.cash)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * What the paytable owed against what the machine paid.
 *
 * The last card on the page, and the only one that puts two independently
 * measured numbers side by side: the credits the awarded paylines came to, from
 * the reels and the game's own maths, and the WIN cell OCR read off the result
 * screenshot. Everything before this is a *reading*; this is the check.
 *
 * **The conversion is one multiplication**: the credits awarded, times what a
 * credit is worth. A paytable combo's value *is* the award and not a per-line rate
 * to be scaled by the stake — measured on a captured win the game drew both ways,
 * `75` on a credit meter and `$0.75` on a cash one at 1c. It is still shown as a
 * ladder rather than a single figure because a wrong verdict is nearly always one
 * of the two inputs rather than the pay itself.
 *
 * **And on a credit meter there is no multiplication at all**, so the ladder does
 * not show one: the glass is already counting the thing the paytable is
 * denominated in, and the award is compared to the WIN cell directly. Every row
 * above the total is a step that actually happened — a `× 0.02` sitting over a
 * figure that did not use it is the ladder-does-not-add-up problem again, just in
 * the other direction.
 *
 * That rate row shows `money_per_credit` and not the denomination, and the
 * distinction is the other reason this card once did not add up: the game's log
 * reports a 2c cabinet as `2`, a count of cents, while one credit is worth 0.02.
 * The label ("2c") is what a reader recognises and the rate is what the arithmetic
 * uses, so the row carries both — the number in the value column, the name in the
 * hint.
 *
 * Under the ladder, the spin as a **table, in both units, always both**: before,
 * the bet, the win, after — then the paytable's claim and the WIN cell under a
 * rule, because that is where a reading becomes a check. A table rather than two
 * lists because the comparison a reader makes is *across* the units as often as
 * down them, and one row holding both is what makes a credit award checkable
 * against a cash meter at a glance. The column heading marks which side was
 * **read**: the other is converted through the denomination and so cannot disagree
 * with it.
 *
 * There is deliberately **no difference row**. The verdict badge is the answer and
 * the two figures it compared are adjacent rows in the table, so a signed delta
 * was a third way of saying the same thing. What is *not* self-evident from the
 * pair is the tolerance behind the verdict, so that says itself in the caption.
 *
 * Two things once multiplied into the expectation that should not have: the stake
 * per line, which inflated it by that stake, and the denomination itself. The
 * stake per line is no longer shown at all; it is still on the payload as
 * `credits_per_line`.
 *
 * `indeterminate` is not a soft failure, and it has two causes worth telling
 * apart: the denomination never appeared in the log, or it appeared but its
 * paytable named no unit so credits cannot be priced in money. The backend's
 * `detail` says which. An unreadable bet is no longer one of them.
 */
/** One unit's account of the spin, off the frames that hold each figure. */
function figuresOf(meter, unit) {
  const byFrame = new Map((meter?.readings ?? []).map((r) => [r.frame, r]));
  const initial = byFrame.get("initial")?.[unit] ?? {};
  const outcome = byFrame.get("outcome")?.[unit] ?? {};
  const collected = byFrame.get("collected")?.[unit];
  return {
    // Before and the bet come off the frame taken *before* the reels turned,
    // which is the only one where the balance has not moved yet.
    before: initial.balance ?? null,
    bet: initial.bet ?? null,
    won: outcome.win ?? null,
    // The final balance is the collected frame when there is one and the outcome
    // frame when there is not: take-win is skipped on a losing spin, and there
    // the result screenshot already *is* the end of the spin.
    after: collected?.balance ?? outcome.balance ?? null,
  };
}

export function AwardComparisonCard({ expected, meter }) {
  // The comparison happens in whatever the glass was drawing: that side was read
  // and the other is derived from it, so it is the only side whose difference is
  // measured rather than computed.
  const inCredits = expected.unit === "credits";
  const tolerance = inCredits ? meter?.credit_tolerance : meter?.tolerance;
  const credits = figuresOf(meter, "credits");
  const cash = figuresOf(meter, "cash");

  // What the meter said, then what the paytable says about it. The rule between
  // the two groups is the boundary between a reading and a check.
  const rows = [
    { label: "Before the spin", credits: credits.before, cash: cash.before },
    { label: "Bet value", credits: credits.bet, cash: cash.bet },
    { label: "Won", credits: credits.won, cash: cash.won },
    { label: "After the spin", credits: credits.after, cash: cash.after },
    {
      label: "Expected win",
      hint: "from the paytable",
      credits: expected.credits,
      cash: expected.cash,
      rule: true,
      emphasis: true,
    },
    {
      label: "WIN cell",
      hint: "read by OCR",
      credits: expected.observed_credits,
      cash: expected.observed_cash,
      emphasis: true,
    },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Scale className="size-4" />
          Award vs. the meter
        </CardTitle>
        <CardDescription>
          What the paytable owed for the symbols that landed, against what the WIN cell
          actually showed
        </CardDescription>
        <CardAction>
          <VerdictBadge verdict={expected.verdict} />
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-4">
        <div>
          <Step
            label="Credits awarded"
            hint={`${expected.paying_lines} paying line${
              expected.paying_lines === 1 ? "" : "s"
            }`}
            value={count(expected.credits)}
          />
          {/* The multiplication is in the ladder only when it actually happens.
              On a credit meter the glass is already counting what the paytable is
              denominated in, so there is no conversion to show and the rate moves
              below as context — a `× 0.02` row above a total that did not use it
              is the same ladder-does-not-add-up problem in the other direction.

              When it is here: the rate, not the denomination. They differ by a
              factor of a hundred -- the log reports a 2c game as `2`, and one
              credit is 0.02 -- so the row shows the number the line above it is
              actually multiplied by, and names the denomination in the hint. */}
          {inCredits ? null : (
            <Step
              operator="×"
              label="Money per credit"
              hint={
                expected.denomination_label !== null
                  ? `${expected.denomination_label} denomination, from the game's own log`
                  : "needs the denomination"
              }
              value={count(expected.money_per_credit)}
            />
          )}
          <Step
            operator="="
            label="Expected win"
            hint={
              inCredits
                ? "the meter is drawing credits, so nothing is converted"
                : undefined
            }
            value={inCredits ? count(expected.credits) : money(expected.cash)}
            emphasis
          />
        </div>

        {/* The spin in both units, always both, whichever the meter was drawing:
            the award is priced in credits and the glass may be showing money, so
            a reader checking a run against the cabinet needs the side they are
            holding. The column heading marks which one was *read*. */}
        <SpinTable rows={rows} inCredits={inCredits} />

        {/* No difference row: the verdict badge is the answer and the two figures
            it compared are in the table above, so a signed delta was a third way
            of saying the same thing. The tolerance behind that verdict is not
            self-evident from the pair, though, so it says itself here. */}
        {typeof tolerance === "number" ? (
          <p className="text-muted-foreground/70 text-[0.65rem]">
            {inCredits
              ? `Equal within ${tolerance} of a credit.`
              : `Equal within ${tolerance}, which absorbs the OCR of the last decimal.`}{" "}
            Judged on the {inCredits ? "credits" : "cash"} column, the one the meter
            drew.
          </p>
        ) : null}

        <p className="text-muted-foreground text-xs break-words">{expected.detail}</p>
      </CardContent>
    </Card>
  );
}
