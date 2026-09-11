import { ChevronRight, Gauge, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/** Money read with no symbol the engine would name — see `Units`. */
const UNNAMED_SYMBOL = "?";

/**
 * `1250.4` is money and reads as `1,250.40`; `49531` is a credit count and reads
 * as `49,531`.
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
 * What the numbers are *in*. Read off the values themselves rather than off the
 * CASH/CREDITS label, which does not OCR reliably — a currency symbol or a
 * fractional amount is what means money.
 */
function Units({ mode, currency }) {
  const known = mode === "cash" || mode === "credits";
  const money = mode === "cash";

  if (!known) {
    return (
      <span
        className="text-muted-foreground text-[0.65rem] tracking-wide uppercase"
        title="Nothing was readable, so there is nothing to judge the units by"
      >
        Units unknown
      </span>
    );
  }

  return (
    <span className="text-muted-foreground flex items-baseline gap-1.5 text-[0.65rem] tracking-wide uppercase">
      {money ? "Cash mode" : "Credits mode"}
      {money ? (
        <span className="text-foreground font-mono normal-case">
          {!currency || currency === UNNAMED_SYMBOL ? (
            <span
              className="text-muted-foreground text-[0.65rem] uppercase"
              title="A symbol is drawn here that the engine would not name"
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
 * Per-field confidence and raw text, collapsed. What to open when a figure looks
 * wrong: the number on its own cannot say whether it was misread or whether the
 * window it was read from is pointed at the wrong cell.
 */
function Fields({ fields }) {
  const entries = Object.entries(fields ?? {});
  if (entries.length === 0) return null;

  return (
    <details className="group border-t pt-3">
      <summary className="text-muted-foreground hover:text-foreground flex cursor-pointer list-none items-center gap-1.5 text-xs select-none">
        <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
        What OCR read, per cell
      </summary>
      <ul className="mt-3 space-y-1">
        {entries.map(([name, field]) => (
          <li
            key={name}
            className="flex flex-wrap items-baseline gap-x-3 font-mono text-[0.65rem]"
          >
            <span className="text-muted-foreground w-12 uppercase">{name}</span>
            <span className="w-20 tabular-nums">
              {typeof field.value === "number" ? field.value : "—"}
            </span>
            <span className="text-muted-foreground break-all">
              {field.text ? JSON.stringify(field.text) : "no text"}
            </span>
            <span className="text-muted-foreground tabular-nums">
              {field.confidence ? `${field.confidence.toFixed(0)}%` : ""}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

/**
 * Numbers the engine was sure of that belong to no cell. Loud rather than
 * collapsed: this is the one failure that otherwise looks like success — it
 * means the declared windows may not match this skin, so the figures above
 * could be the right readings of the wrong cells.
 */
function Unmapped({ unmapped }) {
  if (!unmapped?.length) return null;

  return (
    <div className="space-y-1 border-t pt-3">
      <p className="flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-500">
        <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
        {unmapped.length} number{unmapped.length === 1 ? "" : "s"} read outside every
        cell&apos;s window — this skin&apos;s layout may not match the declared one
      </p>
      <ul className="flex flex-wrap gap-x-4 gap-y-1">
        {unmapped.map((item, index) => (
          <li
            key={`${item.centre}-${index}`}
            className="flex items-baseline gap-1.5 font-mono text-[0.65rem]"
          >
            <span className="tabular-nums">{item.value}</span>
            <span className="text-muted-foreground">
              at {(item.centre * 100).toFixed(0)}% across
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The cash meter strip, as PaddleOCR read it. */
export function ScreenMeterCard({ meter }) {
  const money = meter.mode === "cash";
  const values = meter.values;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Gauge className="size-4" />
          Cash meter
        </CardTitle>
        <CardDescription>
          Read off the meter strip with PaddleOCR — the balance, the last win and the
          bet, in whichever unit the strip was drawing
        </CardDescription>
        <CardAction>
          <Badge variant="outline" className="font-mono text-[0.65rem]">
            {meter.engine}
          </Badge>
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-4">
        <Units mode={meter.mode} currency={meter.currency} />

        <dl className="flex flex-wrap items-baseline gap-x-8 gap-y-2">
          {[
            ["Balance", meter.balance],
            ["Win", meter.win],
            ["Bet", meter.bet],
          ].map(([label, value]) => (
            <div key={label} className="space-y-0.5">
              <dt className="text-muted-foreground text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
                {label}
              </dt>
              <dd className="font-mono text-lg font-medium tabular-nums">
                {amount(value, money)}
              </dd>
            </div>
          ))}
        </dl>

        {/* An empty WIN cell is ordinary between spins, so it is worth saying
            that "—" there is not a failed read. */}
        {meter.win === null && !meter.error ? (
          <p className="text-muted-foreground text-[0.65rem]">
            The WIN cell is empty, which is how it is drawn between spins
          </p>
        ) : null}

        {meter.error ? (
          <p className="flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-500">
            <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
            {meter.error}
          </p>
        ) : null}

        {/* The picture beside the numbers, because that is what makes a bad
            reading obvious at a glance. */}
        {meter.crop_image ? (
          <figure className="space-y-1">
            <img
              src={meter.crop_image}
              alt="The cash meter strip that was read"
              className="bg-muted w-full rounded-md border"
            />
            <figcaption className="text-muted-foreground text-[0.65rem]">
              roi.cash_meter, the strip these numbers were read off
            </figcaption>
          </figure>
        ) : null}

        <Unmapped unmapped={values?.unmapped} />

        <Fields fields={values?.fields} />

        {typeof values?.engine_calls === "number" ? (
          <p className="text-muted-foreground/70 font-mono text-[0.6rem]">
            {values.engine_calls} engine call
            {values.engine_calls === 1 ? "" : "s"}
            {typeof values.duration_ms === "number"
              ? ` · ${(values.duration_ms / 1000).toFixed(1)}s`
              : ""}
            {values.band?.length === 2 ? ` · rows ${values.band.join("–")}` : ""}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
