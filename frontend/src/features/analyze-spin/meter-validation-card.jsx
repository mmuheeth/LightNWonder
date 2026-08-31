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

function amount(value) {
  return typeof value === "number" ? value.toFixed(2) : "—";
}

/**
 * One frame's three numbers, over the strip they were read off.
 *
 * A full row each rather than three across, because the crop *is* the evidence:
 * the meter is a wide, thin strip of small digits, and at a third of the card's
 * width a misread 8 for a 3 is not something a reader can see. The numbers sit on
 * one line above it so the whole reading is still one glance.
 */
function Reading({ reading, crop }) {
  return (
    <div className="border-border/60 bg-muted/20 space-y-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
        <p className="text-muted-foreground text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
          {reading.label}
        </p>
        <dl className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
          {[
            ["Balance", reading.balance],
            ["Win", reading.win],
            ["Bet", reading.bet],
          ].map(([label, value]) => (
            <div key={label} className="flex items-baseline gap-1.5">
              <dt className="text-muted-foreground text-[0.6rem] tracking-wide uppercase">
                {label}
              </dt>
              <dd className="font-mono text-sm font-medium tabular-nums">
                {amount(value)}
              </dd>
            </div>
          ))}
        </dl>
        <span className="text-muted-foreground/70 ml-auto font-mono text-[0.6rem] break-all">
          {reading.file_name}
        </span>
      </div>

      {/* The strip itself, at the full width of the card: a wrong reading is
          nearly always one misread digit, and this is the only place that can be
          seen. */}
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
    </div>
  );
}

/**
 * The cash meter across every screenshot the spin took.
 *
 * Just the readings: one row per frame, each carrying its three numbers and the
 * strip they came off at full width. The relations between them (the bet came off
 * the balance, the win went onto it) are still computed and still on the payload
 * as `checks`, and `verdict` in the header is their summary — they are simply not
 * a table worth reading past on the way to the award. The one comparison a reader
 * actually wants is the WIN cell against what the paytable owed, and that has its
 * own card at the foot of the page.
 *
 * Crops come from the report rather than the progress stream, so they appear a
 * moment after the numbers do.
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
          Read off every screenshot the spin took, in the order it took them
        </CardDescription>
        <CardAction>
          <VerdictBadge verdict={meter.verdict} />
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-3">
        {meter.error ? (
          <p className="text-destructive text-sm break-words">{meter.error}</p>
        ) : null}

        {meter.readings.map((reading) => (
          <Reading
            key={reading.frame}
            reading={reading}
            crop={crops.get(reading.frame) ?? null}
          />
        ))}
      </CardContent>
    </Card>
  );
}
