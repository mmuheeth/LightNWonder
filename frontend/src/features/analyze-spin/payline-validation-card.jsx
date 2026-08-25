import {
  ChevronDown,
  ChevronRight,
  CircleCheck,
  CircleSlash,
  Route,
  TriangleAlert,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
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

function numeric(value) {
  if (typeof value !== "number") return "—";
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)));
}

/** A similarity score at the precision the clusters are actually apart by. */
function score(value) {
  return typeof value === "number" ? value.toFixed(4) : "—";
}

/** A credit total that may be a range, written as one number when it is one. */
function span(min, max, format = numeric) {
  if (typeof min !== "number" || typeof max !== "number") return "—";
  return min === max ? format(min) : `${format(min)}–${format(max)}`;
}

/** The line's own colour, tying its row to its stroke on the overlay. */
function Swatch({ color }) {
  return (
    <span
      aria-hidden="true"
      className="size-3 shrink-0 rounded-full ring-1 ring-black/20"
      style={{ backgroundColor: color }}
    />
  );
}

function Figure({ label, value, hint }) {
  return (
    <div className="space-y-0.5">
      <p className="text-muted-foreground text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
        {label}
      </p>
      <p className="font-mono text-sm font-medium tabular-nums">{value}</p>
      {hint ? <p className="text-muted-foreground text-[0.65rem]">{hint}</p> : null}
    </div>
  );
}

/**
 * Every pair's cosine similarity along one line, as a strip.
 *
 * On the card rather than only in the dropdown, because this is the measurement
 * the whole verdict rests on: a line that "pays 2" is a claim about two numbers,
 * and reading them beside it is the only way to tell a real pair of like symbols
 * from a threshold set too low. Matched pairs are emphasised, and the ones after
 * the run broke are dimmed — they were scored but decided nothing.
 */
function Scores({ steps }) {
  if (steps.length === 0) return null;

  return (
    <p className="flex flex-wrap items-center gap-x-1.5 font-mono text-[0.65rem] tabular-nums">
      {steps.map((step, index) => (
        <span key={`${step.left}-${step.right}`} className="flex items-center gap-1.5">
          {index > 0 ? <span className="text-muted-foreground/40">·</span> : null}
          <span
            className={cn(
              step.matched
                ? "font-medium text-emerald-600 dark:text-emerald-400"
                : "text-muted-foreground",
              !step.counted && "opacity-50",
            )}
            title={`${step.left} ~ ${step.right}${
              step.counted ? "" : " (after the run broke)"
            }`}
          >
            {score(step.similarity)}
          </span>
        </span>
      ))}
    </p>
  );
}

/** The same scores with the tiles they belong to, for the dropdown. */
function StepList({ steps }) {
  if (steps.length === 0) return null;

  return (
    <ul className="space-y-1">
      {steps.map((step) => (
        <li
          key={`${step.left}-${step.right}`}
          className={cn(
            "flex items-center gap-2 font-mono text-xs",
            // Dimmed once the run has broken -- didn't decide anything.
            step.counted ? "" : "text-muted-foreground/70",
          )}
        >
          {step.matched ? (
            <CircleCheck className="size-3.5 shrink-0 text-emerald-500" />
          ) : (
            <CircleSlash className="text-muted-foreground size-3.5 shrink-0" />
          )}
          <span className="w-28 shrink-0">
            {step.left} ~ {step.right}
          </span>
          <span className="tabular-nums">{score(step.similarity)}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * The symbols the game's logged reel stops put on screen.
 *
 * Shown because it is the other half of every award below: the picture found the
 * runs, and this named them. It is also the fastest way to spot a misread — a
 * grid that does not look like the reels in the overlay above means the stop
 * alignment is wrong, and the agreement count says how confident it was.
 */
function SymbolGrid({ paylines }) {
  if (paylines.symbol_grid.length === 0) {
    return paylines.stops_error ? (
      <p className="text-muted-foreground text-xs">{paylines.stops_error}</p>
    ) : null;
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.16em] uppercase">
          Symbols on the reels
        </h3>
        <span className="text-muted-foreground font-mono text-[0.65rem]">
          stops [{paylines.stops.join(", ")}] read as {paylines.stop_anchor}-row
        </span>
        {typeof paylines.stop_compared === "number" ? (
          <span
            className={cn(
              "text-[0.65rem]",
              paylines.stop_anchor_decided
                ? "text-muted-foreground"
                : "text-amber-600 dark:text-amber-500",
            )}
          >
            {paylines.stop_agreed}/{paylines.stop_compared} pairs agree with the picture
            {paylines.stop_anchor_decided ? "" : " — the alignment is a guess"}
          </span>
        ) : null}
      </div>
      <div className="border-border/60 inline-block overflow-hidden rounded-lg border">
        <table className="text-xs">
          <tbody>
            {/* Keyed by index because the index *is* the identity here: this
                is a fixed grid of positions, not a reorderable list. */}
            {paylines.symbol_grid.map((row, rowIndex) => (
              <tr key={rowIndex} className="divide-border/60 divide-x">
                {row.map((code, columnIndex) => (
                  <td
                    key={columnIndex}
                    className="bg-muted/20 px-2.5 py-1.5 text-center font-mono"
                  >
                    {code || "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Every paytable row that could pay this run — what the picture alone narrows to. */
function Candidates({ line }) {
  if (line.candidates.length === 0) return null;

  return (
    <ul className="space-y-1">
      {line.candidates.map((candidate) => (
        <li
          key={candidate.codes.join("-")}
          className="flex items-baseline gap-2 text-xs"
        >
          <span className="font-mono">{candidate.codes.join(", ")}</span>
          <span className="text-muted-foreground truncate">
            {candidate.names.filter(Boolean).join(", ")}
          </span>
          <span className="ml-auto shrink-0 font-mono tabular-nums">
            {numeric(candidate.value)}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** The picture-and-maths disagreement, where there is one. */
function Disagreement({ line }) {
  if (line.agrees !== false) return null;

  return (
    <p className="flex items-start gap-1.5 text-[0.65rem] text-amber-600 dark:text-amber-500">
      <TriangleAlert className="mt-px size-3 shrink-0" />
      the reels show a run of {line.pays} but the logged stops read{" "}
      {line.run_from_stops} — a wild standing in, or a reel drawing the wrong symbol
    </p>
  );
}

/**
 * What this line earns, and on whose authority.
 *
 * Two states of knowledge. With the symbol named by the logged stops, the award
 * is one combo and one number. Without it, the picture alone leaves every row
 * that pays at that length in play, so those are listed rather than one of them
 * being picked. Only rendered for a line that is actually awarded.
 */
function Award({ line }) {
  if (!line.symbol) {
    return (
      <div className="space-y-1">
        <p className="text-muted-foreground text-[0.65rem]">
          The stops did not name the symbol, so any of these could be the award:
        </p>
        <Candidates line={line} />
      </div>
    );
  }

  return (
    <div className="space-y-1">
      {/* The derivation, in the order it happens: this many of this symbol, at
          this value. "Pays" is left for the credits alone. */}
      <p className="text-xs">
        <span className="text-muted-foreground">{line.pays} × </span>
        <span className="font-medium">{line.symbol_name ?? line.symbol}</span>
        <span className="text-muted-foreground"> → </span>
        <span className="font-mono font-medium tabular-nums">
          {numeric(line.credits)}
        </span>
        <span className="text-muted-foreground"> credits</span>
      </p>
      {line.combo_symbols.length > 0 ? (
        <p className="text-muted-foreground font-mono text-[0.65rem]">
          {line.combo_symbols.join(" ")}
          {line.combo_id === null ? "" : ` · combo ${line.combo_id}`}
        </p>
      ) : null}
      <Disagreement line={line} />
      {line.note ? (
        <p className="text-muted-foreground text-[0.65rem]">{line.note}</p>
      ) : null}
    </div>
  );
}

/**
 * One awarded line.
 *
 * Two numbers, and the words for them are not interchangeable: the line
 * **matches** five symbols and **pays** twenty-five credits. Saying "pays 5"
 * for the run length reads as five credits, which is the one misreading this
 * layout exists to prevent — so the match count sits with the label and "pays"
 * is left for the credits alone.
 */
function AwardedLine({ line }) {
  return (
    <div
      className="border-border/60 bg-card space-y-1.5 rounded-lg border-l-4 px-3 py-2 shadow-xs"
      style={{ borderLeftColor: line.color }}
    >
      <div className="flex items-center gap-2">
        <Swatch color={line.color} />
        <p className="text-sm font-semibold whitespace-nowrap">
          {line.label}{" "}
          <span className="text-muted-foreground font-normal">matches {line.pays}</span>
        </p>
        <span className="ml-auto shrink-0 text-sm font-semibold whitespace-nowrap">
          <span className="text-muted-foreground font-normal">pays </span>
          <span className="font-mono tabular-nums">
            {line.exact
              ? numeric(line.value_min)
              : span(line.value_min, line.value_max)}
          </span>
        </span>
      </div>
      <Scores steps={line.steps} />
      <Award line={line} />
    </div>
  );
}

/** One line's dropdown: the verdict on the summary, the evidence inside. */
function LineRow({ line, image }) {
  return (
    <details className="group border-border/60 hover:border-border overflow-hidden rounded-lg border transition-colors">
      <summary className="hover:bg-accent/40 flex cursor-pointer list-none items-center gap-3 px-3 py-2 text-sm [&::-webkit-details-marker]:hidden">
        <ChevronDown className="text-muted-foreground size-4 shrink-0 transition-transform group-open:rotate-180" />
        <Swatch color={line.color} />
        <span className="font-medium">{line.label}</span>
        {line.awarded ? (
          <Badge className="border-emerald-500/30 bg-emerald-500/15 font-mono text-emerald-700 dark:text-emerald-400">
            matches {line.pays} · pays{" "}
            {line.exact
              ? numeric(line.value_min)
              : span(line.value_min, line.value_max)}
          </Badge>
        ) : (
          <Badge variant="outline" className="text-muted-foreground font-mono">
            no win
          </Badge>
        )}
        {line.agrees === false ? (
          <TriangleAlert
            className="size-3.5 shrink-0 text-amber-600 dark:text-amber-500"
            aria-label="the picture and the logged stops disagree"
          />
        ) : null}
        <span className="text-muted-foreground ml-auto hidden truncate font-mono text-[0.65rem] sm:block">
          {line.symbols.length > 0
            ? line.symbols.map((code) => code ?? "—").join(" ")
            : line.positions.join(" → ")}
        </span>
      </summary>

      <div className="bg-muted/20 space-y-3 border-t px-3 py-3">
        {image ? (
          <img
            src={image}
            alt={`${line.label} traced over the reels`}
            className="bg-muted w-full rounded-md border"
          />
        ) : null}
        {line.awarded ? <Award line={line} /> : null}
        {/* The measurement itself, pair by pair. */}
        <StepList steps={line.steps} />
        {/* The tiles the line runs through, and the symbols the stops put on
            them — the two halves side by side. */}
        <p className="text-muted-foreground font-mono text-[0.65rem] break-all">
          {line.positions
            .map((position, index) => {
              const code = line.symbols[index];
              return code ? `${position}=${code}` : position;
            })
            .join("  ")}
        </p>
        {/* Both forms of the line: winGeometry's own, and the tile names it was
            converted into. Carried so the conversion can be checked by eye. */}
        {line.elements.length > 0 ? (
          <p className="text-muted-foreground/70 font-mono text-[0.6rem] break-all">
            winGeometry: {line.elements.map((pair) => `[${pair.join(",")}]`).join(" ")}
          </p>
        ) : null}
        {line.break_position ? (
          <p className="text-muted-foreground text-xs">
            The run stopped at <span className="font-mono">{line.break_position}</span>.
          </p>
        ) : null}
      </div>
    </details>
  );
}

/**
 * The lines the *running* game plays, checked against the reels this spin landed,
 * and priced against its own paytable.
 *
 * Three separate judgements, kept visibly separate:
 *
 * - **What landed** is measured off the picture: cosine similarity between the
 *   split's tiles gives each line's leading run, and every score is on screen,
 *   because a run of two is a claim about two numbers. Nothing about the win
 *   comes out of the log — a check that read the answer there would agree with
 *   the game by construction.
 * - **What it was** comes from the game's logged reel stops, which name the
 *   symbol at every position. That is the one thing similarity cannot say.
 * - **Whether it pays** is the paytable's answer alone. A run of two of a symbol
 *   that pays from three is a real run and no win, so it is not shown as a
 *   result at all — only its scores, in the full line list. `awarded`,
 *   `runs_found` and each line's `note` still carry the reasoning for anyone
 *   reading the record.
 *
 * Two numbers, two words, never swapped: a line **matches** five symbols and
 * **pays** twenty-five credits.
 */
export function PaylineValidationCard({ paylines, detailed }) {
  const expected = paylines.expected;
  const images = new Map(
    (detailed?.lines ?? []).map((line) => [line.line, line.image_data]),
  );
  const overlay = detailed?.overlay_image ?? null;
  const awarded = paylines.lines.filter((line) => line.awarded);
  const disagreeing = paylines.lines.filter((line) => line.agrees === false);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Route className="size-4" />
          Paylines
        </CardTitle>
        <CardDescription>
          Runs measured in the picture, symbols named by the logged stops, awards
          decided by the paytable
        </CardDescription>
        {expected ? (
          <CardAction>
            <VerdictBadge verdict={expected.verdict} />
          </CardAction>
        ) : null}
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <Badge variant="outline" className="font-mono text-[0.65rem] break-all">
            {paylines.paytable_id || "no paytable"}
          </Badge>
          {paylines.payline_set_id ? (
            <Badge variant="secondary" className="font-mono text-[0.65rem]">
              {paylines.payline_set_id}-line set
            </Badge>
          ) : null}
          <span className="text-muted-foreground text-[0.65rem]">
            set chosen by {paylines.resolved_from}, paytable from{" "}
            {paylines.paytable_origin || "nowhere"}
          </span>
        </div>

        {paylines.error ? (
          <p className="text-destructive text-sm break-words">{paylines.error}</p>
        ) : (
          <>
            {/* No summary sentence and no run tally: the Awarded block below
                says the same thing with its working shown, and a run the
                paytable does not pay is not a result to report. Both are still
                on the payload (`summary`, `runs_found`) for the timeline and for
                anyone reading the record. */}

            {overlay ? (
              <img
                src={overlay}
                alt="The awarded lines drawn over the reels of this spin"
                className="bg-muted w-full rounded-md border"
              />
            ) : null}

            <SymbolGrid paylines={paylines} />

            {awarded.length > 0 ? (
              <div className="space-y-2">
                <h3 className="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.16em] uppercase">
                  Awarded
                </h3>
                <div className="grid gap-2 sm:grid-cols-2">
                  {awarded.map((line) => (
                    <AwardedLine key={line.line} line={line} />
                  ))}
                </div>
              </div>
            ) : (
              <div className="border-border/60 bg-muted/30 text-muted-foreground rounded-lg border px-3 py-2 text-sm">
                No line pays
              </div>
            )}

            {/* Gathered once as well as marked per line: a spin where the reels
                and the maths disagree anywhere is worth noticing without
                opening forty dropdowns. */}
            {disagreeing.length > 0 ? (
              <p className="flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-500">
                <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
                {disagreeing.length} line{disagreeing.length === 1 ? "" : "s"} where the
                picture and the logged stops read different runs:{" "}
                {disagreeing.map((line) => line.label).join(", ")}
              </p>
            ) : null}

            {/* Every line, paying or not, behind one dropdown: forty rows is a
                reference to open, not something to scroll past on the way to
                the two validations. */}
            <details className="group border-t pt-4">
              <summary className="text-muted-foreground hover:text-foreground flex cursor-pointer list-none items-center gap-1.5 text-xs select-none">
                <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
                All {paylines.lines.length} lines and their scores, at threshold{" "}
                {paylines.threshold.toFixed(4)}
              </summary>
              <div className="mt-3 space-y-1.5">
                {paylines.lines.map((line) => (
                  <LineRow
                    key={line.line}
                    line={line}
                    image={images.get(line.line) ?? null}
                  />
                ))}
              </div>
            </details>

            {paylines.split ? (
              <p className="text-muted-foreground/70 font-mono text-[0.6rem] break-all">
                split {paylines.split} · frame {paylines.frame}
              </p>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
