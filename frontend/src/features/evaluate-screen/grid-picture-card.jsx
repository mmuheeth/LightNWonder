import { Grid3x3, TriangleAlert } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * The reels crop with a ring over every cell that was named. Its own card rather
 * than a picture inside the reading: it is the evidence *for* that table, and the
 * fastest check that the grid was cut where it looks like it was.
 *
 * Shared between Evaluate Screen and Scatter Value Validation -- both read the
 * same picture off the same pipeline, so the card that shows it is not one each.
 */
export function GridPictureCard({ image }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Grid3x3 className="size-4" />
          The grid that was read
        </CardTitle>
        <CardDescription>
          Cropped from roi.reels and cut into tiles, with a ring over every cell the
          classifier named — no codes drawn on it, since those are the table below
        </CardDescription>
      </CardHeader>
      <CardContent>
        <img
          src={image}
          alt="The reels crop, with the named cells ringed"
          className="bg-muted w-full rounded-md border"
        />
      </CardContent>
    </Card>
  );
}

/** Why there is no grid reading, when there is none. */
export function GridFailureCard({ error }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <TriangleAlert className="size-4 text-amber-600 dark:text-amber-500" />
          The grid could not be read
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-muted-foreground text-sm">{error}</p>
      </CardContent>
    </Card>
  );
}
