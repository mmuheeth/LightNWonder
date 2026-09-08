import { ChevronDown, ChevronRight, FileCode2, RefreshCw } from "lucide-react";

import { StatRow } from "@/components/stat-row";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** Local time, so a line written a minute ago reads as one written a minute ago. */
function formatLoggedAt(value) {
  if (!value) return null;
  const at = new Date(value);
  return Number.isNaN(at.getTime()) ? null : at.toLocaleString();
}

function percent(value) {
  return typeof value === "number" ? `${value.toFixed(2)}%` : "—";
}

/** Which paytable the running game has loaded. */
export function PaytableSummary({ data, selected, onSelect, onRefresh, isFetching }) {
  const { source, identity, math, denomination } = data;
  const loggedAt = formatLoggedAt(source.logged_at);
  const supported = source.supported_denominations ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileCode2 className="size-4" />
          Current paytable
        </CardTitle>
        {/* Sits on this card rather than the page header because this card is
            what it changes: it re-reads the game's log, and what may have moved
            is *which* paytable is loaded. */}
        <CardAction>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onRefresh?.()}
            disabled={isFetching}
            aria-label="Refresh from the game log"
            title="Re-read the game log for the paytable loaded now"
          >
            <RefreshCw className={cn("size-4", isFetching && "animate-spin")} />
          </Button>
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-4">
        <p className="font-mono text-xl font-semibold tracking-tight break-all">
          {data.paytable_id}
        </p>

        {identity?.display_game_id ? (
          <p className="text-muted-foreground text-sm">{identity.display_game_id}</p>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2">
          <StatRow label="Return">
            {percent(identity?.game_pct ?? math.game_pct)}
          </StatRow>
          <StatRow label="Base game return">
            {percent(identity?.game_base_pct ?? math.game_base_pct)}
          </StatRow>
          <StatRow label="Lines">{identity?.number_of_lines ?? "—"}</StatRow>
          {/* The denomination is what selects the paytable on this cabinet, so
              the one in play and the ones it could move to belong together.
              Shown interpreted rather than raw: the log writes `1.000` for a 1c
              cabinet — a count of cents — and read as money that is a thousand
              times the truth. The rate beside it is the number everything
              downstream actually multiplies by. */}
          <StatRow label="Current denom">
            {denomination ? (
              <span className="font-mono">
                {denomination.label}
                {denomination.money_per_credit !== null ? (
                  <span className="text-muted-foreground ml-2 text-xs">
                    {denomination.money_per_credit} per credit
                  </span>
                ) : (
                  <span
                    className="text-destructive ml-2 text-xs"
                    title={`Paytable ${data.paytable_id} names no unit for its denomination, so credits cannot be priced in money. The id is expected to end in an amount and a unit letter, as in '-2c-'.`}
                  >
                    unit unresolved
                  </span>
                )}
              </span>
            ) : (
              (source.denomination ?? <span className="text-muted-foreground">—</span>)
            )}
          </StatRow>
          {denomination?.agrees === false ? (
            <StatRow label="">
              <span
                className="text-destructive text-xs"
                title="The value comes from the log and the multiplier from the paytable folder's own gameConfig.cfg. The logged one wins, because it is the current one — but the two disagreeing means a reading somewhere is stale."
              >
                Log says {denomination.value}, paytable declares{" "}
                {denomination.declared_multiplier}
              </span>
            </StatRow>
          ) : null}
          <StatRow label="Min total bet">{identity?.min_total_bet ?? "—"}</StatRow>
          <StatRow label="Max bets">
            {identity?.max_bets?.length ? identity.max_bets.join(", ") : "—"}
          </StatRow>
        </div>

        <StatRow label="Supported denoms">
          {supported.length ? (
            <span className="font-mono text-xs">{supported.join(", ")}</span>
          ) : (
            <span className="text-muted-foreground">—</span>
          )}
        </StatRow>

        <div className="space-y-2 border-t pt-4">
          <label
            htmlFor="paytable-id"
            className="text-muted-foreground block text-[0.65rem] font-semibold tracking-[0.16em] uppercase"
          >
            Inspect another paytable
          </label>
          <div className="relative">
            <select
              id="paytable-id"
              aria-label="Paytable"
              value={selected}
              onChange={(event) => onSelect(event.target.value)}
              className="border-input bg-background text-foreground hover:bg-accent/50 focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] h-9 w-full min-w-0 cursor-pointer appearance-none rounded-md border px-3 pr-9 font-mono text-sm shadow-xs transition-[color,box-shadow,background-color] outline-none dark:bg-input/30"
            >
              <option value="" className="bg-background text-foreground">
                Whatever the log says
              </option>
              {data.available.map((option) => (
                <option
                  key={option}
                  value={option}
                  className="bg-background text-foreground"
                >
                  {option}
                </option>
              ))}
            </select>
            <span className="text-muted-foreground pointer-events-none absolute inset-y-0 right-3 flex items-center">
              <ChevronDown className="size-4" />
            </span>
          </div>
        </div>

        <details className="group border-t pt-4">
          <summary className="text-muted-foreground hover:text-foreground flex cursor-pointer list-none items-center gap-1.5 text-xs select-none">
            <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
            Where this came from
          </summary>
          <div className="mt-3 space-y-3">
            {source.log_line ? (
              <div className="bg-muted/40 space-y-1 rounded-md border p-3">
                <p className="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.16em] uppercase">
                  {loggedAt ? `Logged ${loggedAt}` : "From the log"}
                </p>
                <p className="font-mono text-xs break-all">{source.log_line}</p>
              </div>
            ) : (
              <p className="text-muted-foreground text-xs">
                No log line — this paytable was picked by hand.
              </p>
            )}
            <StatRow label="Maths">
              <span className="font-mono text-[0.7rem] break-all">
                {data.directory}
              </span>
            </StatRow>
            {source.log_path ? (
              <StatRow label="Log">
                <span className="font-mono text-[0.7rem] break-all">
                  {source.log_path}
                </span>
              </StatRow>
            ) : null}
          </div>
        </details>
      </CardContent>
    </Card>
  );
}
