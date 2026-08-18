/**
 * One labelled value on a line, as every status card lays them out.
 *
 * Lives here rather than in `components/ui/` because it is this app's own
 * convention, not one of the vendored primitives.
 */
export function StatRow({ label, children }) {
  return (
    <div className="flex items-baseline justify-between gap-4 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{children}</span>
    </div>
  );
}
