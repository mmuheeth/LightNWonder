import { Coins } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { SymbolChip, SymbolRun } from "@/features/paytable/symbol-chip";

function roleMap(symbols) {
  return Object.fromEntries(symbols.map((symbol) => [symbol.code, symbol.role]));
}

/**
 * The line pays, laid out the way a paytable poster is: a row per symbol and a
 * column per run length.
 *
 * A combo is one symbol repeated with an `ANY` tail, so every pay is really a
 * (symbol, run, value) triple — and listing the combos in their declared order
 * instead makes that three unordered rows per symbol. Symbols paying alike
 * share a row (a game gives its card ranks one profile), and a blank cell means
 * that run does not pay for them, not that it pays nothing.
 *
 * The backend does the pivot; `math.payline_combos` is still the faithful
 * reading of the file, and any combo mixing two symbols stays there and is
 * listed below, since it has no row to sit in.
 */
export function PaylineComboTable({ math }) {
  const roles = roleMap(math.symbols);
  const lengths = math.pay_lengths ?? [];
  const rows = math.pay_table ?? [];

  const mixed = math.payline_combos.filter(
    (combo) => new Set(combo.symbols.filter((code) => code !== "ANY")).size > 1,
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Coins className="size-4" />
          Payline combos
        </CardTitle>
        <CardDescription>
          What each symbol pays for a run of it, left to right along a line
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.16em] uppercase">
              <tr className="border-b">
                <th className="py-2 pr-3 text-left font-semibold">Code</th>
                <th className="py-2 pr-3 text-left font-semibold">Name</th>
                {lengths.map((length) => (
                  <th key={length} className="w-16 py-2 pl-3 text-right font-semibold">
                    x{length}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.codes.join("/")} className="border-b last:border-0">
                  <td className="py-1.5 pr-3">
                    <span className="flex flex-wrap items-center gap-1">
                      {row.codes.map((code, index) => (
                        <span key={code} className="flex items-center gap-1">
                          {index > 0 ? (
                            <span className="text-muted-foreground text-xs">/</span>
                          ) : null}
                          <SymbolChip
                            code={code}
                            name={row.names[index]}
                            role={roles[code]}
                          />
                        </span>
                      ))}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3 text-xs font-medium">
                    {row.names.some(Boolean)
                      ? row.names
                          .map((name, index) => name ?? row.codes[index])
                          .join(" / ")
                      : "—"}
                  </td>
                  {row.values.map((value, index) => (
                    <td
                      key={lengths[index]}
                      className="py-1.5 pl-3 text-right font-mono font-medium tabular-nums"
                    >
                      {value ?? (
                        <span className="text-muted-foreground font-normal">—</span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Nothing in the shipped games hits this, but a combo of two different
            symbols cannot sit in a per-symbol row, and dropping it silently
            would understate the paytable. */}
        {mixed.length > 0 ? (
          <div className="space-y-2 border-t pt-4">
            <p className="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.16em] uppercase">
              Mixed-symbol combos
            </p>
            {mixed.map((combo, index) => (
              <div
                key={combo.combo_id ?? index}
                className="flex items-center justify-between gap-4"
              >
                <SymbolRun symbols={combo.symbols} names={combo.names} roles={roles} />
                <span className="font-mono font-medium tabular-nums">
                  {combo.value ?? "—"}
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
