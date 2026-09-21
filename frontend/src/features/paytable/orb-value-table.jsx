import { Gem } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const KIND_LABEL = { SC: "SC (scatter orb)", NonSC: "Other orbs" };

function formatProbability(probability) {
  const percent = (probability * 100).toFixed(probability < 0.001 ? 3 : 2);
  const oneIn = probability > 0 ? Math.round(1 / probability) : null;
  return oneIn ? `${percent}% (1 in ${oneIn})` : `${percent}%`;
}

/**
 * One orb kind's declared value range: every credit amount and jackpot tier it
 * can land on, base game, at the paytable's own minimum bet.
 */
function OrbTable({ table }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm font-medium">{KIND_LABEL[table.symbol_kind] ?? table.symbol_kind}</p>
        <p className="text-muted-foreground text-xs">Bet {table.bet}</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.16em] uppercase">
            <tr className="border-b">
              <th className="py-2 pr-3 text-left font-semibold">Value</th>
              <th className="py-2 pr-3 text-right font-semibold">Weight</th>
              <th className="py-2 pl-3 text-right font-semibold">Probability</th>
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, index) => (
              <tr
                key={row.jackpot_code ?? row.value ?? index}
                className="border-b last:border-0"
              >
                <td className="py-1.5 pr-3">
                  {row.jackpot_code !== null ? (
                    <Badge variant="secondary">
                      Jackpot{row.jackpot_label ? ` (${row.jackpot_label})` : ""}
                    </Badge>
                  ) : (
                    <span className="font-mono font-medium tabular-nums">
                      {row.value}
                    </span>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-right font-mono tabular-nums">
                  {row.weight.toLocaleString()}
                </td>
                <td className="text-muted-foreground py-1.5 pl-3 text-right font-mono tabular-nums">
                  {formatProbability(row.probability)}
                </td>
              </tr>
            ))}
          </tbody>
          {table.expected_value != null ? (
            <tfoot>
              <tr className="border-t">
                <td className="py-1.5 pr-3 text-xs font-semibold" colSpan={2}>
                  Expected value
                </td>
                <td className="py-1.5 pl-3 text-right font-mono font-semibold tabular-nums">
                  {table.expected_value.toFixed(1)}
                </td>
              </tr>
            </tfoot>
          ) : null}
        </table>
      </div>
    </div>
  );
}

/**
 * What a landed orb can show: every credit amount and jackpot tier math.xml
 * declares for it, base game, at the paytable's own minimum bet. A different
 * question from the scatter awards above -- this is what one tile pays, not
 * what a count of them across the grid pays.
 */
export function OrbValueTable({ math }) {
  const tables = math.orb_value_tables ?? [];
  if (tables.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Gem className="size-4" />
          Orb value range
        </CardTitle>
        <CardDescription>
          What a landed orb can show, base game, at the minimum bet
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-6">
        {tables.map((table) => (
          <OrbTable key={table.symbol_kind} table={table} />
        ))}
      </CardContent>
    </Card>
  );
}
