/**
 * The reels as a matrix, laid out the way they sit on screen.
 *
 * Rendered twice per result -- once in codes, once in display names -- because the
 * codes are what the rest of the system speaks (the maths files, the paylines
 * block, a spin's reel stops) and the names are what a person reads. Both
 * matrices come from the response rather than being derived here, so the two
 * cannot end up disagreeing about which cell is which.
 *
 * Same shape as the reel-stop grid a spin analysis shows, on purpose: that one is
 * read out of the game's own log and this one out of the picture, and the whole
 * value of the classifier is that the two can be put side by side. A dash is a
 * tile nothing cleared the confidence floor for, which is an answer, not a hole.
 */
export function SymbolGrid({ grid, title, mono = true }) {
  if (!grid?.length) return null;

  return (
    <div className="space-y-1.5">
      {title ? (
        <p className="text-muted-foreground text-[0.6rem] font-semibold tracking-[0.14em] uppercase">
          {title}
        </p>
      ) : null}
      <div className="border-border/60 inline-block overflow-hidden rounded-lg border">
        <table className="text-xs">
          <tbody>
            {grid.map((row, rowIndex) => (
              <tr key={rowIndex} className="divide-border/60 divide-x">
                {row.map((value, columnIndex) => (
                  <td
                    key={columnIndex}
                    className={[
                      "px-2.5 py-1.5 text-center whitespace-nowrap",
                      mono ? "font-mono" : "",
                      value ? "bg-muted/20" : "bg-muted/40 text-muted-foreground",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                  >
                    {value || "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
