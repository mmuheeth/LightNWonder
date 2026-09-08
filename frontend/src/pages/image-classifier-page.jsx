import { ApiErrorAlert } from "@/components/api-error-alert";
import { Skeleton } from "@/components/ui/skeleton";
import { GameSelector } from "@/features/games/game-selector";
import { ClassifyCard } from "@/features/image-classifier/classify-card";
import { TrainCard } from "@/features/image-classifier/train-card";
import {
  useCancelTraining,
  useClassifierStatus,
  useClassifyTiles,
  useSplits,
  useTrainClassifier,
} from "@/features/image-classifier/use-image-classifier";

/** Naming the symbol on a reel tile, from the picture. */
export function ImageClassifierPage() {
  const status = useClassifierStatus();
  const splits = useSplits();
  const train = useTrainClassifier();
  const cancel = useCancelTraining();
  const classify = useClassifyTiles();

  const data = status.data ?? null;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 border-b pb-6 sm:flex-row sm:items-end sm:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Image Classifier</h1>
          <p className="text-muted-foreground text-sm">
            A network trained on the symbol artwork, then read back against the tiles
            the reel grid wrote.
          </p>
        </div>
        <GameSelector />
      </div>

      {status.error && !data ? (
        <ApiErrorAlert error={status.error} onRetry={() => status.refetch()} />
      ) : null}

      {status.isPending ? (
        <div className="space-y-6">
          <Skeleton className="h-72 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : (
        <>
          {data?.detail && data.state !== "ready" ? (
            <p className="text-muted-foreground text-sm">{data.detail}</p>
          ) : null}

          <TrainCard
            status={data}
            isFetching={status.isFetching}
            refetch={status.refetch}
            train={train}
            cancel={cancel}
          />

          <ClassifyCard status={data} splits={splits} classify={classify} />
        </>
      )}
    </div>
  );
}
