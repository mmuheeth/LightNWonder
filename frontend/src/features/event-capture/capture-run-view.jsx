import { ImageOff } from "lucide-react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { captureImageUrl } from "@/features/event-capture/api";
import { useCaptureRun } from "@/features/event-capture/use-event-capture";

/** Times are what a tester correlates against; dates are in the run id already. */
function formatTime(value) {
  if (!value) return "—";
  const at = new Date(value);
  return Number.isNaN(at.getTime()) ? value : at.toLocaleTimeString();
}

/**
 * One event: what happened, then the frame it triggered.
 *
 * Description above the image rather than beside it, and two of these to a row
 * at most. A screenshot of a slot game is mostly reels, and reels shrunk into a
 * thumbnail beside a paragraph are unreadable -- which is the whole point of
 * having taken it. So the image gets the card's full width, and the sentence
 * that says what to look for sits above it.
 */
function EventCard({ runId, event }) {
  const hasFields = Object.keys(event.fields ?? {}).length > 0;

  return (
    <li className="bg-card flex flex-col gap-3 rounded-lg border p-5">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-muted-foreground font-mono text-xs">
          {String(event.sequence).padStart(3, "0")}
        </span>
        <Badge>{event.event}</Badge>
        <span className="text-muted-foreground font-mono text-xs">
          {formatTime(event.at)}
        </span>
      </div>

      <div className="space-y-2">
        <p className="text-base font-medium">{event.summary}</p>

        {hasFields ? (
          <dl className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
            {Object.entries(event.fields).map(([key, value]) => (
              <div key={key} className="flex gap-1">
                <dt className="text-muted-foreground">{key}</dt>
                <dd className="font-mono font-medium">{value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>

      {event.screenshot ? (
        // Opening full size in a tab rather than a lightbox: the images are
        // already on disk and a new tab needs no extra dependency.
        <a
          href={captureImageUrl(runId, event.screenshot)}
          target="_blank"
          rel="noreferrer"
          className="block"
        >
          <img
            src={captureImageUrl(runId, event.screenshot)}
            alt={`${event.event} at ${formatTime(event.at)}`}
            loading="lazy"
            // Contained rather than cropped: a cropped frame can hide the
            // meter or the message that made the event worth capturing.
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

/** One capture run: its events in order, each with the frame it triggered. */
export function CaptureRunView({ runId }) {
  const { data, error, isPending, refetch } = useCaptureRun(runId);

  if (isPending) {
    return (
      <div className="grid gap-6 lg:grid-cols-2">
        <Skeleton className="h-72 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }

  if (error) return <ApiErrorAlert error={error} onRetry={() => refetch()} />;

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold">{data.game}</h2>
        <p className="text-muted-foreground text-sm">
          {data.event_count} events · {formatTime(data.started_at)} to{" "}
          {formatTime(data.stopped_at)} · {data.status}
        </p>
        <p className="text-muted-foreground font-mono text-xs break-all">
          {data.log_path}
        </p>
      </div>

      {data.errors?.length > 0 ? (
        <ul className="text-destructive space-y-1 text-xs">
          {data.errors.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      ) : null}

      {data.events.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No events were recognised during this run.
        </p>
      ) : (
        <ul className="grid gap-6 lg:grid-cols-2">
          {data.events.map((event) => (
            <EventCard key={event.sequence} runId={runId} event={event} />
          ))}
        </ul>
      )}
    </div>
  );
}
