import {
  ChevronDown,
  Circle,
  Coins,
  Cpu,
  Dices,
  Film,
  Play,
  Square,
  Wifi,
  WifiOff,
} from "lucide-react";
import { useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { StatRow } from "@/components/stat-row";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { spinFrameUrl } from "@/features/analyze-spin/api";
import { useBetConfig } from "@/features/analyze-spin/use-analyze-spin";
import { cn } from "@/lib/utils";

/** What fixes a refused start; the backend's own message says what happened. */
const ERROR_HINTS = {
  SPIN_ANALYSIS_UNAVAILABLE:
    "Start the game so it begins writing its log, then try again.",
  SPIN_ANALYSIS_ALREADY_RUNNING: "Wait for the run to finish, or cancel it.",
};

/**
 * The networks a spin can be graded by, and the order they are offered in.
 *
 * Hardcoded rather than fetched. The backend's own architecture type is this same
 * closed pair, `/start` refuses an unknown name with a 400, and reaching into the
 * image-classifier slice for two strings would mean this slice no longer deletes
 * in one directory. A third network is a line here and a line there.
 *
 * ResNet34 leads because it is the one that reads a real split better — on the
 * reference fifteen tiles it names every one correctly — and because the first
 * entry is both what the dropdown opens on and what the backend would choose for
 * a request naming none (`CLASSIFIER_ARCHITECTURE`), so the visible default and
 * the configured one cannot drift apart.
 */
const ARCHITECTURES = [
  { name: "resnet34", label: "ResNet34" },
  { name: "efficientnet_b0", label: "EfficientNet-B0" },
];

const RUN_STATE_LOOKS = {
  running: { variant: "destructive", label: "running" },
  completed: { variant: "secondary", label: "completed" },
  failed: { variant: "destructive", label: "failed" },
  cancelled: { variant: "outline", label: "cancelled" },
};

function formatDuration(ms) {
  if (typeof ms !== "number" || ms <= 0) return "0.0s";
  return `${(ms / 1000).toFixed(1)}s`;
}

/** The two or three screenshots the spin took, in the order it took them. */
function Frames({ frames }) {
  if (frames.length === 0) return null;

  return (
    <div className="space-y-2 border-t pt-4">
      <h3 className="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.16em] uppercase">
        {frames.length} screenshot{frames.length === 1 ? "" : "s"}
      </h3>
      <div className="grid gap-3 sm:grid-cols-3">
        {frames.map((frame) => (
          <figure key={frame.key} className="space-y-1">
            <img
              src={spinFrameUrl(frame.file_name)}
              alt={frame.label}
              loading="lazy"
              className={cn(
                "bg-muted w-full rounded-md border",
                frame.blank && "border-destructive",
              )}
            />
            <figcaption className="text-muted-foreground text-[0.65rem]">
              {frame.label}
              {frame.attempts > 1 ? (
                <span className="text-muted-foreground/70">
                  {" "}
                  · {frame.attempts} tries
                </span>
              ) : null}
            </figcaption>
            {/* Said here as well as on the step, because this is where a reader
                can see that it is true. */}
            {frame.blank ? (
              <p className="text-destructive text-[0.65rem]">
                OBS captured nothing — every reading off this frame is meaningless
              </p>
            ) : null}
          </figure>
        ))}
      </div>
    </div>
  );
}

/**
 * The button, and the run it started.
 *
 * One press drives the whole sequence, so there is one button — everything else
 * on this card is the answer to "what is it doing now". `connected` is shown
 * because progress arrives on a socket: a page that quietly stopped updating
 * looks exactly like a spin that quietly stalled, and only this tells them
 * apart.
 */
export function SpinControlCard({ run, active, connected, start, cancel }) {
  // Off by default: a spin is not recorded unless this run's own toggle says
  // so, and that choice is only asked for at the moment of pressing spin.
  const [record, setRecord] = useState(false);
  // Which network names the tiles of the reels, and so what every payline run is
  // counted from. A per-run choice rather than a setting because both networks
  // stay trained at once and they do not read the same split equally well — two
  // of them agreeing about a spin is worth more than one being confident, and a
  // disagreement says something about the reels.
  const [architecture, setArchitecture] = useState(ARCHITECTURES[0].name);
  // What the cabinet's bet button is set to. Left blank normally: the run works
  // it out from the BET cell it reads anyway, dividing the total bet by what the
  // paytable says one spin costs. This is the override for a meter that will not
  // OCR, which is why it opens on "from the bet" rather than on a rung.
  const [betPerUnit, setBetPerUnit] = useState("");
  const betConfig = useBetConfig();
  const ladder = betConfig.data?.ladder ?? [];
  const unitCost = betConfig.data?.unit_cost ?? null;
  const busy = start.isPending || cancel.isPending;
  const actionError = start.error ?? cancel.error;
  const hint = actionError ? ERROR_HINTS[actionError.code] : null;
  const look = run ? (RUN_STATE_LOOKS[run.state] ?? RUN_STATE_LOOKS.running) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Dices className="size-4" />
          Analyze Spin
        </CardTitle>
        <CardDescription>
          Spin once, and validate the meter and the paylines against the maths the game
          has loaded
        </CardDescription>
        <CardAction>
          <Badge
            variant="outline"
            className={cn(
              "font-mono text-[0.65rem]",
              connected ? "text-muted-foreground" : "text-destructive",
            )}
            title={
              connected
                ? "Following the run over its progress stream"
                : "The progress stream is not connected; reconnecting"
            }
          >
            {connected ? <Wifi /> : <WifiOff />}
            {connected ? "live" : "offline"}
          </Badge>
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              onClick={() => {
                cancel.reset();
                start.mutate({
                  record,
                  architecture,
                  betPerUnit: betPerUnit ? Number(betPerUnit) : undefined,
                });
              }}
              disabled={active || busy}
            >
              <Play className="fill-current" />
              Initiate Spin
            </Button>
            {active ? (
              <Button
                variant="destructive"
                size="sm"
                onClick={() => cancel.mutate()}
                disabled={busy}
              >
                <Square />
                Cancel
              </Button>
            ) : null}
          </div>

          {/* Between the button and the record toggle: the last thing decided
              before pressing spin, and the one that changes what the run
              *concludes* rather than what it captures. */}
          <div className="flex items-center gap-2">
            <Label
              htmlFor="analyze-spin-architecture"
              className="text-muted-foreground text-sm font-normal"
            >
              <Cpu className="size-3.5" />
              Classifier
            </Label>
            <div className="relative">
              <select
                id="analyze-spin-architecture"
                aria-label="Classifier model"
                value={architecture}
                onChange={(event) => setArchitecture(event.target.value)}
                disabled={active || busy}
                className="border-input bg-background text-foreground hover:bg-accent/50 focus-visible:border-ring focus-visible:ring-ring/50 h-8 cursor-pointer appearance-none rounded-md border pr-8 pl-2.5 text-sm shadow-xs transition-[color,box-shadow,background-color] outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-60 dark:bg-input/30"
              >
                {ARCHITECTURES.map((option) => (
                  <option
                    key={option.name}
                    value={option.name}
                    className="bg-background text-foreground"
                  >
                    {option.label}
                  </option>
                ))}
              </select>
              <ChevronDown className="text-muted-foreground pointer-events-none absolute inset-y-0 right-2 my-auto size-3.5" />
            </div>
          </div>

          {/* What the machine's bet is set to. Not a preference like the two
              beside it — it is the multiplier on every award, so a wrong one
              misprices the whole spin rather than measuring it differently.
              Which is why it defaults to reading the machine instead of
              defaulting to a rung: the run divides the BET cell by what a spin
              costs, and only a meter that will not OCR needs this set. */}
          <div className="flex items-center gap-2">
            <Label
              htmlFor="analyze-spin-bet-per-unit"
              className="text-muted-foreground text-sm font-normal"
            >
              <Coins className="size-3.5" />
              Bet / unit
            </Label>
            <div className="relative">
              {ladder.length > 0 ? (
                <select
                  id="analyze-spin-bet-per-unit"
                  aria-label="Credits staked on each bet unit"
                  value={betPerUnit}
                  onChange={(event) => setBetPerUnit(event.target.value)}
                  disabled={active || busy}
                  className="border-input bg-background text-foreground hover:bg-accent/50 focus-visible:border-ring focus-visible:ring-ring/50 h-8 cursor-pointer appearance-none rounded-md border pr-8 pl-2.5 text-sm shadow-xs transition-[color,box-shadow,background-color] outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-60 dark:bg-input/30"
                >
                  <option value="" className="bg-background text-foreground">
                    from the bet
                  </option>
                  {ladder.map((rung) => (
                    <option
                      key={rung}
                      value={rung}
                      className="bg-background text-foreground"
                    >
                      {unitCost ? `${rung} — bets ${rung * unitCost}` : rung}
                    </option>
                  ))}
                </select>
              ) : (
                /* No ladder to offer: the game is not installed on this
                   machine, or its paytable could not be read. A free number
                   rather than invented rungs, and the backend is the validator
                   either way. */
                <input
                  id="analyze-spin-bet-per-unit"
                  aria-label="Credits staked on each bet unit"
                  type="number"
                  min="1"
                  step="1"
                  placeholder="not set"
                  value={betPerUnit}
                  onChange={(event) => setBetPerUnit(event.target.value)}
                  disabled={active || busy}
                  className="border-input bg-background text-foreground focus-visible:border-ring focus-visible:ring-ring/50 h-8 w-24 rounded-md border px-2.5 text-sm shadow-xs transition-[color,box-shadow,background-color] outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-60 dark:bg-input/30"
                />
              )}
              {ladder.length > 0 ? (
                <ChevronDown className="text-muted-foreground pointer-events-none absolute inset-y-0 right-2 my-auto size-3.5" />
              ) : null}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Switch
              id="analyze-spin-record"
              checked={record}
              onCheckedChange={setRecord}
              disabled={active || busy}
            />
            <Label
              htmlFor="analyze-spin-record"
              className="text-muted-foreground text-sm font-normal"
            >
              <Film className="size-3.5" />
              Record video
            </Label>
          </div>
        </div>

        {actionError ? (
          <div className="space-y-1">
            <ApiErrorAlert error={actionError} />
            {hint ? <p className="text-muted-foreground text-xs">{hint}</p> : null}
          </div>
        ) : null}

        {run ? (
          <>
            <div className="space-y-3 border-t pt-4">
              <StatRow label="Run">
                {active ? (
                  <Badge variant="destructive">
                    <Circle className="size-2 animate-pulse fill-current" />
                    {run.message}
                  </Badge>
                ) : (
                  <Badge variant={look.variant}>{look.label}</Badge>
                )}
              </StatRow>
              {!active ? (
                <StatRow label="Result">
                  <span className="text-sm">{run.message}</span>
                </StatRow>
              ) : null}
              <StatRow label="Spin">
                {run.outcome === "win" ? (
                  <Badge className="border-emerald-500/30 bg-emerald-500/15 text-emerald-700 dark:text-emerald-400">
                    won
                  </Badge>
                ) : run.outcome === "no-win" ? (
                  <Badge variant="outline" className="text-muted-foreground">
                    paid nothing
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-muted-foreground">
                    unknown
                  </Badge>
                )}
              </StatRow>
              <StatRow label="Game">{run.label}</StatRow>
              <StatRow label="Elapsed">{formatDuration(run.duration_ms)}</StatRow>
              <StatRow label="Id">
                <span className="font-mono text-xs">{run.run_id}</span>
              </StatRow>
              {run.recording?.output_path ? (
                <StatRow label="Recording">
                  <span className="text-muted-foreground inline-flex items-center gap-1.5 font-mono text-[0.7rem] break-all">
                    <Film className="size-3.5 shrink-0" />
                    {run.recording.output_path}
                  </span>
                </StatRow>
              ) : null}
            </div>

            <Frames frames={run.frames ?? []} />

            {/* The failures a step already named, gathered once: a run that
                went wrong in two places should say so without the reader
                having to scan thirteen steps for the red ones. */}
            {run.errors?.length > 0 ? (
              <ul className="space-y-1 border-t pt-4">
                {run.errors.map((message) => (
                  <li key={message} className="text-destructive text-xs break-words">
                    {message}
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        ) : (
          <p className="text-muted-foreground text-sm">
            No spin analysed yet. The game and OBS need to be running, and the i-deck
            panel open.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
