import { CircleCheck, CircleHelp, CircleX } from "lucide-react";

import { Badge } from "@/components/ui/badge";

/** A validation's verdict. */
const LOOKS = {
  passed: {
    Icon: CircleCheck,
    className:
      "border-emerald-500/30 bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
  },
  failed: {
    Icon: CircleX,
    className: "border-destructive/30 bg-destructive/15 text-destructive",
  },
  indeterminate: {
    Icon: CircleHelp,
    className: "text-muted-foreground",
  },
};

export function VerdictBadge({ verdict, children }) {
  const look = LOOKS[verdict] ?? LOOKS.indeterminate;
  const { Icon } = look;

  return (
    <Badge variant="outline" className={look.className}>
      <Icon />
      {children ?? verdict}
    </Badge>
  );
}
