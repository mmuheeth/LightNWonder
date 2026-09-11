import { ChevronDown, Cpu, Loader2, ScanEye, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";

/** The networks a screen can be read by, and the order they are offered in. */
const ARCHITECTURES = [
  { name: "resnet34", label: "ResNet34" },
  { name: "efficientnet_b0", label: "EfficientNet-B0" },
];

/** What fixes a refused reading; the backend's own message says what happened. */
const ERROR_HINTS = {
  CLASSIFIER_UNTRAINED: "Train a model on the Image Classifier page, then try again.",
  CLASSIFIER_UNAVAILABLE: "PyTorch is not installed on this machine.",
  GRID_NOT_CONFIGURED:
    "The active game's config declares no 'reel_bounds', so there is no grid to cut.",
  SCREEN_EVALUATION_FAILED:
    "Check that OBS is running and its window-capture source is pointed at the game.",
  OBS_NOT_CONNECTED: "Connect to OBS on the Dashboard, then try again.",
  // The backend very likely finished anyway: this request is one long call with
  // no progress to stream, so the browser giving up says nothing about the
  // reading. Its result is in the backend log either way.
  TIMEOUT:
    "The reading took longer than the browser waits. It may still have finished — check the backend log, and try again.",
};

function formatDuration(ms) {
  if (typeof ms !== "number" || ms <= 0) return "0.0s";
  return `${(ms / 1000).toFixed(1)}s`;
}

/**
 * Everything that went wrong, when something did. Shown even beside a successful
 * reading: a grid that read and a meter that did not is a 200, and the missing
 * half has to say why rather than simply be absent.
 */
function Problems({ errors }) {
  if (!errors?.length) return null;

  return (
    <ul className="space-y-1 border-t pt-4">
      {errors.map((error) => (
        <li
          key={error}
          className="flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-500"
        >
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
          {error}
        </li>
      ))}
    </ul>
  );
}

/** The screen that was read, and what it cost to read it. */
function Source({ result }) {
  const { source } = result;

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t pt-4">
      <Badge variant="secondary" className="font-mono text-[0.65rem]">
        {formatDuration(result.duration_ms)}
      </Badge>
      <Badge variant="outline" className="font-mono text-[0.65rem]">
        {source.width}&times;{source.height}
      </Badge>
      {source.letterboxed ? (
        <Badge
          variant="outline"
          className="font-mono text-[0.65rem]"
          title={`The game fills [${source.content_box.join(", ")}] of this frame; every region is a fraction of that box, not of the canvas`}
        >
          letterboxed
        </Badge>
      ) : null}
      <span className="text-muted-foreground/70 font-mono text-[0.6rem] break-all">
        {source.captured ? "captured" : "read"} {source.file_name}
      </span>
    </div>
  );
}

/** The button, and what the last press made of the screen. */
export function ScreenControlCard({ result, analyze }) {
  // Which network names the tiles. A per-press choice rather than a setting, for
  // the reason Analyze Spin makes it one: both stay trained at once and they do
  // not read the same split equally well, so a disagreement between them says
  // something about the grid.
  const [architecture, setArchitecture] = useState(ARCHITECTURES[0].name);
  const busy = analyze.isPending;
  const hint = analyze.error ? ERROR_HINTS[analyze.error.code] : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ScanEye className="size-4" />
          Evaluate Screen
        </CardTitle>
        <CardDescription>
          Read the screen as it is now — no spin is pressed. The symbols come from the
          trained classifier, the figures on the scatters and the cash meter from
          PaddleOCR
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-4">
          <Button
            size="sm"
            onClick={() => analyze.mutate({ architecture })}
            disabled={busy}
          >
            {busy ? <Loader2 className="animate-spin" /> : <ScanEye />}
            {busy ? "Analyzing…" : "Analyze Screen"}
          </Button>

          <div className="flex items-center gap-2">
            <Label
              htmlFor="evaluate-screen-architecture"
              className="text-muted-foreground text-sm font-normal"
            >
              <Cpu className="size-3.5" />
              Classifier
            </Label>
            <div className="relative">
              <select
                id="evaluate-screen-architecture"
                aria-label="Classifier model"
                value={architecture}
                onChange={(event) => setArchitecture(event.target.value)}
                disabled={busy}
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
        </div>

        {/* Said while it runs, because the whole reading is one request with no
            progress to stream and it takes tens of seconds -- a button that has
            said "Analyzing…" for a minute otherwise reads as a hang. */}
        {busy ? (
          <p className="text-muted-foreground text-xs">
            Reading the grid, then each scatter and each meter cell with PaddleOCR. This
            takes up to a minute or so — longer on the first run of the backend, which
            also loads the OCR models.
          </p>
        ) : null}

        {/* A refused request, which is different from a reading that partly
            failed: that one is a 200 and arrives in `result.errors` below. */}
        {analyze.error ? (
          <div className="space-y-1">
            <ApiErrorAlert error={analyze.error} />
            {hint ? <p className="text-muted-foreground text-xs">{hint}</p> : null}
          </div>
        ) : null}

        {result ? (
          <>
            <Source result={result} />
            <Problems errors={result.errors} />
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
