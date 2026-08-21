"""Check the meter extractor against a folder of saved strip crops.

A measurement harness, not a feature. It runs the *real* extractor --
:mod:`app.services.meter` over :mod:`app.utils.meter` -- across every crop in a
folder and prints what each one read, so "does this still work" is a command
rather than a click through the dashboard twenty times.

Deliberately thin. An earlier version of this script carried its own copy of the
locating and reading logic, which is exactly how a harness starts agreeing with
itself and disagreeing with the thing it is supposed to be checking. Everything
below is argument parsing and a table.

Run it from ``backend/``::

    python scripts/meter_probe.py                       # table + CSV
    python scripts/meter_probe.py --dir ../some/folder
    python scripts/meter_probe.py --file screenshot-1787208401603.png

The band is fitted per ``(game, size)`` and cached by the service, and this passes
the file's own name as the game so **every crop is fitted independently**. That is
the harsher test: in the dashboard the band is fitted once per skin and reused, so
a strip that only reads correctly under a band fitted from a different strip shows
up here as a failure rather than hiding behind the cache.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from PIL import Image

# Run from backend/, like every other entry point here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.ocr import candidate_executables, discover_executable
from app.core.config import settings
from app.schemas.meter import MeterValues
from app.services import meter as meter_service

# Below this a value is worth checking against the crop rather than trusting.
# Every value verified correct on this project's strips scored 79 or better.
CONFIDENCE_FLOOR = 75.0

COLUMNS = (
    "file",
    "size",
    "mode",
    "currency",
    "cash",
    "credits",
    "win",
    "bet",
    "cash_conf",
    "win_conf",
    "bet_conf",
    "band",
    "unmapped",
    "engine_calls",
    "duration_ms",
    "error",
)


def row(path: Path, values: MeterValues) -> dict[str, object]:
    """One reading, flattened for the table and the CSV."""
    confidence = {name: field.confidence for name, field in values.fields.items()}
    with Image.open(path) as image:
        size = f"{image.width}x{image.height}"
    return {
        "file": path.name,
        "size": size,
        "mode": values.mode.value,
        "currency": values.currency or "",
        "cash": values.cash if values.cash is not None else "",
        "credits": values.credits if values.credits is not None else "",
        "win": values.win if values.win is not None else "",
        "bet": values.bet if values.bet is not None else "",
        "cash_conf": confidence.get("cash", 0.0),
        "win_conf": confidence.get("win", 0.0),
        "bet_conf": confidence.get("bet", 0.0),
        "band": "-".join(str(edge) for edge in values.band),
        "unmapped": " ".join(
            f"{item.value}@{item.centre:.2f}" for item in values.unmapped
        ),
        "engine_calls": values.engine_calls,
        "duration_ms": values.duration_ms,
        "error": values.error or "",
    }


def suspect(values: MeterValues) -> bool:
    """Whether this reading wants looking at rather than trusting."""
    if values.error is not None or values.unmapped:
        return True
    read = [f.confidence for f in values.fields.values() if f.value is not None]
    return not read or min(read) < CONFIDENCE_FLOOR


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        type=Path,
        default=settings.obs_capture_dir / "cash-meter",
        help="Folder of strip crops (default: the saved cash-meter crops).",
    )
    parser.add_argument("--file", help="Read only this file inside --dir.")
    parser.add_argument("--csv", type=Path, help="Where to write the table as CSV.")
    args = parser.parse_args()

    if discover_executable() is None and settings.OCR_TESSERACT_CMD is None:
        looked = ", ".join(str(path) for path in candidate_executables())
        print(f"No Tesseract executable found. Looked in: {looked or 'PATH'}")
        print("Install it, or set OCR_TESSERACT_CMD.")
        return 1
    print(f"engine  {settings.ocr_tesseract_cmd}")

    paths = [args.dir / args.file] if args.file else sorted(args.dir.glob("*.png"))
    paths = [path for path in paths if path.is_file()]
    if not paths:
        print(f"No .png crops in {args.dir}")
        return 1
    print(f"reading {len(paths)} crop(s) from {args.dir}\n")

    header = (
        f"{'file':32s} {'size':8s} {'mode':8s} {'cur':4s} "
        f"{'cash':>10s} {'credits':>8s} {'win':>8s} {'bet':>8s}  "
        f"{'band':>7s}  conf c/w/b"
    )
    print(header)
    print("-" * len(header))

    rows: list[dict[str, object]] = []
    flagged: list[dict[str, object]] = []
    for path in paths:
        with Image.open(path) as image:
            # The file name as the game, so each crop is fitted on its own merits
            # rather than inheriting a band fitted from the one before it.
            values = meter_service.read(image.convert("RGB"), game=path.name)
        entry = row(path, values)
        rows.append(entry)
        mark = "!" if suspect(values) else " "
        if mark == "!":
            flagged.append(entry)
        print(
            f"{mark}{entry['file']:31s} {entry['size']:8s} {entry['mode']:8s} "
            f"{entry['currency'] or '-':4s} "
            f"{entry['cash']!s:>10s} {entry['credits']!s:>8s} "
            f"{entry['win']!s:>8s} {entry['bet']!s:>8s}  "
            f"{entry['band']:>7s}  "
            f"{entry['cash_conf']:>4.0f} {entry['win_conf']:>4.0f} "
            f"{entry['bet_conf']:>4.0f}"
            + (f"  UNMAPPED {entry['unmapped']}" if entry["unmapped"] else "")
            + (f"  ERROR {entry['error']}" if entry["error"] else "")
        )

    print(f"\n{len(rows)} crop(s) read, {len(flagged)} worth checking")
    destination = args.csv or (args.dir / "meter_probe.csv")
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
