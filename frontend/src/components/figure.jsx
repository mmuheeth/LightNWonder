/**
 * One labelled number, laid out to be scanned as a grid rather than read.
 *
 * Lives here beside `stat-row.jsx` for the same reason it does: this is the app's
 * own convention, not a vendored primitive. `StatRow` is a label and a value on
 * one line, for a list of them down a card; `Figure` is a small block, for a row
 * of them across one.
 *
 * The same component is still copied locally into three cards under
 * `features/analyze-spin/` and `features/paylines/`. This is the shared one; new
 * code should import it rather than adding a fourth copy.
 */
export function Figure({ label, value, hint }) {
  return (
    <div className="space-y-0.5">
      <p className="text-muted-foreground text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
        {label}
      </p>
      <p className="font-mono text-sm font-medium tabular-nums">{value}</p>
      {hint ? <p className="text-muted-foreground text-[0.65rem]">{hint}</p> : null}
    </div>
  );
}

/** The bordered box a row of figures sits in. */
export function FigureGrid({ children, className }) {
  return (
    <div
      className={
        "border-border/60 bg-muted/20 grid grid-cols-2 gap-x-3 gap-y-4 rounded-lg border p-3 sm:grid-cols-3 " +
        (className ?? "")
      }
    >
      {children}
    </div>
  );
}
