import { Clapperboard, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { spinClipUrl } from "@/features/analyze-spin/api";

/** A count of bytes at the precision anyone reads it at. */
function size(bytes) {
  if (typeof bytes !== "number") return "—";
  return bytes < 1024 * 1024
    ? `${Math.round(bytes / 1024)} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The clips laid out the way the reels are, one cell per position. */
function Grid({ runId, columns, clips }) {
  return (
    <div
      className="grid w-fit gap-1.5"
      style={{ gridTemplateColumns: `repeat(${Math.max(columns, 1)}, minmax(0, 1fr))` }}
    >
      {clips.map((clip) => (
        <figure key={clip.name} className="space-y-1">
          {/* No <track>: there is no speech and no sound to caption -- these are
              silent crops of a game's reels, and the position they are of is the
              caption below. */}
          <video
            className="border-border/60 bg-muted/30 block rounded-md border"
            style={{ width: 84 }}
            src={spinClipUrl(runId, clip.file_name)}
            autoPlay
            loop
            muted
            playsInline
          />
          <figcaption className="text-muted-foreground text-center font-mono text-[0.6rem]">
            {clip.name}
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

/** Each reel position, filmed on its own for the seconds after the win landed. */
export function TileClipsCard({ runId, clips }) {
  const measured = clips.fps ? clips.fps.toFixed(1) : "—";
  const requested = clips.requested_fps ? clips.requested_fps.toFixed(0) : "—";
  const seconds = clips.duration_ms ? (clips.duration_ms / 1000).toFixed(1) : "—";
  const written = clips.clips.reduce((total, clip) => total + clip.bytes_written, 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Clapperboard className="size-4" />
          Each reel position, on its own
        </CardTitle>
        <CardDescription>
          Filmed from just after the result screenshot until take-win — which cells the
          game animated, and for how long
        </CardDescription>
        <CardAction>
          <Badge variant="outline" className="font-mono text-[0.65rem]">
            {clips.clips.length} clip{clips.clips.length === 1 ? "" : "s"}
          </Badge>
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {clips.codec ? (
            <Badge variant="secondary" className="font-mono text-[0.65rem]">
              {clips.codec}
            </Badge>
          ) : null}
          <span className="text-muted-foreground text-[0.65rem]">
            {clips.frames} frames over {seconds}s · {measured} fps measured, {requested}{" "}
            asked for · {size(written)}
          </span>
        </div>

        {clips.clips.length > 0 ? (
          <Grid runId={runId} columns={clips.columns} clips={clips.clips} />
        ) : null}

        {/* Reported rather than raised by the backend: losing OBS halfway
            through costs the clips and nothing else about the spin. */}
        {clips.error ? (
          <p className="flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-500">
            <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
            {clips.error}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
