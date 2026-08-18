import { ChevronDown, Gamepad2, LoaderCircle } from "lucide-react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Skeleton } from "@/components/ui/skeleton";
import { useGameCatalog, useSelectGame } from "@/features/games/use-games";

/** Compact dashboard control for the game config driving every integration. */
export function GameSelector() {
  const { data, error, isPending, refetch } = useGameCatalog();
  const select = useSelectGame();
  const selectedGame = select.data?.game ?? data?.active_game ?? "";

  if (isPending) {
    return <Skeleton className="h-14 w-full max-w-xs" />;
  }

  if (error) {
    return <ApiErrorAlert error={error} onRetry={() => refetch()} />;
  }

  return (
    <div className="flex w-full max-w-sm flex-col gap-2">
      <div className="border-border bg-card/70 flex items-center gap-3 rounded-lg border px-3 py-2.5 shadow-xs">
        <div className="bg-primary/10 text-primary flex size-9 shrink-0 items-center justify-center rounded-md">
          <Gamepad2 className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <label
            htmlFor="active-game"
            className="text-muted-foreground block text-[0.65rem] font-semibold tracking-[0.16em] uppercase"
          >
            Active game
          </label>
          <div className="relative mt-0.5">
            <select
              id="active-game"
              aria-label="Active game"
              value={selectedGame}
              onChange={(event) => select.mutate(event.target.value)}
              disabled={select.isPending}
              className="text-foreground w-full appearance-none bg-transparent pr-6 text-sm font-medium outline-none disabled:opacity-60"
            >
              {data.games.map((option) => (
                <option key={option.game} value={option.game}>
                  {option.label}
                </option>
              ))}
            </select>
            {select.isPending ? (
              <LoaderCircle className="text-muted-foreground pointer-events-none absolute top-1/2 right-0 size-4 -translate-y-1/2 animate-spin" />
            ) : (
              <ChevronDown className="text-muted-foreground pointer-events-none absolute top-1/2 right-0 size-4 -translate-y-1/2" />
            )}
          </div>
        </div>
        {select.data?.obs_window_selected === false ? (
          <span className="text-muted-foreground hidden text-[0.65rem] sm:block">
            OBS needs attention
          </span>
        ) : null}
      </div>
      {select.error ? <ApiErrorAlert error={select.error} /> : null}
    </div>
  );
}
