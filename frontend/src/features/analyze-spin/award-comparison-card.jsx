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

/**
 * What the paytable owed against what the machine paid.
 *
 * The last card on the page, and the only one that puts two independently
 * measured numbers side by side: the credits the awarded paylines came to, from
 * the reels and the game's own maths, and the WIN cell OCR read off the result
 * screenshot. Everything before this is a *reading*; this is the check.
 *
 * The conversion between them is listed step by step rather than collapsed into
 * one number, because it runs through the denomination and the line count and a
 * wrong verdict is nearly always one of those rather than the pay itself. It rests
 * on one assumption, and the backend states it in exactly one place too: a line
 * combo's value is credits per line at one credit staked on that line.
 *
 * `indeterminate` is not a soft failure. If the denomination never appeared in the
 * log, or OCR could not read the bet, there was no comparison to make — and a
 * missing input and a real discrepancy need different fixes.
 */
export function AwardComparisonCard({ expected, tolerance }) {
  const difference =
    typeof expected.cash === "number" && typeof expected.observed_win === "number"
      ? expected.observed_win - expected.cash
      : null;

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
          <Step
            operator="×"
            label="Credits per line"
            hint={
              expected.bet_credits !== null && expected.line_count !== null
                ? `${count(expected.bet_credits)} credits bet over ${expected.line_count} lines`
                : "needs the bet off the meter and the line count"
            }
            value={count(expected.credits_per_line)}
          />
          <Step
            operator="×"
            label="Denomination"
            hint={
              expected.total_bet !== null
                ? `bet ${money(expected.total_bet)} on the glass`
                : "from the game's own log"
            }
            value={count(expected.denomination)}
          />
          <Step
            operator="="
            label="Expected win"
            value={money(expected.cash)}
            emphasis
          />
        </div>

        <div>
          <Step
            label="WIN cell on the result screenshot"
            hint="read by OCR"
            value={money(expected.observed_win)}
          />
          <Step
            operator="Δ"
            label="Difference"
            hint={
              typeof tolerance === "number"
                ? `equal within ${tolerance}, which absorbs the last decimal`
                : undefined
            }
            value={
              difference === null
                ? "—"
                : `${difference > 0 ? "+" : ""}${difference.toFixed(2)}`
            }
            emphasis
          />
        </div>

        <p className="text-muted-foreground text-xs break-words">{expected.detail}</p>
      </CardContent>
    </Card>
  );
}
