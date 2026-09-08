"""Reader for ``winGeometry.xml``: where each payline runs across the reels."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

__all__ = [
    "Payline",
    "PaylineSet",
    "WinGeometry",
    "WinGeometryError",
    "load_win_geometry",
]


class WinGeometryError(ValueError):
    """The geometry file is missing, is not XML, or has an unusable shape."""


def _local(tag: object) -> str:
    """Element tag without its ``{namespace}`` prefix."""
    return str(tag).rpartition("}")[2]


def _children(parent: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    """Direct children with this local name, in document order."""
    return [child for child in parent if _local(child.tag) == name]


def _int_attr(element: ElementTree.Element, name: str, *, where: str) -> int:
    """A required integer attribute; a missing or non-numeric one is fatal."""
    raw = element.get(name)
    if raw is None:
        raise WinGeometryError(f"{where} has no {name} attribute")
    try:
        return int(raw)
    except ValueError as exc:
        raise WinGeometryError(
            f"{where} has a non-numeric {name} attribute {raw!r}"
        ) from exc


@dataclass(frozen=True)
class Payline:
    """One line: which row it takes on each reel, left to right."""

    number: int
    """``paylineNumber`` as written -- 0-indexed, so line 1 is number 0."""

    elements: tuple[tuple[int, int], ...]
    """``(reel_index, position)`` pairs, both 0-indexed, in reel order."""

    @property
    def label(self) -> int:
        """The line as a player counts it: 1-indexed."""
        return self.number + 1

    def grid(self) -> tuple[tuple[int, int], ...]:
        """The same line in the game config's ``[row, column]``, 1-indexed form,
        so a config block and this file can be compared directly."""
        return tuple((position + 1, reel + 1) for reel, position in self.elements)


@dataclass(frozen=True)
class PaylineSet:
    """Every line of one bet configuration, e.g. the 40-line set."""

    payline_set_id: str
    """``paylineSetID``, which is the line count as a string for every game so
    far, and is what a paytable's ``NumberOfLines`` selects."""

    paylines: tuple[Payline, ...]

    @property
    def line_count(self) -> int:
        """How many lines the set actually declares."""
        return len(self.paylines)


@dataclass(frozen=True)
class WinGeometry:
    """One parsed ``winGeometry.xml``."""

    path: Path
    sets: tuple[PaylineSet, ...]

    def set_for(self, payline_set_id: str | None) -> PaylineSet | None:
        """Look one set up by id. Numeric ids are compared as numbers, so
        ``"40"`` and ``"040"`` are the same set."""
        if payline_set_id is None:
            return None
        wanted = payline_set_id.strip()
        for candidate in self.sets:
            if candidate.payline_set_id == wanted:
                return candidate
        if wanted.isdigit():
            for candidate in self.sets:
                if candidate.payline_set_id.isdigit() and int(
                    candidate.payline_set_id
                ) == int(wanted):
                    return candidate
        return None


def load_win_geometry(path: Path) -> WinGeometry:
    """Read one ``winGeometry.xml``."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise WinGeometryError(f"No win geometry file at {path}") from exc
    except OSError as exc:
        raise WinGeometryError(
            f"Could not read the win geometry file at {path}: {exc}"
        ) from exc

    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise WinGeometryError(
            f"The win geometry file at {path} is not valid XML: {exc}"
        ) from exc

    if _local(root.tag) != "WinGeometryData":
        raise WinGeometryError(
            f"The win geometry file at {path} is a <{_local(root.tag)}>, "
            "not a <WinGeometryData>"
        )

    sets: list[PaylineSet] = []
    for container in _children(root, "PaylineSetList"):
        for group in _children(container, "PaylineSet"):
            set_id = group.get("paylineSetID")
            if set_id is None:
                raise WinGeometryError(
                    f"a <PaylineSet> in {path} has no paylineSetID attribute"
                )
            lines: list[Payline] = []
            for line in _children(group, "Payline"):
                where = f"a <Payline> of set {set_id} in {path}"
                number = _int_attr(line, "paylineNumber", where=where)
                elements = tuple(
                    (
                        _int_attr(element, "reelIndex", where=where),
                        _int_attr(element, "position", where=where),
                    )
                    for element in _children(line, "PaylineElement")
                )
                lines.append(Payline(number=number, elements=elements))
            # Document order is not line order in every file; sort so line 1 is
            # first on the page whatever the file did.
            lines.sort(key=lambda payline: payline.number)
            sets.append(PaylineSet(payline_set_id=set_id, paylines=tuple(lines)))

    if not sets:
        raise WinGeometryError(
            f"The win geometry file at {path} declares no payline set"
        )
    return WinGeometry(path=path, sets=tuple(sets))
