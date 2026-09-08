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
 * `1250.4` is money and reads as `1,250.40`; `49531` is a credit count and reads as
 * `49,531`.
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

/** What the numbers below are *in*, stated once for the whole card. */
function Units({ mode, currency, denomination }) {
  const known = mode === "cash" || mode === "credits";
  const cash = mode === "cash";
  return (
    <span className="text-muted-foreground flex items-baseline gap-1.5 text-[0.65rem] tracking-wide uppercase">
      {known ? (
        <span>
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
      ) : (
        <span title="Nothing was readable, so there is nothing to judge the units by">
          Units unknown
        </span>
      )}

      {/* The denomination sits with the units because it is one: it converts
          between the two the mode picks between. It comes from the game's log
          rather than off the strip — the badge the games draw beside the cells
          is unlabelled and its unit glyph does not OCR at any setting. */}
      {denomination ? (
        <>
          <span aria-hidden="true">·</span>
          <span
            className="text-foreground font-mono normal-case"
            title={
              denomination.money_per_credit === null
                ? `Denomination ${denomination.value} — its paytable names no unit, so credits cannot be priced in money`
                : `One credit is ${denomination.money_per_credit}${
                    denomination.agrees === false
                      ? "; the log and the paytable disagree about the amount, so one reading is stale"
                      : ""
                  }`
            }
          >
            {denomination.label}
            {denomination.agrees === false ? (
              <span className="text-destructive ml-1">!</span>
            ) : null}
          </span>
        </>
      ) : null}
    </span>
  );
}

/** One frame's three numbers, over the strip they were read off. */
function Reading({ reading, crop, money }) {
  // The unit the strip was drawing goes first, because that one was *read*; the
  // other is derived from it through the denomination. Shown only when it has
  // figures — without a denomination there is nothing to convert with.
  const drawn = money ? reading.cash : reading.credits;
  const derived = money ? reading.credits : reading.cash;
  const hasDerived =
    derived &&
    (derived.balance !== null || derived.win !== null || derived.bet !== null);

  return (
    <div className="border-border/60 bg-muted/20 space-y-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
        <p className="text-muted-foreground text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
          {reading.label}
        </p>
        <dl className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
          {[
            ["Balance", reading.balance, drawn?.balance, derived?.balance],
            ["Win", reading.win, drawn?.win, derived?.win],
            ["Bet", reading.bet, drawn?.bet, derived?.bet],
          ].map(([label, raw, , other]) => (
            <div key={label} className="flex items-baseline gap-1.5">
              <dt className="text-muted-foreground text-[0.6rem] tracking-wide uppercase">
                {label}
              </dt>
              <dd className="font-mono text-sm font-medium tabular-nums">
                {amount(raw, money)}
                {/* The same figure in the other unit, in the same cell rather
                    than a second table: it is one number said twice, and putting
                    it beside its twin is what makes a credit award checkable
                    against a cash meter at a glance. */}
                {hasDerived && typeof other === "number" ? (
                  <span
                    className="text-muted-foreground ml-1.5 text-[0.65rem]"
                    title={`${money ? "In credits" : "In money"}, converted through the denomination`}
                  >
                    {money ? amount(other, false) : amount(other, true)}
                    {money ? " cr" : ""}
                  </span>
                ) : null}
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

/** The cash meter across every screenshot the spin took. */
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
          <Units
            mode={meter.mode}
            currency={meter.currency}
            denomination={meter.denomination}
          />
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
