import { Coins } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/** One labelled figure in the bet/denomination definition list. */
function Stat({ label, value }) {
  return (
    <div className="space-y-0.5">
      <p className="text-muted-foreground text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
        {label}
      </p>
      <p className="font-mono text-sm font-medium tabular-nums">{value ?? "—"}</p>
    </div>
  );
}

/**
 * The bet and denomination in play right now, read the way the Game Config tab
 * reads them -- the log's last bet change and paytable-loaded lines. Shown beside
 * the scatter checks because a value table is priced per bet rung: this is what
 * each check was judged against.
 */
export function BetInfoCard({ betInfo, error }) {
  if (!betInfo) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Coins className="size-4" />
            Live bet &amp; denomination
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">
            {error ?? "No bet or denomination info could be read."}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Coins className="size-4" />
          Live bet &amp; denomination
        </CardTitle>
        <CardDescription>
          Read from the game&apos;s log, the same source the Game Config tab uses --
          this is what every scatter check below is judged against
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat
            label="Bet per unit"
            value={betInfo.current_bet != null ? betInfo.current_bet : null}
          />
          <Stat
            label="Unit cost"
            value={betInfo.unit_cost != null ? `${betInfo.unit_cost} cr` : null}
          />
          <Stat
            label="Denomination"
            value={betInfo.denomination_label ?? null}
          />
          <Stat
            label="Money / credit"
            value={
              betInfo.money_per_credit != null
                ? betInfo.money_per_credit.toFixed(2)
                : null
            }
          />
        </div>

        {betInfo.ladder.length ? (
          <div className="space-y-1">
            <p className="text-muted-foreground text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
              Bet ladder
            </p>
            <div className="flex flex-wrap gap-1.5">
              {betInfo.ladder.map((rung) => (
                <Badge
                  key={rung}
                  variant={rung === betInfo.current_bet ? "default" : "outline"}
                  className="font-mono text-[0.65rem]"
                >
                  {rung}
                </Badge>
              ))}
            </div>
          </div>
        ) : null}

        <div className="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 border-t pt-3 text-[0.65rem]">
          <span className="font-mono break-all">{betInfo.paytable_id}</span>
          <Badge variant="secondary" className="font-mono text-[0.6rem]">
            {betInfo.source_origin}
          </Badge>
          {betInfo.current_bet_source ? (
            <span>bet from {betInfo.current_bet_source}</span>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
