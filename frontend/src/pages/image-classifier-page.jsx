import { ApiErrorAlert } from "@/components/api-error-alert";
import { Badge } from "@/components/ui/badge";
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

const STATE_BADGE = {
  ready: "secondary",
  training: "default",
  stale: "outline",
  untrained: "outline",
  not_installed: "destructive",
  disabled: "outline",
  error: "destructive",
};

/**
 * Naming the symbol on a reel tile, from the picture.
 *
 * Its own route rather than a dashboard card, for the same reason Game Config and
 * Analyze Spin are: the dataset is a nine-row table, training is a five-stage list
 * with its own figures, and a classification is fifteen tiles each with a picture,
 * a name and three probabilities. None of that survives half a row.
 *
 * Nothing else in this app names a symbol from pixels -- the payline check only
 * asks whether two tiles match each other, and the reel-stop reading takes its
 * answer from the game's own log. So this is the one reading that can disagree
 * with the game, which is the only kind that can catch it drawing the wrong thing.
 */
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
            EfficientNet-B0 over the symbol artwork, then read back against the tiles
            the reel grid wrote.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {data ? (
            <Badge variant={STATE_BADGE[data.state] ?? "outline"}>{data.state}</Badge>
          ) : null}
          <GameSelector />
        </div>
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
