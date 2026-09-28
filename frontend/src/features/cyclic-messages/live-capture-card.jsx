import { Camera, Circle, Film, ImageOff, ScanText, TriangleAlert } from "lucide-react";

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

/**
 * Which strip the frames below are of, in words.
 *
 * A run shows three and they are different things to read — a win paying out,
 * what the game says between spins, and both of those in one view — so the
 * card names the one it is showing rather than leaving it to be inferred from
 * the event badges.
 *
 * The third is one spin's whole presentation. Take the win and the strip runs
 * on from the line messages into “GAME OVER / GAME PAYS n / PLAY 880 CREDITS”;
 * the frames of both are on the card together, whether the backend captured
 * them as one window (the win taken early, so the strip never stopped) or as
 * two (the win taken after its line messages had finished).
 */
const STRIP = {
  "win-video": "win presentation",
  "idle-video": "between-spins strip",
  "win-then-idle": "win presentation, then between-spins",
};

/** Times are what a tester correlates against; the date is in the run id. */
function formatTime(value) {
  if (!value) return "—";
  const at = new Date(value);
  return Number.isNaN(at.getTime()) ? value : at.toLocaleTimeString();
}

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
  const said = (readings ?? []).filter((reading) => reading.error || reading.repaired);
  if (said.length === 0) {
    // Three states, not two, and conflating the last two is what left frames
    // sitting on "reading…" after their pass had been fully read. An *empty*
    // `readings` is a frame the worker has not reached -- the backend fills one
    // entry per region the moment it does, blank region included -- so anything
    // non-empty here was read and the strip simply had nothing believable on it
    // at that moment, which is a fact about the pass and not a pending state.
    // Sampling runs faster than the strip changes, so a frame landing in the
    // gap between two messages is normal and permanent: nothing will ever come
    // back to fill it in, because the clip recovery appends its own frames
    // rather than re-reading these.
    const pending = (readings ?? []).length === 0;
    return (
      <p className="text-muted-foreground flex items-center gap-1.5 text-xs italic">
        <ScanText className={pending ? "size-3 animate-pulse" : "size-3"} />
        {/* And while it is pending, which of the two waits: nothing is handed
            to the recogniser until the pass closes, by design, so a row that
            says "reading…" through a 150s pass looks stuck rather than
            deliberate. */}
        {pending
          ? capturing
            ? "read when the strip stops"
            : "reading…"
          : "no message on the strip"}
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

/**
 * One line of the strip, in the two states that are worth a row of their own.
 *
 * *Read* is text. *Failed* is a line the worker reached and could not read,
 * which is a different fact from a strip that said nothing and the only one of
 * the four states worth investigating. The other two never get here: `Strip`
 * has already taken *queued* (no readings at all) and *said nothing* (read,
 * nothing believable on the band) and rendered them as one line between them.
 */
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
 * Whether this frame was read and the strip had nothing on it.
 *
 * Not the same as "no caption yet": the backend fills one reading per region
 * the moment it reaches a frame, blank regions included, so readings present
 * with nothing believable in any of them is a *finished* answer -- the
 * screenshot caught the gap the strip leaves between two messages.
 *
 * Sampling runs faster than the strip changes on purpose, so these are normal
 * and there are several per pass. They are worth nothing to a reader, which is
 * why `LiveCaptureCard` drops them: a card showing a screenshot of an empty
 * band under the words "no message on the strip" is a tile of nothing to
 * report, and on a between-spins pass half the grid was them.
 */
function readAsNothing(frame) {
  const readings = frame.readings ?? [];
  return (
    readings.length > 0 &&
    !readings.some((reading) => reading.error || reading.repaired)
  );
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
 * (`frame.readings` is empty) until the pass closes, at which point the backend
 * reads them all in order and this card's own poll picks each one up as it
 * lands — so a card fills in from the top down once the strip stops, rather
 * than progressively while it plays.
 *
 * Emptiness is the whole signal there, and it is a list rather than the single
 * `reading` this card was first written against: once a frame has been read it
 * carries one entry per region — blank regions included — so a frame that read
 * as nothing is non-empty and only `Strip` knows to say so.
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
  const grouped = byCaption(data.frames);
  // Groups that actually read as something: a frame still waiting to be read,
  // and one that read as nothing, are each their own group but neither is a
  // message the strip showed.
  const read = grouped.filter((group) => group.key).length;
  // Frames that were read and caught the strip mid-change are dropped from the
  // grid -- see `readAsNothing`. Filtered *here* rather than in `byCaption`,
  // which still has to see them: a blank breaks a run of one caption, so
  // grouping through them is what keeps two showings of one message either
  // side of a gap counted as two rather than bridged into one.
  //
  // A frame still waiting for its caption is kept, which is what keeps the
  // pictures arriving one per frame while a pass is playing; only once the
  // captions land does the blank half of the grid go away.
  const groups = grouped.filter(
    (group) => !group.frames.every((frame) => readAsNothing(frame)),
  );
  const blanks = grouped.length - groups.length;
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
          ) : data.recovering ? (
            // The third thing, and the one that fills in what the stills
            // missed: the window's own clip being read back frame by frame.
            // Shown because it is where the rest of a window's messages come
            // from, and they arrive seconds after the window closed.
            <Badge variant="secondary">
              <Film className="size-3 animate-pulse" />
              recovering from the clip
            </Badge>
          ) : data.cycle != null ? (
            <Badge variant="outline">pass finished</Badge>
          ) : null}
          {data.strip ? (
            <Badge variant="outline" className="font-normal">
              {STRIP[data.strip] ?? data.strip}
            </Badge>
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
                {data.spins_without_pay === 1 ? "spin has" : "spins have"} paid nothing
                since tracking started. Those are captured too — once a spin is over the
                strip cycles “GAME OVER”, “GAME PAYS 0” and “PLAY 880 CREDITS” until the
                next spin clears it.
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
              {/* Counted, not shown. The frames themselves are dropped from
                  the grid below -- a screenshot of an empty band says nothing
                  -- but silently rendering four tiles under "Frames 7" makes
                  the difference look like frames that went missing, which is
                  the one thing this card exists to make visible. */}
              {blanks > 0 ? (
                <div className="flex gap-1">
                  <dt>Mid-change</dt>
                  <dd
                    className="text-foreground font-mono"
                    title="Frames that caught the gap the strip leaves between two messages, so they carry no caption"
                  >
                    {blanks}
                  </dd>
                </div>
              ) : null}
              {/* Only while there is one: 0 for the whole pass, jumps to
                  `frame_count` the moment it closes, then counts down as the
                  post-pass read works through them. */}
              {data.queue_depth > 0 ? (
                <div className="flex gap-1">
                  <dt>Left to read</dt>
                  <dd className="text-foreground font-mono">{data.queue_depth}</dd>
                </div>
              ) : null}
              {/* Clips filed and not yet read back. Non-zero while a window is
                  open is normal — recovery gives way to capture — and it is
                  what says the list below is still filling in. */}
              {data.recovery_pending > 0 ? (
                <div className="flex gap-1">
                  <dt>Clips to recover</dt>
                  <dd className="text-foreground font-mono">{data.recovery_pending}</dd>
                </div>
              ) : null}
            </dl>

            {/* Shown beside a finished pass too, not only on the empty view:
                without it a run that has been losing for ten minutes looks
                like one frozen on its last win. Only over a *win* window,
                though — "the most recent win" over the between-spins strip's
                own frames was describing the wrong thing, and that strip is
                what a losing spin produces, so the two were being shown
                together at exactly the wrong moment. */}
            {!data.capturing &&
            data.spins_without_pay > 0 &&
            (data.strip === "win-video" || data.strip === "win-then-idle") ? (
              <p className="text-muted-foreground text-xs">
                {data.spins_without_pay}{" "}
                {data.spins_without_pay === 1 ? "spin has" : "spins have"} paid nothing
                since this one. The frames below are the most recent win.
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
                {blanks > 0
                  ? "Every frame of this pass caught the strip between two messages. " +
                    "Read the messages off the clip below."
                  : "The presentation has opened; the first frame is being taken."}
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
