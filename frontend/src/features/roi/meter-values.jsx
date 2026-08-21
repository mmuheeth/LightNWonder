import { AlertTriangle } from "lucide-react";

import { StatRow } from "@/components/stat-row";

/**
 * The five values read off a cash-meter crop.
 *
 * Rendered inside the ROI card rather than in a card of its own: cropping the
 * meter and reading it are one action, so the numbers belong beside the picture
 * they came from.
 *
 * Two things this deliberately does not hide.
 *
 * **A low confidence is shown, not smoothed over.** A misread digit still looks
 * like a number -- `476` for `176` -- so the score the engine gave is the only
 * clue that a value is worth checking against the crop above it. Values verified
 * correct on this project's strips scored 79 or better.
 *
 * **`unmapped` is a warning, not a footnote.** A number that fell outside every
 * field's window means this skin's layout is not the expected one, which makes
 * every value above it suspect even though they all look fine.
 */

/** Below this a value is worth checking against the crop rather than trusting. */
const CONFIDENCE_FLOOR = 75;

/** `1001.4` reads as money; `49531` reads as a count. Neither wants the other's format. */
function formatValue(value, { money }) {
  if (value === null || value === undefined) return null;
  return money
    ? value.toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    : value.toLocaleString();
}

function MeterRow({ label, value, money, confidence, empty = "—" }) {
  const shown = formatValue(value, { money });
  const weak = shown !== null && confidence > 0 && confidence < CONFIDENCE_FLOOR;
  return (
    <StatRow label={label}>
      {shown === null ? (
        <span className="text-muted-foreground text-xs">{empty}</span>
      ) : (
        <span className="font-mono">
          {shown}
          {weak ? (
            <span
              className="text-muted-foreground ml-2 text-xs"
              title={`Confidence ${confidence.toFixed(0)} — check against the crop`}
            >
              ?{confidence.toFixed(0)}
            </span>
          ) : null}
        </span>
      )}
    </StatRow>
  );
}

export function MeterValues({ meter }) {
  if (!meter) return null;

  if (meter.error) {
    return (
      <div className="space-y-2 border-t pt-4">
        <p className="text-muted-foreground text-xs">
          The meter could not be read: {meter.error}
        </p>
      </div>
    );
  }

  const confidence = (name) => meter.fields?.[name]?.confidence ?? 0;
  const isCash = meter.mode === "cash";

  return (
    <div className="space-y-2 border-t pt-4">
      <p className="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.16em] uppercase">
        Meter
      </p>

      <StatRow label="Currency">
        {meter.currency ? (
          <span className="font-mono">
            {meter.currency}
            {meter.currency === "?" ? (
              <span
                className="text-muted-foreground ml-2 text-xs"
                title="A symbol is drawn here that Tesseract will not name — the yen glyph reads as nothing at every mode and scale"
              >
                unreadable symbol
              </span>
            ) : null}
          </span>
        ) : (
          <span className="text-muted-foreground text-xs">
            {isCash ? "—" : "none — credits"}
          </span>
        )}
      </StatRow>

      <MeterRow
        label="Cash"
        value={meter.cash}
        money
        confidence={confidence("cash")}
        empty={isCash ? "—" : "not in cash mode"}
      />
      <MeterRow
        label="Credits"
        value={meter.credits}
        confidence={confidence("cash")}
        empty={isCash ? "not in credits mode" : "—"}
      />
      <MeterRow
        label="Win"
        value={meter.win}
        money={isCash}
        confidence={confidence("win")}
        // An empty WIN cell is the normal state between spins, so it says so
        // rather than looking like a failed read.
        empty="none this spin"
      />
      <MeterRow
        label="Bet"
        value={meter.bet}
        money={isCash}
        confidence={confidence("bet")}
      />

      {meter.unmapped?.length ? (
        <p className="text-destructive flex items-start gap-1.5 text-xs">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>
            {meter.unmapped.length} value
            {meter.unmapped.length === 1 ? "" : "s"} read but matched no field (
            {meter.unmapped.map((item) => item.value).join(", ")}). This skin's
            layout may differ from the expected one — check the values above
            against the crop.
          </span>
        </p>
      ) : null}
    </div>
  );
}
