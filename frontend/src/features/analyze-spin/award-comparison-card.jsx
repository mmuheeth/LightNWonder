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

/** A blank cell, for a figure that does not exist rather than one that is zero. */
const EMPTY = "";

/**
 * The spin, calculated beside read.
 *
 * Four value columns in two pairs, and the split is the point: **only one unit is
 * ever read.** The meter draws credits or money, never both, so one of the two
 * "read by OCR" columns is empty for the whole run — and the figures in the other
 * unit are *calculated* from it through the denomination, which is a different kind
 * of number and cannot disagree with its source.
 *
 * That is what makes the verdict legible: where a row has a calculated figure and
 * a read one in the same unit, the two were measured independently and their
 * agreement is the check. Where it has only one, there was nothing to compare.
 */
function SpinTable({ rows, inCredits }) {
  const columns = [
    { key: "calcCredits", format: count },
    { key: "calcCash", format: money },
    { key: "ocrCredits", format: count, read: inCredits },
    { key: "ocrCash", format: money, read: !inCredits },
  ];

  return (
    <table className="w-full text-sm">
      <thead className="text-muted-foreground">
        <tr className="text-[0.6rem] tracking-[0.14em] uppercase">
          <th className="w-2/5" />
          <th colSpan={2} className="border-border/60 border-b px-2 pb-1 font-semibold">
            Calculated
          </th>
          <th colSpan={2} className="border-border/60 border-b pb-1 font-semibold">
            Read by OCR
          </th>
        </tr>
        <tr className="text-[0.6rem] tracking-wide uppercase">
          <th />
          <th className="px-2 pt-1 pb-1.5 text-right font-normal">Credits</th>
          <th className="pt-1 pb-1.5 text-right font-normal">Cash</th>
          <th className="px-2 pt-1 pb-1.5 text-right font-normal">
            <span className={cn(inCredits && "text-primary font-semibold")}>
              Credits
            </span>
          </th>
          <th className="pt-1 pb-1.5 text-right font-normal">
            <span className={cn(!inCredits && "text-primary font-semibold")}>Cash</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.label} className="border-border/40 border-t">
            <th
              scope="row"
              className="text-muted-foreground py-1.5 text-left font-normal"
            >
              {row.label}
              {row.hint ? (
                <span className="text-muted-foreground/60 ml-2 text-[0.65rem]">
                  {row.hint}
                </span>
              ) : null}
            </th>
            {columns.map(({ key, format, read }) => (
              <td
                key={key}
                className={cn(
                  "py-1.5 text-right font-mono tabular-nums",
                  key.endsWith("Credits") && "px-2",
                  // The read column is the evidence; the calculated ones are
                  // consequences of it, so they sit back a shade.
                  read === undefined && "text-muted-foreground",
                  row.verdict === "failed" && "text-destructive",
                )}
              >
                {row[key] === null || row[key] === undefined ? EMPTY : format(row[key])}
              </td>
            ))}
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
 * **The conversion is two multiplications**: a paytable combo's value is a rate
 * per *bet unit*, so the lines' rates are staked first — times the bet per unit —
 * and only the result is worth converting into money. The captured win behind
 * the second half (`75` on a credit meter, `$0.75` on a cash one at 1c) was a
 * spin at one credit a unit, where the two are the same number; it is consistent
 * with the stake and never was evidence against one. What it does rule out is
 * scaling by the stake spread over a *line*, which on FortuneOx is 88 over 40
 * lines and not a rung of anything.
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
 * Under the ladder, the spin as a table: before, bet value, won, after — with four
 * value columns in two pairs, **calculated** and **read by OCR**, each split by
 * unit.
 *
 * That split is the load-bearing part, and getting it wrong was a real bug here.
 * **Only one unit is ever read.** The meter draws credits or money, never both, so
 * one of the two OCR columns is empty for the whole run; the figures in the other
 * unit are *calculated* from it through the denomination, and a calculated figure
 * cannot disagree with the thing it was calculated from. Showing both as though
 * OCR had seen them claimed two independent readings where there was one.
 *
 * So the verdict reads straight off the table: where a row has a calculated figure
 * **and** a read one in the same unit, the two were measured independently and
 * their agreement is the check — the award against the WIN cell, and the balance
 * arithmetic against the closing balance. Where a row has only one, there was
 * nothing to compare, which is why an opening balance and a bet have no calculated
 * counterpart in their own unit. The badge is the worst of those verdicts, taken
 * from the backend's own rather than by re-comparing the numbers here: the
 * tolerances live there, and two places deciding the same thing is how they come
 * to disagree.
 *
 * There is deliberately **no difference row**. The badge is the answer and the
 * figures it compared are adjacent cells, so a signed delta was a third way of
 * saying the same thing. What is *not* self-evident from the pair is the tolerance
 * behind the verdict, so that says itself in the caption.
 *
 * The stake *per line* is not one of the multiplications and is not shown as a
 * step, though it is still on the payload as `credits_per_line`. It is a real
 * thing a player reads off the glass and it prices nothing: 88 credits over 40
 * lines is 2.2, which no ladder offers.
 *
 * `indeterminate` is not a soft failure, and it has three causes worth telling
 * apart: no bet per unit was given, so the lines' rates cannot be staked; the
 * denomination never appeared in the log; or it appeared but its paytable named
 * no unit, so credits cannot be priced in money. The backend's `detail` says
 * which. An unreadable *bet* is none of them — the stake is an input here, not
 * something read off the meter.
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

/** The worst of several verdicts, as `_verdict_of` ranks them on the backend. */
function worstVerdict(verdicts) {
  const known = verdicts.filter(Boolean);
  if (!known.length) return "indeterminate";
  if (known.includes("failed")) return "failed";
  if (known.includes("indeterminate")) return "indeterminate";
  return "passed";
}

/**
 * The check that says where the balance ended up, in one unit.
 *
 * `balance-reconciled` is the whole spin as one identity and is the right answer
 * when the win was collected. On a losing spin take-win is skipped, so there is no
 * collected frame and no such check — but `bet-deducted` is asking the same
 * question there, since with nothing won the balance after the bet *is* the
 * balance at the end.
 */
function endingCheck(meter, unit) {
  const checks = meter?.checks ?? [];
  return (
    checks.find((c) => c.key === `balance-reconciled-${unit}`) ??
    checks.find((c) => c.key === `bet-deducted-${unit}`) ??
    null
  );
}

export function AwardComparisonCard({ expected, meter }) {
  // Only one unit is ever *read*: the meter draws it. The other is computed from
  // it through the denomination, so it can never disagree and is never evidence.
  const inCredits = expected.unit === "credits";
  const tolerance = inCredits ? meter?.credit_tolerance : meter?.tolerance;
  const credits = figuresOf(meter, "credits");
  const cash = figuresOf(meter, "cash");
  const endCredits = endingCheck(meter, "credits");
  const endCash = endingCheck(meter, "cash");

  // Which of the four value columns a figure belongs in. A figure the meter drew
  // goes under "read by OCR" in its own unit and nowhere else; everything else is
  // calculated, whether from the paytable, from the arithmetic, or by converting
  // the drawn unit into the other one.
  const readCredits = (value) => (inCredits ? value : null);
  const readCash = (value) => (inCredits ? null : value);
  const convCredits = (value) => (inCredits ? null : value);
  const convCash = (value) => (inCredits ? value : null);

  const rows = [
    {
      label: "Before the spin",
      // Nothing calculates an opening balance -- it is an input. So the only
      // calculated cell on this row is the conversion into the other unit.
      calcCredits: convCredits(credits.before),
      calcCash: convCash(cash.before),
      ocrCredits: readCredits(credits.before),
      ocrCash: readCash(cash.before),
    },
    {
      label: "Bet value",
      calcCredits: convCredits(credits.bet),
      calcCash: convCash(cash.bet),
      ocrCredits: readCredits(credits.bet),
      ocrCash: readCash(cash.bet),
    },
    {
      label: "Won",
      hint: "paytable vs. the WIN cell",
      // The one row where both sides are measured independently: the paytable
      // priced this off the symbols that landed, and OCR read it off the glass.
      calcCredits: expected.credits,
      calcCash: expected.cash,
      ocrCredits: readCredits(expected.observed_credits ?? expected.observed_win),
      ocrCash: readCash(expected.observed_cash ?? expected.observed_win),
      verdict: expected.verdict,
    },
    {
      label: "After the spin",
      hint: "before − bet + won",
      calcCredits: endCredits?.expected ?? null,
      calcCash: endCash?.expected ?? null,
      ocrCredits: readCredits(endCredits?.actual ?? credits.after),
      ocrCash: readCash(endCash?.actual ?? cash.after),
      verdict: (inCredits ? endCredits : endCash)?.verdict ?? null,
    },
  ];

  // Pass only if every row that has both a calculated and a read figure agrees.
  // Taken from the backend's own verdicts rather than by re-comparing the numbers
  // here: the tolerances live there, and two places deciding the same thing is
  // how they come to disagree.
  const verdict = worstVerdict(rows.map((row) => row.verdict));

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
          <VerdictBadge verdict={verdict} />
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-4">
        <div>
          {/* The first multiplication, and the only input on this card that was
              given rather than measured. A paytable value is a rate per bet
              unit, so the rows the maths supplies are worth nothing until the
              stake says how many units — and a wrong stake misprices the whole
              spin while every reading behind it stays correct. Shown as its own
              step for exactly that reason. Divided back out of the total rather
              than passed in: `credits` is the sum of the lines already priced,
              so the rate total is that over the stake, exactly. */}
          {expected.bet_per_unit ? (
            <>
              <Step
                label="Line rates"
                hint={`${expected.paying_lines} paying line${
                  expected.paying_lines === 1 ? "" : "s"
                }, per bet unit`}
                value={count(expected.credits / expected.bet_per_unit)}
              />
              <Step
                operator="×"
                label="Bet per unit"
                hint="from the bet the meter drew"
                value={count(expected.bet_per_unit)}
              />
            </>
          ) : null}
          <Step
            operator={expected.bet_per_unit ? "=" : undefined}
            label="Credits awarded"
            hint={
              expected.bet_per_unit
                ? undefined
                : `${expected.paying_lines} paying line${
                    expected.paying_lines === 1 ? "" : "s"
                  } — needs a bet per unit to price`
            }
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

        {/* Calculated beside read, in both units. One "read by OCR" column is
            empty for the whole run, because the meter draws one unit and not the
            other — and that is the honest shape: the other unit's figures are
            calculated from it and were never independently seen. */}
        <SpinTable rows={rows} inCredits={inCredits} />

        {/* No difference row: the badge is the answer and the figures it compared
            are adjacent cells, so a signed delta was a third way of saying the
            same thing. The tolerance behind the verdict is not self-evident from
            the pair, though, so it says itself here. */}
        <p className="text-muted-foreground/70 text-[0.65rem]">
          Passes when every calculated figure matches the one OCR read in the same unit
          {typeof tolerance === "number"
            ? inCredits
              ? `, within ${tolerance} of a credit`
              : `, within ${tolerance} — enough to absorb the OCR of the last decimal`
            : ""}
          . The meter drew {inCredits ? "credits" : "cash"}, so that is the column
          judged; the other is calculated from it and cannot disagree.
        </p>

        <p className="text-muted-foreground text-xs break-words">{expected.detail}</p>
      </CardContent>
    </Card>
  );
}
