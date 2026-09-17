import { Camera, Circle, ImageOff, ScanText, TriangleAlert } from "lucide-react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cyclicFileUrl } from "@/features/cyclic-messages/api";
import { useCyclicLive } from "@/features/cyclic-messages/use-cyclic-messages";

/** Times are what a tester correlates against; the date is in the run id. */
function formatTime(value) {
  if (!value) return "—";
  const at = new Date(value);
  return Number.isNaN(at.getTime()) ? value : at.toLocaleTimeString();
}

/**
 * The caption the reader made of one frame, in the three states it has.
 *
 * The three are kept apart on purpose and this is the whole reason the card
 * exists. *Queued* is a frame the worker has not reached — its picture is
 * already here and its text is coming. *Read* is text. *Failed* is a frame the
 * worker reached and could not read, which is a different fact from a strip
 * that said nothing and the only one of the three worth investigating.
 */
/**
 * Every line of the strip on one frame, top line first.
 *
 * The strip is stacked — FortuneOx draws "LINE 25 PAYS 15" with "PLAY 880
 * CREDITS" beneath it — and the two cycle independently, so what was on screen
 * is both of them together rather than either alone. They are read separately
 * (see the backend's CYCLIC_MESSAGES_TEXT_REGIONS: one crop per line, because
 * the recogniser is single-line and reading both at once mangles them), and
 * they are shown stacked in the same order the game draws them.
 */
function Strip({ runId, readings, capturing }) {
  // A band that read nothing is simply not shown. The strip does not use every
  // one of its lines at every moment -- between spins FortuneOx leaves the top
  // band empty for a whole pass -- so saying so under every frame, above a crop
  // of an empty band, was a row of nothing to report. Dropping the band here
  // rather than inside `Caption` is what keeps its crop meaningful: every
  // picture shown is the pixels behind a caption that was actually read.
  const said = (readings ?? []).filter(
    (reading) => reading.error || reading.repaired,
  );
  if (said.length === 0) {
    // Two different waits, and saying which matters: while the strip is still
    // playing nothing has even been handed to the recogniser yet, by design,
    // and a row that says "reading…" for a minute looks stuck rather than
    // deliberate.
    return (
      <p className="text-muted-foreground flex items-center gap-1.5 text-xs italic">
        <ScanText className="size-3 animate-pulse" />
        {capturing ? "read when the strip stops" : "reading…"}
      </p>
    );
  }
  return (
    <div className="space-y-2">
      {said.map((reading, index) => (
        <Caption key={reading.region || index} runId={runId} reading={reading} />
      ))}
    </div>
  );
}

function Caption({ runId, reading }) {
  if (reading.error) {
    return (
      <p className="text-destructive flex items-start gap-1.5 text-xs">
        <TriangleAlert className="mt-0.5 size-3 shrink-0" />
        <span className="break-words">{reading.error}</span>
      </p>
    );
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline gap-2">
        <p
          className={reading.reliable ? "font-medium" : "text-muted-foreground italic"}
        >
          {reading.repaired}
        </p>
        {reading.confidence > 0 ? (
          <span
            className={`ml-auto font-mono text-xs ${
              reading.reliable ? "text-muted-foreground" : "text-destructive"
            }`}
          >
            {Math.round(reading.confidence)}%
          </span>
        ) : null}
      </div>

      {/* The crop the recogniser was actually given. A wrong reading is either
          a misread or a badly aimed region, and only the pixels say which.
          Shown only under a line that read something -- `Strip` has already
          dropped the bands the strip was not using, so this is never a picture
          of an empty band. */}
      {reading.crop ? (
        <img
          src={cyclicFileUrl(runId, reading.crop)}
          alt={`roi.${reading.region} crop`}
          loading="lazy"
          className="bg-muted/40 max-h-10 rounded border"
        />
      ) : null}
    </div>
  );
}

/**
 * What was on screen, as one key: every line of the strip joined together.
 *
 * Not the top line alone. The two lines cycle independently, so "LINE 25 PAYS
 * 15 / PLAY 880 CREDITS" and "LINE 25 PAYS 15 / GAME OVER" are two different
 * things the strip showed, and keying on the first line would merge them into
 * one and lose the second line's message entirely.
 *
 * Empty unless at least one line read as something — a frame still waiting to
 * be read, or one where every line came back blank, groups with nothing.
 */
function stripKey(frame) {
  const keys = (frame.readings ?? []).map((reading) => reading.key ?? "");
  return keys.some(Boolean) ? keys.join("|") : "";
}

/**
 * Collapse the pass's frames into one entry per caption the strip showed.
 *
 * Two stages, because there are two different reasons the same caption turns up
 * more than once and only counting both gets the list right:
 *
 * 1. **Sampling is faster than the strip changes**, so a caption is usually
 *    caught two or three times in a row. Those adjacent frames are one showing.
 * 2. **The strip loops.** A pass walks its messages and starts again, so one
 *    caption comes round once per lap with others in between. Those are
 *    separate showings — real repeats, but not something to list over and over.
 *
 * So showings are counted rather than listed, exactly as the clip reader does
 * it (`cyclic_text._distinct`) and exactly as “Read the messages” renders it.
 * Ordered by first appearance, not by count: the strip has an order and it is
 * the order a reader is checking against.
 *
 * Grouping only, never deciding — `reading.key` is the backend's own rule for
 * "the same caption" (`cyclic_text.caption_key`), so the stills and a clip read
 * back off the same pass cannot disagree about what the strip said.
 *
 * A frame with no key — not read yet, or read as nothing — is its own entry and
 * merges with nothing. During capture that is every frame, which is what keeps
 * the pictures arriving one per frame; only once the captions land does the
 * list collapse to the messages behind them. A blank also *breaks* a run, so a
 * caption either side of one counts as two showings rather than being bridged
 * into a single message that spans the gap.
 */
function byCaption(frames) {
  const groups = [];
  const seen = new Map();
  // The strip currently on screen, so a repeat that is merely the next frame
  // of it is not counted as a fresh showing.
  let open = null;

  for (const frame of frames) {
    const key = stripKey(frame);
    if (!key) {
      groups.push({ key: "", frames: [frame], showings: 1 });
      open = null;
      continue;
    }
    const group = seen.get(key);
    if (group === undefined) {
      const created = { key, frames: [frame], showings: 1 };
      seen.set(key, created);
      groups.push(created);
    } else {
      group.frames.push(frame);
      if (open !== key) group.showings += 1;
    }
    open = key;
  }
  return groups;
}

/**
 * How well a frame read, as one number: the worst of the lines that said
 * something.
 *
 * The worst rather than the mean, because one mangled line makes the strip
 * wrong however cleanly the other read. But only of the lines that *said*
 * something: a band the strip is not using scores 0 and is not a bad reading,
 * and counting it would drag every frame of a pass to 0 and leave
 * `bestFrame` choosing between them at random.
 */
function stripConfidence(frame) {
  const scored = (frame.readings ?? [])
    .filter((reading) => reading.repaired)
    .map((reading) => reading.confidence ?? 0);
  return scored.length === 0 ? -1 : Math.min(...scored);
}

/**
 * The frame of a group to actually show — the one its recogniser was surest of.
 *
 * The clip reader's rule (`_group`: "a message keeps the best-scoring of the
 * readings that formed it"), applied here for the same reason. Grouping
 * tolerates spacing and punctuation, so the frames of one caption need not
 * agree character for character, and the reading the engine scored highest is
 * the better bet than whichever happened to be last. The picture comes from
 * that same frame, so the caption shown is the caption in the screenshot
 * under it.
 */
function bestFrame(frames) {
  return frames.reduce((best, frame) =>
    stripConfidence(frame) > stripConfidence(best) ? frame : best,
  );
}

/** One captured frame: the picture first, its caption underneath. */
function LiveFrame({ runId, group, capturing }) {
  const frame = bestFrame(group.frames);
  const sampled = frame.source === "sampled";

  return (
    <li className="bg-card flex flex-col gap-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-baseline gap-2 text-xs">
        <span className="text-muted-foreground font-mono">
          {String(frame.sequence).padStart(3, "0")}
        </span>
        <Badge variant={sampled ? "outline" : "default"}>{frame.event}</Badge>
        {/* Counted rather than hidden, and showings rather than frames: how
            many times the strip came round to this caption is a fact about the
            pass, where how many frames caught it is a fact about the sampling
            rate. Both are kept — dropping them would make a caption that was up
            for three laps look identical to one that flashed past once. */}
        {group.showings > 1 ? (
          <span
            className="text-muted-foreground font-mono"
            title={`${group.frames.length} frames caught it`}
          >
            &times;{group.showings}
          </span>
        ) : null}
        <span className="text-muted-foreground ml-auto font-mono">
          {formatTime(frame.at)}
        </span>
      </div>

      {frame.screenshot ? (
        <a
          href={cyclicFileUrl(runId, frame.screenshot)}
          target="_blank"
          rel="noreferrer"
          className="block"
        >
          <img
            src={cyclicFileUrl(runId, frame.screenshot)}
            alt={`${frame.event} at ${formatTime(frame.at)}`}
            loading="lazy"
            // object-contain, not cropped: a crop could hide the strip itself.
            className="bg-muted/40 aspect-video w-full rounded-md border object-contain"
          />
        </a>
      ) : (
        <div className="bg-muted/40 text-muted-foreground flex aspect-video w-full flex-col items-center justify-center gap-1 rounded-md border">
          <ImageOff className="size-5" />
          <p className="px-3 text-center text-xs">
            {frame.capture_error ?? "No screenshot was taken."}
          </p>
        </div>
      )}

      <Strip runId={runId} readings={frame.readings} capturing={capturing} />
    </li>
  );
}

/**
 * The win presentation being captured right now, frame by frame.
 *
 * Its own card rather than a section of the run view because it answers a
 * question the run view cannot: the run view reads a sealed manifest, and this
 * reads the live run — so a frame is on screen the moment OBS wrote it rather
 * than at the next flush.
 *
 * A picture and its caption arrive at different times, and the card is laid
 * out around that. The picture appears the moment the capture loop takes it —
 * capturing every 2s is this feature's only priority, so nothing reads a
 * caption while a pass is still going. Every frame sits on “reading…”
 * (`frame.reading` is null) until the pass closes, at which point the backend
 * reads them all in order and this card's own poll picks each one up as it
 * lands — so a card fills in from the top down once the strip stops, rather
 * than progressively while it plays.
 *
 * Newest first, unlike the run view. A pass runs to 150s and a tester watching
 * one wants the frame that just landed, not to scroll for it.
 */
export function LiveCaptureCard({ isActive }) {
  const { data, error, isPending } = useCyclicLive(isActive);

  if (!isActive) return null;

  if (error) return <ApiErrorAlert error={error} />;

  if (isPending) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Live capture</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-40 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (!data.active) return null;

  // In the order the strip first showed each caption, which is the order a
  // reader is checking against and the order "Read the messages" lists them in.
  // Not newest-first: once the repeats are counted the list is one entry per
  // caption rather than one per frame, so it no longer grows without bound and
  // there is nothing to scroll past.
  const groups = byCaption(data.frames);
  // Groups that actually read as something: a frame still waiting to be read,
  // and one that read as nothing, are each their own group but neither is a
  // message the strip showed.
  const read = groups.filter((group) => group.key).length;
  const capped = data.frame_count > data.frames.length;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <Camera className="size-4" />
          Live capture
          {data.capturing ? (
            <Badge variant="destructive">
              <Circle className="size-2 animate-pulse fill-current" />
              capturing
            </Badge>
          ) : data.reading ? (
            // Only true for the batch of reading right after a pass closes —
            // capturing and reading never overlap, so these two badges never
            // show together.
            <Badge variant="secondary">
              <ScanText className="size-3 animate-pulse" />
              reading captions
            </Badge>
          ) : data.cycle != null ? (
            <Badge variant="outline">pass finished</Badge>
          ) : null}
        </CardTitle>
        <CardDescription>
          Every frame of whichever strip is running — the win presentation (“GAME PAYS”
          through the line messages) while a win pays out, then the between-spins strip
          (“GAME OVER / GAME PAYS n / PLAY 880 CREDITS”) once the spin is over, whether
          it lost or its win has been taken. Captions are read after each window closes,
          so nothing competes with capture while the strip is playing
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        {data.cycle == null ? (
          <div className="text-muted-foreground space-y-2 text-sm">
            <p>
              Waiting for the first spin to finish. Capture opens on a win paying out
              and again once the spin is over — after a loss, or after a win is taken —
              and closes when the strip stops. The long attract loop beyond that is
              logged message by message and already has a frame each in the run below.
            </p>
            {/* The difference between "working, nothing has paid" and "stuck".
                A losing spin shows no win strip at all, so with nothing on
                screen the only evidence tracking is alive is that the run has
                been reading the log and counting them. */}
            {data.spins_without_pay > 0 ? (
              <p>
                <span className="text-foreground font-medium">
                  {data.spins_without_pay}
                </span>{" "}
                {data.spins_without_pay === 1 ? "spin has" : "spins have"} paid
                nothing since tracking started. Those are captured too — once a spin is
                over the strip cycles “GAME OVER”, “GAME PAYS 0” and “PLAY 880 CREDITS”
                until the next spin clears it.
              </p>
            ) : null}
          </div>
        ) : (
          <>
            <dl className="text-muted-foreground flex flex-wrap gap-x-5 gap-y-1 text-xs">
              <div className="flex gap-1">
                <dt>Sequence</dt>
                <dd className="text-foreground font-mono">{data.cycle}</dd>
              </div>
              <div className="flex gap-1">
                <dt>Frames</dt>
                <dd className="text-foreground font-mono">{data.frame_count}</dd>
              </div>
              {/* The two are different questions and the gap between them is
                  the point of sampling faster than the strip moves: frames is
                  how many pictures were taken, this is how many captions they
                  caught. Only once something has been read is it an answer. */}
              {read > 0 ? (
                <div className="flex gap-1">
                  <dt>Messages</dt>
                  <dd className="text-foreground font-mono">{read}</dd>
                </div>
              ) : null}
              <div className="flex gap-1">
                {/* Both halves: the interval is a floor and an OBS round trip
                    decides the rest, so what was asked for and what landed are
                    two different numbers and only the pair says whether the
                    pass was covered. */}
                <dt>Rate</dt>
                <dd className="text-foreground font-mono">
                  {data.sample_rate.toFixed(2)}/s
                  {data.sample_interval_seconds > 0 ? (
                    <span className="text-muted-foreground">
                      {" "}
                      of {(1 / data.sample_interval_seconds).toFixed(2)}/s
                    </span>
                  ) : null}
                </dd>
              </div>
              <div className="flex gap-1">
                <dt>Read</dt>
                <dd className="text-foreground font-mono">{data.read_count}</dd>
              </div>
              {/* Only while there is one: 0 for the whole pass, jumps to
                  `frame_count` the moment it closes, then counts down as the
                  post-pass read works through them. */}
              {data.queue_depth > 0 ? (
                <div className="flex gap-1">
                  <dt>Left to read</dt>
                  <dd className="text-foreground font-mono">{data.queue_depth}</dd>
                </div>
              ) : null}
            </dl>

            {/* Shown beside a finished pass too, not only on the empty view:
                the frames below are the last win, and without this a run that
                has been losing for ten minutes looks like one frozen on it. */}
            {!data.capturing && data.spins_without_pay > 0 ? (
              <p className="text-muted-foreground text-xs">
                {data.spins_without_pay}{" "}
                {data.spins_without_pay === 1 ? "spin has" : "spins have"} paid
                nothing since this one. The frames below are the most recent win.
              </p>
            ) : null}

            {/* The one failure the frames themselves cannot show: a message
                sampled either side of leaves nothing behind, so a list that is
                missing one looks exactly like a complete list. */}
            {data.coverage_warning ? (
              <p className="text-destructive flex items-start gap-2 text-xs">
                <TriangleAlert className="mt-0.5 size-3 shrink-0" />
                <span>{data.coverage_warning}</span>
              </p>
            ) : null}

            {capped ? (
              <p className="text-muted-foreground text-xs">
                Showing the most recent {data.frames.length} of {data.frame_count}{" "}
                frames. All of them are in the run’s own record below.
              </p>
            ) : null}

            {groups.length === 0 ? (
              <p className="text-muted-foreground text-sm">
                The presentation has opened; the first frame is being taken.
              </p>
            ) : (
              <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {groups.map((group) => (
                  <LiveFrame
                    key={group.frames[0].sequence}
                    runId={data.run_id}
                    group={group}
                    capturing={data.capturing}
                  />
                ))}
              </ul>
            )}
          </>
        )}

        {data.errors.length > 0 ? (
          <ul className="text-destructive space-y-1 text-xs">
            {data.errors.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}
