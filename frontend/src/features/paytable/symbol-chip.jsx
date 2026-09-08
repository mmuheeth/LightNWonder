import { cn } from "@/lib/utils";

// Roles come from math.xml (what substitutes, what is counted anywhere), so a
// chip is coloured by what the symbol *does* rather than by what it is called.
// `ANY` is not a symbol at all — it is a combo's trailing wildcard — so it gets
// its own dashed treatment instead of being drawn as a regular code.
const ROLE_CLASSES = {
  wild: "border-primary/40 bg-primary/10 text-primary",
  scatter: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  regular: "border-border bg-muted/60 text-foreground",
  any: "border-dashed border-border bg-transparent text-muted-foreground",
};

/** One symbol code, sized to sit in a table cell or a run of five. */
export function SymbolChip({ code, name, role = "regular", className }) {
  const kind = code === "ANY" ? "any" : role;

  return (
    <span
      title={name ? `${code} — ${name}` : code}
      className={cn(
        "inline-flex h-6 min-w-8 items-center justify-center rounded border px-1.5 font-mono text-xs font-medium",
        ROLE_CLASSES[kind] ?? ROLE_CLASSES.regular,
        className,
      )}
    >
      {code}
    </span>
  );
}

/**
 * A combo's symbol list, left to right — which is the order it pays in.
 * @param {{symbols: string[], names?: Array<string|null>, roles?: Record<string,
 *   string>}} props
 */
export function SymbolRun({ symbols, names = [], roles = {} }) {
  return (
    <span className="flex flex-wrap items-center gap-1">
      {symbols.map((code, index) => (
        <SymbolChip
          key={`${code}-${index}`}
          code={code}
          name={names[index]}
          role={roles[code]}
        />
      ))}
    </span>
  );
}
