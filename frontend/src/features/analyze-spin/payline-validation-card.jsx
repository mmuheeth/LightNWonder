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
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

function numeric(value) {
  if (typeof value !== "number") return "—";
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)));
}

/** A probability as a percentage, at the precision the classes separate by. */
function percent(value) {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
}

/** One symbol code, or the dash a tile nothing could name is drawn as. */
function code(value) {
  return value ?? "—";
}

/**
 * One code in the strip, the wild marked.
 *
 * Worth its own colour because a wild is the one tile whose code does not say
 * what it counted as: `AA AA WC AA AA` is five Ox, and a reader checking a run
 * of five against three visible Ox codes needs to see which tile stood in.
 */
function Code({ value, wild }) {
  const substituted = wild !== null && value === wild;
  return (
    <span
      className={cn(substituted && "text-amber-600 dark:text-amber-400")}
      title={substituted ? "the wild, read as whatever the run pays as" : undefined}
    >
      {code(value)}
    </span>
  );
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

/**
 * The codes along one line, with the joins that decided the run.
 *
 * On the card rather than only in the dropdown, because this is the measurement
 * the whole verdict rests on: a line that matches two is a claim about two codes,
 * and reading them beside it is the only way to tell a real pair from a
 * misclassified tile. `=` is a counted match, `≠` is where the run broke, and the
 * joins after the break are dimmed — they were compared and decided nothing.
 *
 * This replaced a strip of cosine similarity scores. There is deliberately no
 * number here in their place: two codes are equal or they are not, and printing
 * a 1.00 for "equal" would read as a measurement that was never taken. The
 * per-tile confidence lives on the reel-reading card, where the floor it is
 * judged against is also shown.
 */
function Codes({ line, wild }) {
  if (line.steps.length === 0) return null;

  return (
    <p className="flex flex-wrap items-center gap-x-1.5 font-mono text-[0.65rem]">
      <span className={cn(line.pays > 0 && "font-medium")}>
        <Code value={line.steps[0].left_symbol} wild={wild} />
      </span>
      {line.steps.map((step) => (
        <span
          key={`${step.left}-${step.right}`}
          className={cn("flex items-center gap-1.5", !step.counted && "opacity-50")}
        >
          <span
            className={cn(
              step.matched
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-muted-foreground",
            )}
            title={`${step.left} vs ${step.right}${
              step.counted ? "" : " (after the run broke)"
            }${step.line_symbol ? ` — the run was paying as ${step.line_symbol}` : ""}`}
          >
            {step.matched ? "=" : "≠"}
          </span>
          <span
            className={cn(step.matched && step.counted && "font-medium")}
            title={step.right}
          >
            <Code value={step.right_symbol} wild={wild} />
          </span>
        </span>
      ))}
    </p>
  );
}

/**
 * The same joins with the tiles they belong to, for the dropdown.
 *
 * A counted join is judged against what the *run* is paying as, not against the
 * tile to its left, and a wild is where the two come apart: `AA WC BB` breaks at
 * the second join even though a wild sits happily beside a Pisces. Both codes
 * still show, since they are what was read — the run's own symbol is appended
 * where it differs, because without it a break beside a wild reads as a bug.
 */
function StepList({ steps, wild }) {
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
            {step.left} · {step.right}
          </span>
          <span>
            <Code value={step.left_symbol} wild={wild} /> {step.matched ? "=" : "≠"}{" "}
            <Code value={step.right_symbol} wild={wild} />
          </span>
          {step.line_symbol && step.line_symbol !== step.left_symbol ? (
            <span className="text-muted-foreground/80">as {step.line_symbol}</span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/**
 * What this line earns, and on whose authority.
 *
 * One derivation now, not two states of knowledge: the classifier named the
 * symbol on every tile, so a run resolves to one paytable row and one number.
 * Only rendered for a line that is actually awarded — a cancelled run gets its
 * `note` instead, which is the more useful sentence.
 *
 * The length here is `combo_pays`, not `pays`: a run that leads with wilds is two
 * combos and the paytable pays the better of them, so four wilds then an Ox is
 * priced as four wilds even though the run covers five. The two differ only
 * there, and the `note` says so when they do.
 */
function Award({ line }) {
  return (
    <div className="space-y-1">
      {/* The derivation, in the order it happens: this many of this symbol, at
          this rate, staked this many times. The paytable's own figure is kept
          beside the award rather than replaced by it — it is the number the
          Game Config page shows, and the two are equal only at one credit a
          unit. "Pays" is left for the credits alone. */}
      <p className="text-xs">
        <span className="text-muted-foreground">{line.combo_pays ?? line.pays} × </span>
        <span className="font-medium">{line.symbol_name ?? line.symbol}</span>
        <span className="text-muted-foreground"> → </span>
        <span className="font-mono font-medium tabular-nums">
          {numeric(line.credits ?? line.combo_value)}
        </span>
        <span className="text-muted-foreground">
          {line.credits === null ? " a bet unit" : " credits"}
        </span>
        {line.credits !== null && line.combo_value !== line.credits ? (
          <span className="text-muted-foreground">
            {" "}
            ({numeric(line.combo_value)} a bet unit)
          </span>
        ) : null}
      </p>
      {line.combo_symbols.length > 0 ? (
        <p className="text-muted-foreground font-mono text-[0.65rem]">
          {line.combo_symbols.join(" ")}
          {line.combo_id === null ? "" : ` · combo ${line.combo_id}`}
        </p>
      ) : null}
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
function AwardedLine({ line, wild }) {
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
            {numeric(line.credits ?? line.combo_value)}
          </span>
          {/* An awarded line with no credits is a run nobody said the stake for.
              It says the rate rather than printing it as though it were the
              award — the same mistake as reading a rate for a total. */}
          {line.credits === null ? (
            <span className="text-muted-foreground font-normal"> a bet unit</span>
          ) : null}
        </span>
      </div>
      <Codes line={line} wild={wild} />
      <Award line={line} />
    </div>
  );
}

/** One line's dropdown: the verdict on the summary, the evidence inside. */
function LineRow({ line, image, wild }) {
  return (
    <details className="group border-border/60 hover:border-border overflow-hidden rounded-lg border transition-colors">
      <summary className="hover:bg-accent/40 flex cursor-pointer list-none items-center gap-3 px-3 py-2 text-sm [&::-webkit-details-marker]:hidden">
        <ChevronDown className="text-muted-foreground size-4 shrink-0 transition-transform group-open:rotate-180" />
        <Swatch color={line.color} />
        <span className="font-medium">{line.label}</span>
        {line.awarded ? (
          <Badge className="border-emerald-500/30 bg-emerald-500/15 font-mono text-emerald-700 dark:text-emerald-400">
            matches {line.pays} · pays {numeric(line.credits ?? line.combo_value)}
            {line.credits === null ? " a bet unit" : ""}
          </Badge>
        ) : (
          <Badge variant="outline" className="text-muted-foreground font-mono">
            no win
          </Badge>
        )}
        {/* A run the reels landed and the maths does not pay: worth flagging on
            the closed row, because it is the case that looks like a bug. */}
        {line.paying && !line.awarded ? (
          <TriangleAlert
            className="size-3.5 shrink-0 text-amber-600 dark:text-amber-500"
            aria-label="a run the paytable does not pay"
          />
        ) : null}
        <span className="text-muted-foreground ml-auto hidden truncate font-mono text-[0.65rem] sm:block">
          {line.symbols.length > 0
            ? line.symbols.map(code).join(" ")
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
        {/* The comparison itself, pair by pair. */}
        <StepList steps={line.steps} wild={wild} />
        {/* The tiles the line runs through and the code read off each -- the
            position and its answer side by side. */}
        <p className="text-muted-foreground font-mono text-[0.65rem] break-all">
          {line.positions
            .map((position, index) => {
              const symbol = line.symbols[index];
              return symbol ? `${position}=${symbol}` : position;
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
        {!line.awarded && line.note ? (
          <p className="text-muted-foreground text-xs">{line.note}</p>
        ) : null}
      </div>
    </details>
  );
}

/**
 * The lines the *running* game plays, checked against the reels this spin landed,
 * and priced against its own paytable.
 *
 * Two judgements, kept visibly separate:
 *
 * - **What landed** is read off the picture by the image classifier: each tile is
 *   named with a symbol code, and a line's run is the leading stretch of equal
 *   codes. Those codes are on screen, because a line that matches two is a claim
 *   about two codes. Nothing about the win comes out of the log — a check that
 *   read the answer there would agree with the game by construction.
 * - **Whether it pays** is the paytable's answer alone. A run of two of a symbol
 *   that pays from three is a real run and no win, so it is not shown as a result
 *   at all — only its codes, in the full line list, flagged there. `awarded`,
 *   `runs_found` and each line's `note` carry the reasoning for anyone reading
 *   the record.
 *
 * What used to be a third judgement is gone. The run was measured by cosine
 * similarity, which cannot name a symbol, so the award stayed a range of every
 * paytable row paying at that length until the game's *logged reel stops* narrowed
 * it — from a source that agreed with the game by construction. One reading of one
 * picture now answers both halves, and the ranges went with it.
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
  const cancelled = paylines.lines.filter((line) => line.paying && !line.awarded);
  const unnamed = paylines.unnamed_positions ?? [];
  // Null on a game whose config declares no `wild_card_replacement`, which is
  // when nothing was substituted and there is nothing to mark.
  const wild = paylines.wild_symbol ?? null;
  const substituted = paylines.lines.filter((line) => line.leading_wilds > 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Route className="size-4" />
          Paylines
        </CardTitle>
        <CardDescription>
          Runs read off the picture by the classifier, awards decided by the paytable
        </CardDescription>
        {/* No verdict badge: this card's subject is the lines and what they earn
            in credits. Whether that matches the WIN cell is a comparison against
            a second, independently measured number, and it has its own card and
            its own badge below — claiming the same verdict in two places is how
            a reader ends up unsure which number it was about. */}
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

            {awarded.length > 0 ? (
              <div className="space-y-2">
                <h3 className="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.16em] uppercase">
                  Awarded
                </h3>
                <div className="grid gap-2 sm:grid-cols-2">
                  {awarded.map((line) => (
                    <AwardedLine key={line.line} line={line} wild={wild} />
                  ))}
                </div>
                {/* The sum, taken off the response rather than added up here, so
                    the figure on this card and the one the verdict was reached
                    with cannot be two different numbers. What it converts to in
                    money, and whether the meter agreed, is the card below. */}
                {expected ? (
                  <div className="border-border/60 bg-muted/30 flex items-baseline gap-2 rounded-lg border px-3 py-2">
                    <span className="text-sm font-semibold">Total credits</span>
                    <span className="text-muted-foreground text-xs">
                      over {expected.paying_lines} paying line
                      {expected.paying_lines === 1 ? "" : "s"}
                    </span>
                    <span className="ml-auto font-mono text-base font-semibold tabular-nums">
                      {numeric(expected.credits)}
                    </span>
                  </div>
                ) : null}
              </div>
            ) : (
              <div className="border-border/60 bg-muted/30 text-muted-foreground rounded-lg border px-3 py-2 text-sm">
                No line pays
              </div>
            )}

            {/* Gathered once as well as flagged per line: a run the reels landed
                and the maths does not pay is the case that looks like a bug, and
                naming the lines is cheaper than opening forty dropdowns. */}
            {cancelled.length > 0 ? (
              <p className="flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-500">
                <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
                {cancelled.length} run{cancelled.length === 1 ? "" : "s"} the paytable
                does not pay: {cancelled.map((line) => line.label).join(", ")}
              </p>
            ) : null}

            {/* The first thing to check when a spin that plainly paid reports no
                run: the confidence floor is above where the classes separate, so
                a tile the model was only fairly sure of comes back unnamed and
                every line through it stops there. */}
            {unnamed.length > 0 ? (
              <p className="text-muted-foreground text-xs">
                {unnamed.length} tile{unnamed.length === 1 ? "" : "s"} came back unnamed
                at the {percent(paylines.min_confidence)} floor (
                <span className="font-mono">{unnamed.join(", ")}</span>), so every line
                through one stops there.
              </p>
            ) : null}

            {/* The counterpart to the unnamed-tile note: a run *longer* than the
                codes read off it, because a wild counted as something else. Named
                once here for the same reason cancelled runs are — a five-long run
                over four visible Ox codes is the other thing that looks like a
                bug. */}
            {wild !== null && substituted.length > 0 ? (
              <p className="text-xs text-amber-600 dark:text-amber-500">
                <span className="font-mono">{wild}</span> stood in on{" "}
                {substituted.length} line{substituted.length === 1 ? "" : "s"} (
                {substituted.map((line) => line.label).join(", ")}), each read as
                whatever its run pays as.
              </p>
            ) : null}

            {/* Every line, paying or not, behind one dropdown: forty rows is a
                reference to open, not something to scroll past on the way to
                the two validations. */}
            <details className="group border-t pt-4">
              <summary className="text-muted-foreground hover:text-foreground flex cursor-pointer list-none items-center gap-1.5 text-xs select-none">
                <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
                All {paylines.lines.length} lines and the codes they were read from, at
                confidence floor {percent(paylines.min_confidence)}
              </summary>
              <div className="mt-3 space-y-1.5">
                {paylines.lines.map((line) => (
                  <LineRow
                    key={line.line}
                    line={line}
                    image={images.get(line.line) ?? null}
                    wild={wild}
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
