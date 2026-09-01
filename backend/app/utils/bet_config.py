"""Readers for ``betPerUnitConfig.xml`` and ``betUnitConfig.xml``: what a spin
costs, and what multiplies a paytable's line values.

These two files sit beside ``math.xml`` in every paytable folder and answer the
question neither ``math.xml`` nor ``gameConfig.cfg`` answers directly: a payline
combo's ``<Value>`` is a rate *per bet unit*, not an award in credits, so a line
reading "pays 25" pays 25 at one credit a unit and 250 at ten.

Between them::

    total_bet_credits = UnitSelectData/@cost  x  BetPerUnit

For FortuneOx's 40-line paytables that is ``88 x {1, 2, 3, 5, 10}``, which is
exactly the ``88 176 264 440 880`` that ``gameConfig.cfg`` lists as
``SpecificMaxBets`` and ``math.xml`` carries as its ``AllowedBetsTbl`` -- three
statements of one ladder, of which this is the only one that separates the two
factors. The 20-line paytables are ``50 x`` the same rungs.

**Tags pair the two files.** A ``BetPerUnitMappings tag="1"`` goes with a
``UnitSelectMappings tag="1"``; ``betPerUnitConfig.xml``'s own comment records
that an *untagged* mapping is the default, which is the shape HuffNPuffLink
ships and FortuneOx does not. So neither lookup here demands a tag, and both
fall back the same way -- see :meth:`BetPerUnitConfig.ladder`.

Neither file carries a namespace, but both are read by local name so every
reader in ``app/utils`` behaves the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

__all__ = [
    "BetConfigError",
    "BetPerUnitConfig",
    "BetPerUnitMapping",
    "BetUnitConfig",
    "BetUnitSelection",
    "UnitConfiguration",
    "load_bet_per_unit_config",
    "load_bet_unit_config",
]


class BetConfigError(ValueError):
    """A bet configuration file is missing, is not XML, or has an unusable shape."""


def _local(tag: object) -> str:
    """Element tag without its ``{namespace}`` prefix."""
    return str(tag).rpartition("}")[2]


def _children(parent: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    """Direct children with this local name, in document order."""
    return [child for child in parent if _local(child.tag) == name]


def _descendants(root: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    """Every element with this local name at any depth, in document order.

    Used for the two list wrappers, whose nesting differs between games by a
    level of indentation that carries no meaning -- FortuneOx and HuffNPuffLink
    write the same structure with different whitespace and, in one case, an
    extra container. Matching on the leaf keeps a cosmetic difference from
    reading as a missing block.
    """
    return [element for element in root.iter() if _local(element.tag) == name]


def _int_text(element: ElementTree.Element, name: str, *, where: str) -> int | None:
    """An optional integer child element. Absent is fine; non-numeric is not.

    The text is stripped before parsing: these files indent some values the way
    ``gameConfig.cfg`` writes ``<MinDenomMultiplier> 1 </...>``.
    """
    child = next(iter(_children(element, name)), None)
    if child is None or child.text is None or not child.text.strip():
        return None
    raw = child.text.strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise BetConfigError(f"{where} has a non-numeric <{name}> {raw!r}") from exc


def _int_attr(element: ElementTree.Element, name: str, *, where: str) -> int | None:
    """An optional integer attribute. Absent is fine; non-numeric is not."""
    raw = element.get(name)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise BetConfigError(
            f"{where} has a non-numeric {name} attribute {raw!r}"
        ) from exc


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


# --- betPerUnitConfig.xml -------------------------------------------------


@dataclass(frozen=True)
class BetPerUnitMapping:
    """One selectable ladder of bets per unit."""

    tag: str | None
    """``tag`` as written, or ``None`` for the untagged default."""

    values: tuple[int, ...]
    """The rungs, in the order the file lists them."""


@dataclass(frozen=True)
class BetPerUnitConfig:
    """One parsed ``betPerUnitConfig.xml``."""

    path: Path
    minimum: int | None
    maximum: int | None
    """``MaxBetPerUnit`` -- the file's own comment calls it the highest value
    across all mappings, so it is not necessarily the last rung of any one."""

    mappings: tuple[BetPerUnitMapping, ...]

    def mapping(self, tag: str | None = None) -> BetPerUnitMapping | None:
        """The ladder a tag selects.

        Three steps, because the two shipped games write this differently: the
        named tag when one matches, then the untagged default the file's own
        comment describes, then the only mapping there is. A game with one
        scheme should not have to know its tag to be read.
        """
        if tag is not None:
            for candidate in self.mappings:
                if candidate.tag == tag:
                    return candidate
        for candidate in self.mappings:
            if candidate.tag is None:
                return candidate
        return self.mappings[0] if len(self.mappings) == 1 else None

    def ladder(self, tag: str | None = None) -> tuple[int, ...]:
        """The rungs a tag selects, or empty when nothing matched."""
        found = self.mapping(tag)
        return () if found is None else found.values


def load_bet_per_unit_config(path: Path) -> BetPerUnitConfig:
    """Read one ``betPerUnitConfig.xml``."""
    root = _parse(path, what="bet per unit", root_tag="BetPerUnitData")

    minimum: int | None = None
    maximum: int | None = None
    mappings: list[BetPerUnitMapping] = []
    for configuration in _descendants(root, "BetPerUnitConfiguration"):
        where = f"a <BetPerUnitConfiguration> in {path}"
        # First one wins for the bounds: every shipped file declares exactly one
        # configuration, and a second would be a shape worth noticing rather
        # than silently averaging.
        if minimum is None:
            minimum = _int_text(configuration, "MinBetPerUnit", where=where)
        if maximum is None:
            maximum = _int_text(configuration, "MaxBetPerUnit", where=where)
        for mapping in _children(configuration, "BetPerUnitMappings"):
            values: list[int] = []
            for element in _children(mapping, "BetPerUnit"):
                raw = (element.text or "").strip()
                if not raw:
                    continue
                try:
                    values.append(int(raw))
                except ValueError as exc:
                    raise BetConfigError(
                        f"a <BetPerUnit> in {path} is not a whole number: {raw!r}"
                    ) from exc
            mappings.append(
                BetPerUnitMapping(tag=mapping.get("tag"), values=tuple(values))
            )

    if not mappings:
        raise BetConfigError(
            f"The bet per unit file at {path} declares no <BetPerUnitMappings>"
        )
    return BetPerUnitConfig(
        path=path, minimum=minimum, maximum=maximum, mappings=tuple(mappings)
    )


# --- betUnitConfig.xml ----------------------------------------------------


@dataclass(frozen=True)
class BetUnitSelection:
    """What one unit selection costs: ``units`` lines for ``cost`` credits."""

    tag: str | None
    units: int | None
    cost: int | None
    """Credits for the whole selection at one bet per unit -- the number a
    paytable value is multiplied by the bet per unit against."""


@dataclass(frozen=True)
class UnitConfiguration:
    """Every selection available at one number of units."""

    num_units: int | None
    selections: tuple[BetUnitSelection, ...]

    def selection(self, tag: str | None = None) -> BetUnitSelection | None:
        """The selection a tag names, falling back exactly as the ladder does."""
        if tag is not None:
            for candidate in self.selections:
                if candidate.tag == tag:
                    return candidate
        for candidate in self.selections:
            if candidate.tag is None:
                return candidate
        return self.selections[0] if len(self.selections) == 1 else None


@dataclass(frozen=True)
class BetUnitConfig:
    """One parsed ``betUnitConfig.xml``."""

    path: Path
    configurations: tuple[UnitConfiguration, ...]

    def configuration(self, units: int | None = None) -> UnitConfiguration | None:
        """The block for a number of units -- a paytable's own line count.

        Falls back to the only block there is, so a game shipping one
        configuration reads correctly even when the caller has no line count to
        offer.
        """
        if units is not None:
            for candidate in self.configurations:
                if candidate.num_units == units:
                    return candidate
        return self.configurations[0] if len(self.configurations) == 1 else None

    def cost(self, units: int | None = None, tag: str | None = None) -> int | None:
        """What a spin costs at one bet per unit, or ``None`` if nothing matched."""
        configuration = self.configuration(units)
        if configuration is None:
            return None
        found = configuration.selection(tag)
        return None if found is None else found.cost


def load_bet_unit_config(path: Path) -> BetUnitConfig:
    """Read one ``betUnitConfig.xml``."""
    root = _parse(path, what="bet unit", root_tag="BetUnitData")

    configurations: list[UnitConfiguration] = []
    for configuration in _descendants(root, "UnitConfiguration"):
        where = f"a <UnitConfiguration> in {path}"
        selections: list[BetUnitSelection] = []
        for mapping in _children(configuration, "UnitSelectMappings"):
            tag = mapping.get("tag")
            for data in _children(mapping, "UnitSelectData"):
                selections.append(
                    BetUnitSelection(
                        tag=tag,
                        units=_int_attr(data, "units", where=where),
                        cost=_int_attr(data, "cost", where=where),
                    )
                )
        configurations.append(
            UnitConfiguration(
                num_units=_int_attr(configuration, "numUnits", where=where),
                selections=tuple(selections),
            )
        )

    if not configurations:
        raise BetConfigError(
            f"The bet unit file at {path} declares no <UnitConfiguration>"
        )
    return BetUnitConfig(path=path, configurations=tuple(configurations))
