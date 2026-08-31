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

/** Money read with no symbol Tesseract would name — see `Units` below. */
const UNNAMED_SYMBOL = "?";

/**
 * `1250.4` is money and reads as `1,250.40`; `49531` is a credit count and reads
 * as `49,531`. Neither wants the other's format, which is the whole reason the
 * mode is on the payload.
 */
function amount(value, money) {
  if (typeof value !== "number") return "—";
  return money
    ? value.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    : value.toLocaleString();
}

/**
 * What the numbers below are *in*, stated once for the whole card.
 *
 * Once rather than per figure, and per card rather than per frame: the units are
 * a property of the machine, not of a screenshot, so the backend resolves one
 * answer across every frame it read (`meter.mode`) and the per-frame reading
 * stays on `readings[].values` for a run where they disagreed. Prefixing nine
 * figures with a symbol would say it eight times over.
 *
 * It sits beside the verdict because it qualifies the verdict: the arithmetic
 * under it — the bet leaving the balance, the win joining it — is checked in
 * whatever units the glass was drawing, and a reader comparing a balance against
 * the game needs to know which.
 */
function Units({ mode, currency }) {
  if (mode !== "cash" && mode !== "credits") {
    return (
      <span
        className="text-muted-foreground text-[0.65rem] tracking-wide uppercase"
        title="Nothing was readable, so there is nothing to judge the units by"
      >
        Units unknown
      </span>
    );
  }
  const cash = mode === "cash";
  return (
    <span className="text-muted-foreground text-[0.65rem] tracking-wide uppercase">
      {cash ? "Cash" : "Credits"}
      {cash ? (
        <span className="text-foreground ml-1.5 font-mono normal-case">
          {currency === UNNAMED_SYMBOL || !currency ? (
            <span
              className="text-muted-foreground text-[0.65rem] uppercase"
              title="A symbol is drawn here that Tesseract will not name — the yen glyph reads as nothing at every mode and scale"
            >
              symbol unreadable
            </span>
          ) : (
            currency
          )}
        </span>
      ) : null}
    </span>
  );
}

/**
 * One frame's three numbers, over the strip they were read off.
 *
 * A full row each rather than three across, because the crop *is* the evidence:
 * the meter is a wide, thin strip of small digits, and at a third of the card's
 * width a misread 8 for a 3 is not something a reader can see. The numbers sit on
 * one line above it so the whole reading is still one glance.
 */
function Reading({ reading, crop, money }) {
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
                {amount(value, money)}
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
 * What the header does carry beside that verdict is the units — cash and its
 * currency, or credits — because every figure below is ambiguous without them.
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
        <CardAction className="flex items-center gap-3">
          <Units mode={meter.mode} currency={meter.currency} />
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
            // Credits are whole; only money wants the two decimals. An unknown
            // mode means nothing read, so the format never shows.
            money={meter.mode !== "credits"}
          />
        ))}
      </CardContent>
    </Card>
  );
}
