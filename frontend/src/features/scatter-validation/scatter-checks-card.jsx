import { ChevronRight, CircleCheck, CircleX, HelpCircle, ListChecks } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const STATUS_META = {
  matched: {
    label: "Matched",
    icon: CircleCheck,
    className: "text-emerald-600 dark:text-emerald-500",
  },
  not_matched: {
    label: "Not matched",
    icon: CircleX,
    className: "text-red-600 dark:text-red-500",
  },
  unreadable: {
    label: "Unreadable",
    icon: HelpCircle,
    className: "text-amber-600 dark:text-amber-500",
  },
  no_table: {
    label: "No value table",
    icon: HelpCircle,
    className: "text-muted-foreground",
  },
};

/** What a check found, as read (a figure, a jackpot label, or neither). */
function readFigure(check) {
  if (check.ocr_prize_label) return check.ocr_prize_label;
  if (typeof check.ocr_value === "number") return String(check.ocr_value);
  return "—";
}

/**
 * Whether `expected_values` is math.xml's own multipliers turned into money
 * (currently only at $2) rather than the raw table -- the report shows the
 * raw figures underneath when it is, so a reader can see where the shown
 * numbers came from.
 */
function isScaled(check) {
  return check.money_per_credit != null && check.money_per_credit !== 1.0;
}

function StatusBadge({ status }) {
  const meta = STATUS_META[status] ?? STATUS_META.no_table;
  const Icon = meta.icon;
  return (
    <span className={["flex items-center gap-1 text-xs font-medium", meta.className].join(" ")}>
      <Icon className="size-3.5 shrink-0" />
      {meta.label}
    </span>
  );
}

/** One landed scatter, its OCR'd figure, and what it was judged against. */
function CheckRow({ check }) {
  return (
    <tr className="border-b last:border-0 align-top">
      <td className="py-2 pr-3">
        <div className="flex items-center gap-1.5">
          <Badge variant="outline" className="font-mono text-[0.65rem]">
            {check.symbol}
          </Badge>
          <span className="text-xs">{check.label}</span>
        </div>
        <span className="text-muted-foreground font-mono text-[0.65rem]">
          {check.name} (row {check.row}, col {check.column})
        </span>
      </td>
      <td className="py-2 pr-3 font-mono text-sm font-semibold tabular-nums">
        {readFigure(check)}
      </td>
      <td className="py-2 pr-3">
        <div className="flex flex-wrap gap-1">
          {check.expected_values.map((value) => (
            <Badge key={value} variant="secondary" className="font-mono text-[0.65rem]">
              {value}
            </Badge>
          ))}
          {check.expected_jackpot_labels.map((label) => (
            <Badge key={label} variant="secondary" className="font-mono text-[0.65rem]">
              {label}
            </Badge>
          ))}
          {check.expected_values.length === 0 && check.expected_jackpot_labels.length === 0 ? (
            <span className="text-muted-foreground text-xs">—</span>
          ) : null}
        </div>
        {check.bet != null ? (
          <span className="text-muted-foreground text-[0.6rem]">at bet {check.bet}</span>
        ) : null}
        {isScaled(check) ? (
          <div className="text-muted-foreground mt-0.5 text-[0.6rem]">
            math.xml: {check.raw_expected_values.join(", ")} &times; {check.money_per_credit}
          </div>
        ) : null}
      </td>
      <td className="py-2">
        <StatusBadge status={check.status} />
        {check.reason ? (
          <p className="text-muted-foreground mt-0.5 max-w-xs text-[0.65rem]">{check.reason}</p>
        ) : null}
      </td>
    </tr>
  );
}

/**
 * Every landed scatter, judged against the value range the loaded maths declares
 * for it. The judgement neither Evaluate Screen nor the paytable view makes on
 * its own -- this is the one new comparison this tab exists for.
 */
export function ScatterChecksCard({ checks }) {
  if (!checks?.length) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ListChecks className="size-4" />
            Scatter value checks
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">
            No scatter symbols landed on this screen.
          </p>
        </CardContent>
      </Card>
    );
  }

  const notMatched = checks.filter((c) => c.status === "not_matched").length;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ListChecks className="size-4" />
          Scatter value checks
        </CardTitle>
        <CardDescription>
          Every figure PaddleOCR read off a landed scatter, checked against the
          value range math.xml declares for it at the live bet
        </CardDescription>
        <CardAction>
          <Badge
            variant={notMatched > 0 ? "destructive" : "secondary"}
            className="font-mono text-[0.65rem]"
          >
            {notMatched > 0 ? `${notMatched} not matched` : `${checks.length} checked`}
          </Badge>
        </CardAction>
      </CardHeader>

      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.14em] uppercase">
              <tr className="border-b">
                <th className="py-2 pr-3 text-left font-semibold">Scatter</th>
                <th className="py-2 pr-3 text-left font-semibold">Read</th>
                <th className="py-2 pr-3 text-left font-semibold">Expected</th>
                <th className="py-2 text-left font-semibold">Status</th>
              </tr>
            </thead>
            <tbody>
              {checks.map((check) => (
                <CheckRow key={check.name} check={check} />
              ))}
            </tbody>
          </table>
        </div>

        <details className="group mt-3 border-t pt-3">
          <summary className="text-muted-foreground hover:text-foreground flex cursor-pointer list-none items-center gap-1.5 text-[0.65rem] select-none">
            <ChevronRight className="size-3 transition-transform group-open:rotate-90" />
            What OCR read, raw
          </summary>
          <ul className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
            {checks.map((check) => (
              <li
                key={check.name}
                className="flex items-baseline gap-1.5 font-mono text-[0.65rem]"
              >
                <span className="text-muted-foreground">{check.name}</span>
                <span className="break-all">
                  {check.ocr_text ? JSON.stringify(check.ocr_text) : "no text"}
                </span>
                {typeof check.ocr_confidence === "number" ? (
                  <span className="text-muted-foreground tabular-nums">
                    {check.ocr_confidence.toFixed(0)}%
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      </CardContent>
    </Card>
  );
}
