/** The reels as a matrix, laid out the way they sit on screen. */
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
