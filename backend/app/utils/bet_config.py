"""Readers for ``betPerUnitConfig.xml`` and ``betUnitConfig.xml``: what a spin costs,
and what multiplies a paytable's line values."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

__all__ = ["BetConfigError", "load_bet_ladder", "load_unit_costs"]


class BetConfigError(ValueError):
    """A bet configuration file is missing, is not XML, or has an unusable shape."""


def _local(tag: object) -> str:
    """Element tag without its ``{namespace}`` prefix."""
    return str(tag).rpartition("}")[2]


def _find(root: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    """Every element with this local name, at any depth."""
    return [element for element in root.iter() if _local(element.tag) == name]


def _parse(path: Path, *, what: str, root_tag: str) -> ElementTree.Element:
    """Read and parse one file, with every failure named as a config error."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise BetConfigError(f"No {what} file at {path}") from exc
    except OSError as exc:
        raise BetConfigError(
            f"Could not read the {what} file at {path}: {exc}"
        ) from exc

    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise BetConfigError(
            f"The {what} file at {path} is not valid XML: {exc}"
        ) from exc

    if _local(root.tag) != root_tag:
        raise BetConfigError(
            f"The {what} file at {path} is a <{_local(root.tag)}>, not a <{root_tag}>"
        )
    return root


def load_bet_ladder(path: Path) -> tuple[int, ...]:
    """The rungs a player may stake on each bet unit, in the file's own order."""
    root = _parse(path, what="bet per unit", root_tag="BetPerUnitData")
    mapping = next(iter(_find(root, "BetPerUnitMappings")), None)
    if mapping is None:
        raise BetConfigError(f"{path} declares no <BetPerUnitMappings>")

    rungs: list[int] = []
    for element in _find(mapping, "BetPerUnit"):
        raw = (element.text or "").strip()
        if not raw:
            continue
        try:
            rungs.append(int(raw))
        except ValueError as exc:
            # Dropping it silently would shorten the ladder, which is what a
            # bet read off the meter is checked against.
            raise BetConfigError(
                f"a <BetPerUnit> in {path} is not a whole number: {raw!r}"
            ) from exc

    if not rungs:
        raise BetConfigError(f"{path} declares no rungs")
    return tuple(rungs)


def load_unit_costs(path: Path) -> tuple[tuple[int | None, int], ...]:
    """``(units, cost)`` for each block: what a spin costs at one credit a unit."""
    root = _parse(path, what="bet unit", root_tag="BetUnitData")

    costs: list[tuple[int | None, int]] = []
    for block in _find(root, "UnitConfiguration"):
        data = next(iter(_find(block, "UnitSelectData")), None)
        if data is None:
            continue
        raw = data.get("cost")
        if raw is None:
            continue
        try:
            costs.append((_units_of(block), int(raw.strip())))
        except ValueError as exc:
            raise BetConfigError(
                f"a <UnitSelectData> in {path} has a non-numeric cost {raw!r}"
            ) from exc

    if not costs:
        raise BetConfigError(f"{path} declares no unit cost")
    return tuple(costs)


def _units_of(block: ElementTree.Element) -> int | None:
    """``numUnits`` as a number, or ``None`` when it is absent or unusable."""
    raw = block.get("numUnits")
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None
