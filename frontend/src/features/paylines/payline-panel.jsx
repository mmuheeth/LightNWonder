import {
  ChevronDown,
  CircleCheck,
  CircleSlash,
  RefreshCw,
  Route,
  ScanSearch,
} from "lucide-react";
import { useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { StatRow } from "@/components/stat-row";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useCheckPaylines, usePaylineLayout } from "@/features/paylines/use-paylines";
import { cn } from "@/lib/utils";

/** A similarity score at the precision the clusters are actually apart by. */
function score(value) {
  return typeof value === "number" ? value.toFixed(4) : "—";
}

/**
 * One labelled number in the statistics block.
 *
 * Its own component rather than a `StatRow`, because these read as a grid of
 * figures to scan across rather than as a list of a card's properties.
 */
function Figure({ label, value, hint }) {
  return (
    <div className="space-y-0.5">
      <p className="text-muted-foreground text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
        {label}
      </p>
      <p className="font-mono text-sm font-medium">{value}</p>
      {hint ? <p className="text-muted-foreground text-[0.65rem]">{hint}</p> : null}
    </div>
  );
}

/** The line's own colour, as the swatch the overlay's stroke is matched to. */
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
 * One paying line, as a box rather than a clause in a sentence.
 *
 * `result.summary` reads fine as a log line but not as something to scan --
 * "Line 1 pays 2, Line 5 pays 5" is one run-on clause however many lines paid.
 * A box per line is a box per fact: the colour ties it to the same line's
 * stroke on the overlay below and its row in the dropdown further down.
 */
function PaylineResultCard({ line }) {
  return (
    <div
      className="border-border/60 bg-card flex items-center gap-2.5 rounded-lg border-l-4 py-2 pr-4 pl-3 shadow-xs"
      style={{ borderLeftColor: line.color }}
    >
      <Swatch color={line.color} />
      <p className="text-sm font-semibold whitespace-nowrap">
        {line.label}{" "}
        <span className="text-muted-foreground font-normal">pays {line.pays}</span>
      </p>
    </div>
  );
}

/**
 * One line's own dropdown: the verdict on the summary, the evidence inside.
 *
 * A native `<details>` rather than a vendored accordion, because that is exactly
 * this behaviour with no dependency and it keeps keyboard and screen-reader
 * support without any of it being written here. Closed by default and
 * independently openable: forty lines of four comparisons each is not something
 * to read all at once, and the whole point of the dropdown is to look at one
 * line when its number surprises you.
 */
function PaylineRow({ line }) {
  return (
    <details className="group border-border/60 hover:border-border overflow-hidden rounded-lg border transition-colors">
      <summary className="hover:bg-accent/40 flex cursor-pointer list-none items-center gap-3 px-3 py-2.5 text-sm [&::-webkit-details-marker]:hidden">
        <ChevronDown className="text-muted-foreground size-4 shrink-0 transition-transform group-open:rotate-180" />
        <Swatch color={line.color} />
        <span className="font-medium">{line.label}</span>
        {line.paying ? (
          <Badge className="border-emerald-500/30 bg-emerald-500/15 font-mono text-emerald-700 dark:text-emerald-400">
            pays {line.pays}
          </Badge>
        ) : (
          <Badge variant="outline" className="text-muted-foreground font-mono">
            no win
          </Badge>
        )}
        <span className="text-muted-foreground ml-auto hidden truncate font-mono text-[0.65rem] sm:block">
          {line.positions.join(" → ")}
        </span>
      </summary>

      <div className="bg-muted/20 space-y-3 border-t px-3 py-3">
        {/* The picture carries the tile-by-tile verdict now -- green borders on
            the confirmed run, red on the tile that broke it -- so there is no
            separate row of position chips repeating the same thing in text. */}
        {line.image_data ? (
          <img
            src={line.image_data}
            alt={`${line.label} traced over the reels${
              line.break_position ? `, breaking at ${line.break_position}` : ""
            }`}
            className="bg-muted w-full rounded-md border"
          />
        ) : null}

        <ul className="space-y-1">
          {line.steps.map((step) => (
            <li
              key={`${step.left}-${step.right}`}
              className={cn(
                "flex items-center gap-2 font-mono text-xs",
                /* Dimmed once the run has broken: these comparisons happened
                   and are worth seeing, but they did not decide anything. */
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
      </div>
    </details>
  );
}

/**
 * Check which paylines of the active game pay on the latest split reel grid.
 *
 * One step past the Reel grid panel, and it reads that panel's output: the tiles
 * under `obs-captured-files/grid/<frame>/`, not a screenshot. So the loop is
 * three clicks in a row — Screenshot in the OBS panel, Split in the Reel grid
 * panel, Check here — and a re-check at a different threshold does not need the
 * simulator to still be showing the same spin.
 *
 * A line is read from the left one adjacent pair at a time and stops at the
 * first pair that is not the same symbol, so `pays` is the length of the leading
 * run: three matching reels behind a break pay nothing.
 *
 * **The threshold is the control that matters, and it is not intuitive.** Cosine
 * similarity of pixel channels does not start at zero for unrelated pictures —
 * two different symbols on one reel background score 0.6 to 0.9 and two crops of
 * the same symbol score above 0.96 — so a cut that sounds generous calls
 * everything a match. `stats.matched_min` and `stats.rejected_max` on a result
 * are the two numbers a working cut sits between, which is what tuning
 * `PAYLINE_MATCH_THRESHOLD` against a frame that does not move is for.
 *
 * Full width on the dashboard because the annotated reels are a wide picture and
 * the per-line evidence is a long list, and neither survives half a row.
 */
export function PaylinePanel() {
  const { data, error, isPending, isFetching, refetch } = usePaylineLayout();
  const check = useCheckPaylines();

  // Blank means "whatever the setting says", which is the case that needs no
  // typing. Held as the raw string so a half-typed "0." is not snapped to 0.
  const [set, setSet] = useState("");
  const [threshold, setThreshold] = useState("");

  const sets = data?.sets ?? [];
  const split = data?.latest_split ?? null;
  const layoutError = data?.error ?? null;
  const selectedSet = set || data?.default_set || sets[0]?.name || "";

  const trimmed = threshold.trim();
  const override = trimmed === "" ? null : Number(trimmed);
  const thresholdInvalid =
    override !== null &&
    !(Number.isFinite(override) && override >= -1 && override <= 1);
  const canCheck = Boolean(split) && Boolean(selectedSet) && !thresholdInvalid;

  const result = check.data ?? null;
  const stats = result?.stats ?? null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Route className="size-4" />
          Payline check
        </CardTitle>
        <CardDescription>
          Matching the tiles of the latest split against the patterns that pay
        </CardDescription>
        <CardAction>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => refetch()}
            disabled={isFetching}
            aria-label="Refresh payline layout"
          >
            <RefreshCw className={cn("size-4", isFetching && "animate-spin")} />
          </Button>
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-4">
        {isPending ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : error ? (
          <ApiErrorAlert error={error} onRetry={() => refetch()} />
        ) : (
          <>
            <StatRow label="Game">{data.game}</StatRow>

            <div className="space-y-2 border-t pt-4">
              <div className="flex flex-wrap items-end gap-3">
                <div className="space-y-1">
                  <label
                    htmlFor="payline-set"
                    className="text-muted-foreground block text-[0.65rem] font-semibold tracking-[0.16em] uppercase"
                  >
                    Line set
                  </label>
                  <div className="relative">
                    <select
                      id="payline-set"
                      aria-label="Line set"
                      value={selectedSet}
                      onChange={(event) => setSet(event.target.value)}
                      disabled={sets.length === 0}
                      className="border-input bg-background text-foreground hover:bg-accent/50 focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] h-9 w-36 cursor-pointer appearance-none rounded-md border px-3 pr-9 font-mono text-sm shadow-xs transition-[color,box-shadow,background-color] outline-none disabled:cursor-not-allowed disabled:opacity-60 dark:bg-input/30"
                    >
                      {sets.length === 0 ? (
                        <option value="">No sets configured</option>
                      ) : (
                        sets.map((option) => (
                          <option
                            key={option.name}
                            value={option.name}
                            className="bg-background text-foreground"
                          >
                            {option.label}
                          </option>
                        ))
                      )}
                    </select>
                    <span className="text-muted-foreground pointer-events-none absolute inset-y-0 right-3 flex items-center">
                      <ChevronDown className="size-4" />
                    </span>
                  </div>
                </div>

                <div className="space-y-1">
                  <label
                    htmlFor="payline-threshold"
                    className="text-muted-foreground block text-[0.65rem] font-semibold tracking-[0.16em] uppercase"
                  >
                    Match threshold
                  </label>
                  <Input
                    id="payline-threshold"
                    aria-label="Match threshold"
                    inputMode="decimal"
                    placeholder={String(data.threshold)}
                    value={threshold}
                    onChange={(event) => setThreshold(event.target.value)}
                    className="h-9 w-32 font-mono text-sm"
                  />
                </div>

                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    check.mutate({
                      set: selectedSet,
                      ...(override === null ? {} : { threshold: override }),
                    })
                  }
                  disabled={!canCheck || check.isPending}
                >
                  <ScanSearch />
                  Check
                </Button>
              </div>

              {thresholdInvalid ? (
                <p className="text-destructive text-xs">
                  The threshold must be a number from -1 to 1.
                </p>
              ) : null}
              {!split && !layoutError ? (
                <p className="text-muted-foreground text-xs">
                  No split yet — split one from the Reel grid panel first.
                </p>
              ) : null}
              {layoutError ? (
                <p className="text-destructive text-xs">{layoutError}</p>
              ) : null}
            </div>

            {check.error ? <ApiErrorAlert error={check.error} /> : null}

            {result ? (
              <div className="space-y-4 border-t pt-4">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <Badge variant="outline" className="font-mono text-[0.65rem]">
                    {result.set} lines · threshold {score(result.threshold)}
                  </Badge>
                  <span className="text-muted-foreground font-mono text-[0.65rem] break-all">
                    {result.source.split}
                  </span>
                </div>

                {/* One box per paying line, rather than the comma-separated
                    sentence that same information reads as in `result.summary`:
                    a box is a fact to scan, a clause is a sentence to parse. */}
                <div className="flex flex-wrap gap-2">
                  {result.lines.some((line) => line.paying) ? (
                    result.lines
                      .filter((line) => line.paying)
                      .map((line) => <PaylineResultCard key={line.name} line={line} />)
                  ) : (
                    <div className="border-border/60 bg-muted/30 text-muted-foreground rounded-lg border px-3 py-2 text-sm">
                      No line pays
                    </div>
                  )}
                </div>

                {result.overlay_image ? (
                  <img
                    src={result.overlay_image}
                    alt={`Paying lines of the ${result.set}-line set drawn over the reels`}
                    className="bg-muted w-full rounded-md border"
                  />
                ) : null}

                <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
                  <div className="space-y-2">
                    <h3 className="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.16em] uppercase">
                      Per-line validation
                    </h3>
                    <div className="space-y-1.5">
                      {result.lines.map((line) => (
                        <PaylineRow key={line.name} line={line} />
                      ))}
                    </div>
                  </div>

                  <div className="space-y-3">
                    <h3 className="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.16em] uppercase">
                      Statistics
                    </h3>
                    <div className="border-border/60 bg-muted/20 grid grid-cols-2 gap-x-3 gap-y-4 rounded-lg border p-3">
                      <Figure label="Lines" value={stats.lines} />
                      <Figure label="Paying" value={stats.paying} />
                      <Figure
                        label="Pairs"
                        value={stats.comparisons}
                        hint="distinct comparisons"
                      />
                      <Figure label="Matched" value={stats.matches} />
                      <Figure
                        label="Best line"
                        value={stats.best_line ?? "—"}
                        hint={
                          stats.best_line ? `pays ${stats.best_pays}` : "nothing paid"
                        }
                      />
                      <Figure
                        label="Score range"
                        value={`${score(stats.score_min)}–${score(stats.score_max)}`}
                      />
                      {/* The pair that says whether the threshold is doing its
                          job: a cut between these two is separating the symbols,
                          one outside it is not. */}
                      <Figure
                        label="Lowest match"
                        value={score(stats.matched_min)}
                        hint="counted as the same"
                      />
                      <Figure
                        label="Highest reject"
                        value={score(stats.rejected_max)}
                        hint="counted as different"
                      />
                    </div>
                  </div>
                </div>
              </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
