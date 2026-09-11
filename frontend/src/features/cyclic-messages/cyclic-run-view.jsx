import { ImageOff, ScanText, Timer, TriangleAlert } from "lucide-react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cyclicFileUrl } from "@/features/cyclic-messages/api";
import {
  useCyclicRun,
  useReadCyclicText,
} from "@/features/cyclic-messages/use-cyclic-messages";

/** Times are what a tester correlates against; dates are in the run id already. */
function formatTime(value) {
  if (!value) return "—";
  const at = new Date(value);
  return Number.isNaN(at.getTime()) ? value : at.toLocaleTimeString();
}

/**
 * Split a run into its cyclic sequences, preserving order.
 *
 * The events already carry a `cycle`, so this only groups — it never decides
 * which sequence something belongs to. That call is the backend's, made while
 * following the log, and re-deriving it here from event names would be a second
 * answer that could disagree with the manifest.
 */
function bySequence(events) {
  const groups = [];
  for (const event of events) {
    const last = groups.at(-1);
    if (last && last.cycle === event.cycle) last.events.push(event);
    else groups.push({ cycle: event.cycle, events: [event] });
  }
  return groups;
}

/**
 * A boundary marker: structure, not a frame. Rendered as a thin rule rather
 * than a card with an empty image box — it never had a picture to miss.
 */
function MarkerRow({ event }) {
  return (
    <li className="text-muted-foreground flex items-baseline gap-2 px-1 text-xs">
      <span className="font-mono">{String(event.sequence).padStart(3, "0")}</span>
      <span className="border-b border-dashed" />
      <Badge variant="outline">{event.event}</Badge>
      <span className="truncate">{event.summary}</span>
      <span className="ml-auto font-mono">{formatTime(event.at)}</span>
    </li>
  );
}

/**
 * One message: what the strip did, then the frame it triggered. Full-width
 * image, not a thumbnail — the strip is a thin band of small text and is
 * unreadable shrunk down, which is the one thing a reader is here for.
 */
function MessageCard({ runId, event }) {
  const sampled = event.source === "sampled";
  const fields = Object.entries(event.fields ?? {});

  return (
    <li className="bg-card flex flex-col gap-3 rounded-lg border p-5">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-muted-foreground font-mono text-xs">
          {String(event.sequence).padStart(3, "0")}
        </span>
        <Badge variant={sampled ? "outline" : "default"}>{event.event}</Badge>
        {/* The honesty of the feature, on the face of every frame that needs
            it: no log line describes this picture. */}
        {sampled ? (
          <Badge variant="secondary" title="Taken on a timer; see the note below">
            <Timer className="size-3" />
            sampled
          </Badge>
        ) : null}
        <span className="text-muted-foreground font-mono text-xs">
          {formatTime(event.at)}
        </span>
      </div>

      <div className="space-y-2">
        <p className="text-base font-medium">{event.summary}</p>

        {fields.length > 0 ? (
          <dl className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
            {fields.map(([key, value]) => (
              <div key={key} className="flex gap-1">
                <dt className="text-muted-foreground">{key}</dt>
                <dd className="font-mono font-medium">{value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>

      {event.screenshot ? (
        // Full size in a new tab rather than a lightbox — no extra dependency.
        <a
          href={cyclicFileUrl(runId, event.screenshot)}
          target="_blank"
          rel="noreferrer"
          className="block"
        >
          <img
            src={cyclicFileUrl(runId, event.screenshot)}
            alt={`${event.event} at ${formatTime(event.at)}`}
            loading="lazy"
            // object-contain, not cropped: a crop could hide the message strip.
            className="bg-muted/40 aspect-video w-full rounded-md border object-contain"
          />
        </a>
      ) : (
        <div className="bg-muted/40 text-muted-foreground flex aspect-video w-full flex-col items-center justify-center gap-2 rounded-md border">
          <ImageOff className="size-6" />
          <p className="px-4 text-center text-xs">
            {event.capture_error ?? "No screenshot was taken."}
          </p>
        </div>
      )}
    </li>
  );
}

/** How a clip ended, said in words rather than as its rule name. */
const CLOSED_BY = {
  "cyclic-line-pays-cycle-finished": "one full pass through the line messages",
  "cyclic-results-cycle-stopped": "cut short — the next spin cleared the strip",
  "cyclic-game-pays": "cut short — the next win began",
  "run-stopped": "cut short — tracking was stopped",
  "time-limit": "cut short — the line messages never reported finishing",
};

/**
 * One win presentation's recording: “GAME PAYS” through the line messages.
 *
 * Rendered inside the sequence it covers rather than at the top of the run,
 * because that is what it is a video of — the frames beside it were taken from
 * inside this clip.
 */
function ClipVideo({ runId, clip }) {
  const ended = clip.closed_by ? CLOSED_BY[clip.closed_by] : null;

  if (!clip.file_name) {
    return (
      <p className="text-muted-foreground text-xs">
        No video for this win. {clip.error}
      </p>
    );
  }
  return (
    <figure className="space-y-1">
      <video
        controls
        preload="metadata"
        src={cyclicFileUrl(runId, clip.file_name)}
        className="bg-muted/40 aspect-video w-full rounded-md border"
      />
      <figcaption className="text-muted-foreground text-xs">
        {ended ?? clip.closed_by}
      </figcaption>
    </figure>
  );
}

/** `12.5` → `0:12.5`, which is what a video player's scrubber shows. */
function formatOffset(seconds) {
  const whole = Math.floor(seconds);
  const tenths = Math.round((seconds - whole) * 10);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}.${tenths}`;
}

/**
 * The messages read out of one clip, and the button that asks for them.
 *
 * A button rather than an automatic fetch: the backend decodes a frame a
 * second and runs OCR on each, which is minutes of work for a long clip and
 * nobody should pay for it by opening a page.
 */
function ClipMessages({ runId, clip }) {
  const read = useReadCyclicText(runId);
  const reading = read.data;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => read.mutate({ cycle: clip.cycle })}
          disabled={read.isPending}
        >
          <ScanText className={read.isPending ? "animate-pulse" : undefined} />
          {read.isPending ? "Reading the clip…" : "Read the messages"}
        </Button>
        {read.isPending ? (
          <span className="text-muted-foreground text-xs">
            A frame a second, each one OCR'd — around three minutes for a
            90-second clip. Leave the tab open.
          </span>
        ) : null}
      </div>

      {read.error ? <ApiErrorAlert error={read.error} /> : null}

      {reading ? (
        <div className="space-y-2">
          {/* The engine is named because it is a per-host setting and the two
              disagree about a caption — without it, two readings of one clip
              look like the same reading changing its mind. */}
          <p className="text-muted-foreground text-xs">
            {reading.captions.length} captions over {reading.messages.length}{" "}
            showings, from {reading.frames_sampled} frames at{" "}
            {reading.interval_seconds}s, read by {reading.engine}
          </p>

          {/* The one thing that makes a plausible-looking reading untrustworthy,
              said before the list rather than buried per row. */}
          {reading.frames_unreadable > 0 ? (
            <p className="text-destructive flex items-start gap-2 text-xs">
              <TriangleAlert className="mt-0.5 size-3 shrink-0" />
              <span>
                {reading.frames_unreadable} of {reading.frames_sampled} frames
                came back below the confidence floor. That is usually the
                recording rather than the reader — a caption the encoder smeared
                still OCRs to something plausible, so treat the greyed rows as
                unreliable and raise the OBS recording quality.
              </span>
            </p>
          ) : null}

          {/* `captions`, not `messages`: the strip loops, so the chronological
              list says "Line 1 Pays 250" once per time round. The repeats are
              counted on the row instead — the timeline is still on the
              response for anyone who needs when rather than what. */}
          {reading.captions.length === 0 ? (
            <p className="text-muted-foreground text-xs">
              No text was found on any frame of this clip.
            </p>
          ) : (
            <ol className="divide-y rounded-md border text-sm">
              {reading.captions.map((caption, index) => (
                <li
                  key={`${caption.first_seen}-${index}`}
                  className={`flex items-baseline gap-3 px-3 py-1.5 ${
                    caption.reliable ? "" : "text-muted-foreground"
                  }`}
                >
                  <span className="font-mono text-xs">
                    {formatOffset(caption.first_seen)}
                  </span>
                  <span className={caption.reliable ? "font-medium" : "italic"}>
                    {caption.text}
                  </span>
                  {caption.showings > 1 ? (
                    <span className="text-muted-foreground font-mono text-xs">
                      &times;{caption.showings}
                    </span>
                  ) : null}
                  <span
                    className={`ml-auto font-mono text-xs ${
                      caption.reliable ? "text-muted-foreground" : "text-destructive"
                    }`}
                  >
                    {Math.round(caption.confidence)}%
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>
      ) : null}
    </div>
  );
}

/** One cyclic message run: its sequences in order, each frame with its picture. */
export function CyclicRunView({ runId }) {
  const { data, error, isPending, refetch } = useCyclicRun(runId);

  if (isPending) {
    return (
      <div className="grid gap-6 lg:grid-cols-2">
        <Skeleton className="h-72 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  if (error) return <ApiErrorAlert error={error} onRetry={() => refetch()} />;

  const sequences = bySequence(data.events);
  // Keyed by the sequence each clip covers: the backend decided which win a
  // recording belongs to, and re-deriving it from timestamps here would be a
  // second answer that could disagree with the manifest.
  const clips = new Map((data.videos ?? []).map((clip) => [clip.cycle, clip]));

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold">{data.game}</h2>
        <p className="text-muted-foreground text-sm">
          {data.message_count} messages over {data.cycle_count} sequences ·{" "}
          {clips.size} {clips.size === 1 ? "clip" : "clips"} ·{" "}
          {formatTime(data.started_at)} to {formatTime(data.stopped_at)} · {data.status}
        </p>
        <p className="text-muted-foreground font-mono text-xs break-all">
          {data.log_path}
        </p>
      </div>

      {data.sampled_count > 0 ? (
        <p className="text-muted-foreground bg-muted/40 rounded-md border p-3 text-xs">
          <strong className="font-medium">{data.sampled_count}</strong> of these
          frames are marked <em>sampled</em>. The game logs where the line
          messages start and where one full pass through them ends, but writes
          nothing at all in between — so those frames were taken on a timer
          inside that window, and their log line is the boundary that opened it
          rather than a line describing the message in the picture.
        </p>
      ) : null}

      {data.errors?.length > 0 ? (
        <ul className="text-destructive space-y-1 text-xs">
          {data.errors.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      ) : null}

      {data.events.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No cyclic messages were recognised during this run.
        </p>
      ) : (
        sequences.map((group) => (
          <section key={group.cycle} className="space-y-3">
            <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
              Sequence {group.cycle}
            </h3>
            {clips.has(group.cycle) ? (
              <>
                <ClipVideo runId={runId} clip={clips.get(group.cycle)} />
                {clips.get(group.cycle).file_name ? (
                  <ClipMessages runId={runId} clip={clips.get(group.cycle)} />
                ) : null}
              </>
            ) : null}
            <ul className="space-y-2">
              {group.events
                .filter((event) => !event.captured)
                .map((event) => (
                  <MarkerRow key={event.sequence} event={event} />
                ))}
            </ul>
            <ul className="grid gap-6 lg:grid-cols-2">
              {group.events
                .filter((event) => event.captured)
                .map((event) => (
                  <MessageCard key={event.sequence} runId={runId} event={event} />
                ))}
            </ul>
          </section>
        ))
      )}
    </div>
  );
}
