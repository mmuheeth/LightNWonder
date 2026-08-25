import { Gauge } from "lucide-react";

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

function amount(value) {
  return typeof value === "number" ? value.toFixed(2) : "—";
}

/** One frame's three numbers, over the strip they were read off. */
function Reading({ reading, crop }) {
  return (
    <div className="border-border/60 bg-muted/20 space-y-2 rounded-lg border p-3">
      <p className="text-muted-foreground text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
        {reading.label}
      </p>

      <dl className="grid grid-cols-3 gap-2">
        {[
          ["Balance", reading.balance],
          ["Win", reading.win],
          ["Bet", reading.bet],
        ].map(([label, value]) => (
          <div key={label} className="space-y-0.5">
            <dt className="text-muted-foreground text-[0.6rem] tracking-wide uppercase">
              {label}
            </dt>
            <dd className="font-mono text-sm font-medium tabular-nums">
              {amount(value)}
            </dd>
          </div>
        ))}
      </dl>

      {/* The strip itself, because a failed check is nearly always one misread
          digit and this is the only place that can be seen. */}
      {crop ? (
        <img
          src={crop}
          alt={`Cash meter as it read ${reading.label.toLowerCase()}`}
          className="bg-muted w-full rounded border"
        />
      ) : null}

      {reading.error ? (
        <p className="text-destructive text-xs break-words">{reading.error}</p>
      ) : null}
      <p className="text-muted-foreground/70 font-mono text-[0.6rem] break-all">
        {reading.file_name}
      </p>
    </div>
  );
}

/** One relation between two frames, with the arithmetic that decided it. */
function Check({ check }) {
  return (
    <li
      className={cn(
        "border-border/60 flex flex-col gap-1 rounded-lg border-l-4 py-2 pr-3 pl-3",
        check.verdict === "passed" && "border-l-emerald-500",
        check.verdict === "failed" && "border-l-destructive",
        check.verdict === "indeterminate" && "border-l-muted-foreground/40",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm">{check.label}</span>
        <VerdictBadge verdict={check.verdict} />
      </div>
      <p className="text-muted-foreground font-mono text-xs break-words">
        {check.detail}
      </p>
    </li>
  );
}

/**
 * The cash meter across every screenshot the spin took, and what the
 * differences between them prove.
 *
 * The readings lead and the checks follow, because a check is a statement about
 * two readings and reading it without them is unactionable — the numbers are the
 * answer, and "failed" is a consequence. Crops come from the report rather than
 * the progress stream, so they appear a moment after the verdict does.
 */
export function MeterValidationCard({ meter, detailed }) {
  const crops = new Map(
    (detailed?.readings ?? []).map((reading) => [reading.frame, reading.crop_image]),
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Gauge className="size-4" />
          Cash meter
        </CardTitle>
        <CardDescription>
          Read off every screenshot, then checked against each other
        </CardDescription>
        <CardAction>
          <VerdictBadge verdict={meter.verdict} />
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-4">
        {meter.error ? (
          <p className="text-destructive text-sm break-words">{meter.error}</p>
        ) : null}

        {meter.readings.length > 0 ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {meter.readings.map((reading) => (
              <Reading
                key={reading.frame}
                reading={reading}
                crop={crops.get(reading.frame) ?? null}
              />
            ))}
          </div>
        ) : null}

        {meter.checks.length > 0 ? (
          <div className="space-y-2 border-t pt-4">
            <h3 className="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.16em] uppercase">
              Checks
            </h3>
            <ul className="space-y-1.5">
              {meter.checks.map((check) => (
                <Check key={check.key} check={check} />
              ))}
            </ul>
            <p className="text-muted-foreground text-[0.65rem]">
              Two amounts count as equal within {meter.tolerance}, which absorbs the OCR
              of the last decimal and nothing larger.
            </p>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
