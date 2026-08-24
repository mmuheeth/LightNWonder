import { ChevronDown, Rows3 } from "lucide-react";
import { useState } from "react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { SymbolChip } from "@/features/paytable/symbol-chip";

/**
 * The strips of one set, laid out the way they sit on the machine: one column
 * per reel, one row per stop.
 *
 * A strip *is* its stop order, so this is the order and nothing else — not a
 * tally of what each symbol occupies, and not the per-stop weights, both of
 * which are on the response but say nothing about where a symbol sits. Strips
 * within a set can differ in length (a five-reel set with a shorter reel 5 is
 * normal), so short columns simply end.
 */
export function ReelStripTable({ math }) {
  const sets = math.reel_strip_sets;
  const defaultSet =
    sets.find((set) => set.is_default)?.identifier ?? sets[0]?.identifier ?? "";
  const [setId, setSetId] = useState("");
  const selected = setId || defaultSet;

  const strips = math.reel_strips.filter((strip) => strip.set_id === selected);
  const depth = Math.max(0, ...strips.map((strip) => strip.symbols.length));
  const roles = Object.fromEntries(
    math.symbols.map((symbol) => [symbol.code, symbol.role]),
  );
  const names = Object.fromEntries(
    math.symbols.map((symbol) => [symbol.code, symbol.name]),
  );
  const truncated = strips.some((strip) => strip.truncated);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Rows3 className="size-4" />
          Reel strips
        </CardTitle>
        <CardDescription>
          {strips.length} strips in {selected || "no set"}
          {depth ? ` · up to ${depth} stops each` : ""}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-3">
        <div className="relative">
          <select
            aria-label="Reel strip set"
            value={selected}
            onChange={(event) => setSetId(event.target.value)}
            className="border-input bg-background text-foreground hover:bg-accent/50 focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] h-9 w-full min-w-0 cursor-pointer appearance-none rounded-md border px-3 pr-9 font-mono text-sm shadow-xs transition-[color,box-shadow,background-color] outline-none dark:bg-input/30"
          >
            {sets.map((set) => (
              <option
                key={set.identifier}
                value={set.identifier}
                className="bg-background text-foreground"
              >
                {set.identifier}
                {set.is_default ? " (base game)" : ""}
              </option>
            ))}
          </select>
          <span className="text-muted-foreground pointer-events-none absolute inset-y-0 right-3 flex items-center">
            <ChevronDown className="size-4" />
          </span>
        </div>

        {truncated ? (
          <p className="text-muted-foreground text-xs">
            Some strips are longer than the backend sends; the stop count in each header
            is the real one.
          </p>
        ) : null}

        {strips.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            This set names no strip that the maths declares.
          </p>
        ) : (
          <div className="max-h-[32rem] overflow-auto rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-card sticky top-0 z-10 border-b">
                <tr>
                  <th className="text-muted-foreground bg-card sticky left-0 z-20 px-2 py-2 text-right text-[0.65rem] font-semibold tracking-[0.16em] uppercase">
                    Stop
                  </th>
                  {strips.map((strip) => (
                    <th key={strip.identifier} className="px-2 py-2 text-left">
                      <span className="block text-xs font-semibold">
                        Reel {(strip.reel_index ?? 0) + 1}
                      </span>
                      <span className="text-muted-foreground block font-mono text-[0.65rem] font-normal">
                        {strip.identifier} · {strip.length}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: depth }, (_, index) => (
                  <tr key={index} className="border-b last:border-0">
                    <td className="text-muted-foreground bg-card sticky left-0 px-2 py-1 text-right font-mono text-xs tabular-nums">
                      {index}
                    </td>
                    {strips.map((strip) => {
                      const code = strip.symbols[index];
                      return (
                        <td key={strip.identifier} className="px-2 py-1">
                          {code ? (
                            <span className="flex items-center gap-1.5">
                              <SymbolChip
                                code={code}
                                name={names[code]}
                                role={roles[code]}
                              />
                              {/* The name beside the code, so a strip reads as
                                  symbols rather than as a wall of initials.
                                  Truncated rather than wrapped: a stop is one
                                  line, and five reels of "Orb (Splittable)"
                                  would otherwise set the row height. */}
                              {names[code] ? (
                                <span
                                  className="text-muted-foreground max-w-24 truncate text-xs"
                                  title={names[code]}
                                >
                                  {names[code]}
                                </span>
                              ) : null}
                            </span>
                          ) : null}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
