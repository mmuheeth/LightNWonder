import { useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Skeleton } from "@/components/ui/skeleton";
import { GameSelector } from "@/features/games/game-selector";
import { PaylineComboTable } from "@/features/paytable/payline-combo-table";
import { PaytableSummary } from "@/features/paytable/paytable-summary";
import { ReelStripTable } from "@/features/paytable/reel-strip-table";
import { usePaytable, useRefreshPaytable } from "@/features/paytable/use-paytable";
import { WinGeometryCard } from "@/features/paytable/win-geometry-card";

/**
 * The maths the running game has loaded, read out of its own install.
 *
 * Everything here hangs off one id: the `paytableId` the game writes to its log
 * names a directory under the game's `GameConfig` folder, and that directory's
 * `math.xml` is the symbols, the strips and the combos. So the summary leads
 * and the rest follows from it — and the selector in it can point the page at
 * another paytable of the same game without the game running on it.
 *
 * One page rather than a dashboard card: each of these is a table, and a table
 * does not survive half a row.
 */
export function GameConfigPage() {
  // Empty means "whatever the log says", which is the answer the page is for;
  // an explicit id is a different query rather than a filter on this one.
  const [paytableId, setPaytableId] = useState("");
  const { data, error, isPending, isFetching, refetch } = usePaytable(
    paytableId || null,
  );
  const refresh = useRefreshPaytable();

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-end sm:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Game Config</h1>
          <p className="text-muted-foreground text-sm">
            The paytable the active game loaded, and the maths inside it.
          </p>
        </div>
        <GameSelector />
      </div>

      {isPending ? (
        <div className="space-y-6">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      ) : error ? (
        <div className="space-y-4">
          <ApiErrorAlert error={error} onRetry={() => refetch()} />
          {/* A hand-picked id that came back 404 would otherwise leave the page
              with no way back to the one the log names. */}
          {paytableId ? (
            <button
              type="button"
              onClick={() => setPaytableId("")}
              className="text-muted-foreground hover:text-foreground text-sm underline underline-offset-4"
            >
              Back to the paytable the log names
            </button>
          ) : null}
        </div>
      ) : (
        <>
          <PaytableSummary
            data={data}
            selected={paytableId}
            onSelect={setPaytableId}
            onRefresh={refresh}
            isFetching={isFetching}
          />
          {/* Read down: which paytable, where its lines run, what they pay,
              and what sits on each reel. The strips come last because they are
              by far the longest thing here. */}
          <WinGeometryCard geometry={data.win_geometry} />
          <PaylineComboTable math={data.math} />
          <ReelStripTable math={data.math} />
        </>
      )}
    </div>
  );
}
