import {
  Circle,
  CircleCheck,
  CircleMinus,
  CircleX,
  ChevronRight,
  LoaderCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * Every step wears its state, and the four non-running ones are visually
 * distinct on purpose: unreached, deliberately not run, done, and failed are
 * four different facts about a spin, and a timeline that only distinguished
 * done-from-not would make a skipped take-win look like a broken one.
 */
const LOOKS = {
  pending: {
    Icon: Circle,
    icon: "text-muted-foreground/40",
    text: "text-muted-foreground/60",
  },
  running: {
    Icon: LoaderCircle,
    icon: "text-primary animate-spin",
    text: "font-medium",
  },
  completed: { Icon: CircleCheck, icon: "text-emerald-500", text: "" },
  skipped: {
    Icon: CircleMinus,
    icon: "text-muted-foreground/50",
    text: "text-muted-foreground",
  },
  failed: {
    Icon: CircleX,
    icon: "text-destructive",
    text: "text-destructive font-medium",
  },
};

function formatDuration(ms) {
  if (typeof ms !== "number") return null;
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function formatTime(value) {
  if (!value) return null;
  const at = new Date(value);
  return Number.isNaN(at.getTime()) ? null : at.toLocaleTimeString();
}

function Step({ step }) {
  const look = LOOKS[step.state] ?? LOOKS.pending;
  const { Icon } = look;
  const duration = formatDuration(step.duration_ms);

  return (
    <li className="flex gap-3">
      <Icon className={cn("mt-0.5 size-4 shrink-0", look.icon)} />
      <div className="min-w-0 flex-1 space-y-0.5 pb-3">
        <div className="flex items-baseline gap-2">
          <span className={cn("text-sm", look.text)}>{step.label}</span>
          {duration ? (
            <span className="text-muted-foreground ml-auto shrink-0 font-mono text-[0.65rem] tabular-nums">
              {duration}
            </span>
          ) : null}
        </div>
        {/* The step's own account of what it did. Wrapped rather than truncated:
            these lines carry file names and the reason a wait ran out. */}
        {step.error ? (
          <p className="text-destructive text-xs break-words">
            {step.error}
            {step.error_code ? (
              <span className="text-muted-foreground font-mono">
                {" "}
                · {step.error_code}
              </span>
            ) : null}
          </p>
        ) : step.detail ? (
          <p className="text-muted-foreground text-xs break-words">{step.detail}</p>
        ) : null}
      </div>
    </li>
  );
}

/**
 * The sequence one spin goes through, live.
 *
 * The whole plan is on screen from the first frame — the backend sends every
 * step `pending` — so a run that dies on the press shows the eight things that
 * never happened rather than simply stopping. The game-log lines it read on the
 * way sit under a dropdown: they are what tells "the reels never stopped" from
 * "the reels stopped and a bonus took over", and noise otherwise.
 */
export function SpinTimeline({ run }) {
  const events = run.events ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sequence</CardTitle>
        <CardDescription>
          One spin, from the press to the two validations
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <ol className="space-y-0">
          {run.steps.map((step) => (
            <Step key={step.key} step={step} />
          ))}
        </ol>

        {events.length > 0 ? (
          <details className="group border-t pt-4">
            <summary className="text-muted-foreground hover:text-foreground flex cursor-pointer list-none items-center gap-1.5 text-xs select-none">
              <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
              {events.length} game log event{events.length === 1 ? "" : "s"} read
            </summary>
            <ul className="mt-3 space-y-1.5">
              {events
                .slice()
                .reverse()
                .map((event, index) => (
                  <li
                    key={`${event.event}-${event.at ?? index}-${index}`}
                    className="flex items-baseline gap-2 text-xs"
                  >
                    <Badge variant="secondary" className="shrink-0 font-mono">
                      {event.event}
                    </Badge>
                    <span className="text-muted-foreground truncate">
                      {event.summary}
                    </span>
                    <span className="text-muted-foreground/70 ml-auto shrink-0 font-mono text-[0.65rem] tabular-nums">
                      {formatTime(event.at)}
                    </span>
                  </li>
                ))}
            </ul>
          </details>
        ) : null}
      </CardContent>
    </Card>
  );
}
