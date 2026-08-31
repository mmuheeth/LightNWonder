"""Readers for the two files that identify and define one paytable folder.

A game ships its maths outside this repo, under ``<game_config>/<paytableId>/``,
and the game's own log names which of those folders is loaded (see the
``paytable-changed`` rule in :data:`app.utils.game_log.DEFAULT_RULES`). Two
files in that folder are worth reading:

``gameConfig.cfg``
    The folder's identity -- its ``GameId`` (byte-identical to the log's
    ``paytableId`` and to the folder name), the marketing ``DisplayGameId``,
    the return percentages, and ``NumberOfLines``, which is the line count the
    cabinet is configured for and therefore the payline set actually in play.

``math.xml``
    The maths itself: symbols, reel strips, and the combos that pay. Namespaced
    (``http://scientificgames.com/slotMathXMLSchema.xsd``) and close to a
    megabyte, so element lookup here is by local name -- pinning the namespace
    would make a schema-version bump look like a corrupt file.

Deliberately ignorant of who consumes it, like every other ``app/utils`` format
reader: nothing here reads the game's screen, its log, or its config JSON.
:mod:`app.services.paytable` is what joins the three.

Both files are written with a UTF-8 BOM, hence ``utf-8-sig`` throughout.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

__all__ = [
    "ANY_SYMBOL",
    "GameMath",
    "GameMathError",
    "MathDefaults",
    "PaylineCombo",
    "PaytableIdentity",
    "PaytableRef",
    "ReelStrip",
    "ReelStripSet",
    "ScatterCombo",
    "SymbolSet",
    "SymbolUsage",
    "WildSymbol",
    "load_game_math",
    "load_paytable_identity",
]

# Trailing wildcard in a combo's symbol list: "and anything after this".
ANY_SYMBOL = "ANY"


class GameMathError(ValueError):
    """A maths file is missing, is not XML, or has an unusable shape."""


# --- XML helpers ----------------------------------------------------------
#
# math.xml is namespaced and the other files are not; everything here looks up
# by local name so neither has to care.


def _local(tag: object) -> str:
    """Element tag without its ``{namespace}`` prefix."""
    return str(tag).rpartition("}")[2]


def _children(parent: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    """Direct children with this local name, in document order."""
    return [child for child in parent if _local(child.tag) == name]


def _child(parent: ElementTree.Element, name: str) -> ElementTree.Element | None:
    """First direct child with this local name, or ``None``."""
    return next(iter(_children(parent, name)), None)


def _text(parent: ElementTree.Element, name: str) -> str | None:
    """Stripped text of a direct child, or ``None`` when absent or empty.

    These files indent some values (``<MinDenomMultiplier> 1 </...>``), so the
    strip is not optional.
    """
    found = _child(parent, name)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _texts(parent: ElementTree.Element, name: str) -> tuple[str, ...]:
    """Stripped text of every direct child with this local name."""
    return tuple(
        child.text.strip()
        for child in _children(parent, name)
        if child.text and child.text.strip()
    )


def _int(parent: ElementTree.Element, name: str) -> int | None:
    """Integer value of a direct child. A non-numeric one is a broken file."""
    raw = _text(parent, name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise GameMathError(f"{name} must be a whole number, got {raw!r}") from exc


def _float(parent: ElementTree.Element, name: str) -> float | None:
    """Decimal value of a direct child. A non-numeric one is a broken file."""
    raw = _text(parent, name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise GameMathError(f"{name} must be a number, got {raw!r}") from exc


def _parse(path: Path, *, what: str) -> ElementTree.Element:
    """Read one of these files, translating every failure to one error type."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise GameMathError(f"No {what} at {path}") from exc
    except OSError as exc:
        raise GameMathError(f"Could not read the {what} at {path}: {exc}") from exc
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise GameMathError(f"The {what} at {path} is not valid XML: {exc}") from exc


# --- gameConfig.cfg -------------------------------------------------------


@dataclass(frozen=True)
class PaytableIdentity:
    """``gameConfig.cfg``: who this paytable folder is, in its own words."""

    path: Path

    game_type: str | None
    """Theme name, e.g. ``FortuneOx``."""

    game_id: str | None
    """The paytable id: the same string as the folder name and the log's
    ``paytableId``. Reading it back is what proves the folder resolved to the
    one the game actually loaded."""

    display_game_id: str | None
    """One-line summary the operator sees: lines, percentage, bet limits."""

    game_pct: float | None
    min_game_pct: float | None
    game_base_pct: float | None
    min_game_base_pct: float | None

    number_of_lines: int | None
    """Lines the cabinet plays. This -- not math.xml's default -- is the payline
    set in play, since a paytable folder is per line configuration."""

    min_total_bet: int | None

    min_denom_multiplier: int | None
    """``MinDenomMultiplier``: how many of the base unit one credit is worth on
    this folder -- 2 on a ``-2c-`` paytable. The only place the *amount* of a
    denomination is declared rather than logged, so it is what corroborates the
    value the log reported. See :mod:`app.utils.denomination`, which does the
    corroborating; nothing here interprets it."""

    max_bets: tuple[int, ...]
    """``SpecificMaxBets`` of the first denomination block that declares any."""

    denominations: tuple[float, ...]
    """``DenomConfig``'s own ``Denom`` entries. **Not the denominations the
    cabinet currently offers, and not guaranteed to contain the current one** --
    the ``-2c-`` folder lists 1, 5, 10, 50 and 100 while running at 2. Kept
    because it is what the file says; do not check a live denomination against
    it."""


def load_paytable_identity(path: Path) -> PaytableIdentity:
    """Read one ``gameConfig.cfg``."""
    root = _parse(path, what="game config")

    denominations: list[float] = []
    max_bets: tuple[int, ...] = ()
    for block in _children(root, "DenomConfig"):
        denom = _float(block, "Denom")
        if denom is not None:
            denominations.append(denom)
        raw_bets = _text(block, "SpecificMaxBets")
        if raw_bets and not max_bets:
            try:
                max_bets = tuple(int(part) for part in raw_bets.split())
            except ValueError as exc:
                raise GameMathError(
                    f"SpecificMaxBets in {path} must be whole numbers, got {raw_bets!r}"
                ) from exc

    return PaytableIdentity(
        path=path,
        game_type=_text(root, "GameType"),
        game_id=_text(root, "GameId"),
        display_game_id=_text(root, "DisplayGameId"),
        game_pct=_float(root, "GamePct"),
        min_game_pct=_float(root, "MinGamePct"),
        game_base_pct=_float(root, "GameBasePct"),
        min_game_base_pct=_float(root, "MinGameBasePct"),
        number_of_lines=_int(root, "NumberOfLines"),
        min_total_bet=_int(root, "MinTotalBet"),
        min_denom_multiplier=_int(root, "MinDenomMultiplier"),
        max_bets=max_bets,
        denominations=tuple(denominations),
    )


# --- math.xml -------------------------------------------------------------


@dataclass(frozen=True)
class WildSymbol:
    """One wild and what it stands in for."""

    code: str
    substitutes: tuple[str, ...]


@dataclass(frozen=True)
class SymbolSet:
    """Every symbol code a reel strip in this set may carry."""

    identifier: str
    symbols: tuple[str, ...]
    wilds: tuple[WildSymbol, ...]


@dataclass(frozen=True)
class ReelStrip:
    """One strip: the symbols around a reel, in stop order.

    ``weights`` runs parallel to ``symbols``. It is the *stop* weighting, not a
    pay value, and is usually uniform -- an unequal one is the interesting case.
    """

    identifier: str
    symbol_set_id: str | None
    symbols: tuple[str, ...]
    weights: tuple[int, ...]

    @property
    def length(self) -> int:
        """Number of stops on the strip."""
        return len(self.symbols)

    def counts(self) -> dict[str, int]:
        """How many stops each symbol occupies, in first-appearance order."""
        tally: dict[str, int] = {}
        for symbol in self.symbols:
            tally[symbol] = tally.get(symbol, 0) + 1
        return tally


@dataclass(frozen=True)
class ReelStripSet:
    """Which strips make up one set of reels, and how much of each is on screen."""

    identifier: str
    strip_ids: tuple[str, ...]

    visible_heights: tuple[int, ...]
    """Rows of each reel the player sees: ``(3, 3, 3, 3, 3)`` for a 5x3 game."""


@dataclass(frozen=True)
class PaylineCombo:
    """One paying pattern along a line, read left to right.

    ``ANY`` in :attr:`symbols` is the trailing wildcard, so a three-of-a-kind is
    stored as ``[X, X, X, ANY, ANY]``. The leading run is what pays, which is
    the same rule :mod:`app.services.paylines` reads a split by.
    """

    combo_set: str
    combo_id: int | None
    group: int | None
    value: float | None
    symbols: tuple[str, ...]

    @property
    def match_length(self) -> int:
        """Symbols that must match before the ``ANY`` tail begins."""
        return sum(1 for symbol in self.symbols if symbol != ANY_SYMBOL)


@dataclass(frozen=True)
class ScatterCombo:
    """A pay, or a bonus trigger, for a count of symbols anywhere on screen."""

    combo_set: str
    combo_id: int | None
    group: int | None
    value: float | None
    symbols: tuple[str, ...]
    min_symbols: int | None
    max_symbols: int | None

    base_multiplier: str | None
    """What ``value`` multiplies: ``TotalBet``, ``BetPerLine``, or nothing."""

    bonus_code: int | None


@dataclass(frozen=True)
class PaytableRef:
    """A named paytable: which combo sets are live when it is selected."""

    identifier: str
    combo_set_ids: tuple[str, ...]


@dataclass(frozen=True)
class SymbolUsage:
    """One symbol code as the *reels* have it, rather than as they declare it.

    ``SymbolSetList`` is a declaration and ``ReelStripList`` is the fact: on
    FortuneOx the former names eighteen codes and only seventeen of them are on
    a strip at all, so a table built from the declaration lists a symbol that
    can never land. These counts are what says which is which.
    """

    code: str
    reel_stops: int
    """Stops on the base game's own reels. 0 for a code that only exists in a
    feature set -- a real number, not a missing row."""

    total_stops: int
    """Stops across every strip in the file, base game and features together."""

    strips: int
    """How many strips carry it."""


@dataclass(frozen=True)
class MathDefaults:
    """``DefaultConfiguration``: what the maths starts a base game with."""

    symbol_set_id: str | None
    reel_strip_set_id: str | None
    paytable_id: str | None

    payline_set_id: str | None
    """Names a ``paylineSetID`` in ``winGeometry.xml``. The cabinet may play a
    different one -- ``gameConfig.cfg``'s ``NumberOfLines`` is authoritative."""

    initial_stops: tuple[int, ...]


@dataclass(frozen=True)
class GameMath:
    """One parsed ``math.xml``."""

    path: Path
    game_id: str | None
    game_pct: float | None
    min_game_pct: float | None
    game_base_pct: float | None
    min_game_base_pct: float | None
    defaults: MathDefaults
    symbol_sets: tuple[SymbolSet, ...]
    reel_strips: tuple[ReelStrip, ...]
    reel_strip_sets: tuple[ReelStripSet, ...]
    payline_combos: tuple[PaylineCombo, ...]
    scatter_combos: tuple[ScatterCombo, ...]
    paytables: tuple[PaytableRef, ...]

    def symbol_set(self, identifier: str | None) -> SymbolSet | None:
        """Look one symbol set up by identifier, or take the only one there is."""
        if identifier is None:
            return self.symbol_sets[0] if len(self.symbol_sets) == 1 else None
        return next((s for s in self.symbol_sets if s.identifier == identifier), None)

    def reel_strip(self, identifier: str) -> ReelStrip | None:
        """Look one strip up by identifier."""
        return next((s for s in self.reel_strips if s.identifier == identifier), None)

    def reel_strip_set(self, identifier: str | None) -> ReelStripSet | None:
        """Look one set of reels up by identifier."""
        if identifier is None:
            return None
        return next(
            (s for s in self.reel_strip_sets if s.identifier == identifier), None
        )

    def reel_symbols(
        self, *, base_set_id: str | None = None
    ) -> tuple[SymbolUsage, ...]:
        """Every symbol code that actually appears on a reel strip, counted.

        Built from ``ReelStripList`` rather than from ``SymbolSetList`` because
        the strips are what the player can land: a declared code that no strip
        carries is a row about nothing. ``base_set_id`` names the set whose
        stops are counted as :attr:`SymbolUsage.reel_stops` -- normally
        ``defaults.reel_strip_set_id``, the reels the base game spins.

        Ordered by first appearance on the reels, which is the file's own order
        and the only one that is not an opinion.
        """
        base = self.reel_strip_set(base_set_id)
        base_strips = set(base.strip_ids) if base is not None else set()

        reel_stops: dict[str, int] = {}
        total_stops: dict[str, int] = {}
        strips: dict[str, int] = {}
        order: list[str] = []

        for strip in self.reel_strips:
            counts = strip.counts()
            for code, count in counts.items():
                if code not in total_stops:
                    order.append(code)
                total_stops[code] = total_stops.get(code, 0) + count
                strips[code] = strips.get(code, 0) + 1
                if strip.identifier in base_strips:
                    reel_stops[code] = reel_stops.get(code, 0) + count

        return tuple(
            SymbolUsage(
                code=code,
                reel_stops=reel_stops.get(code, 0),
                total_stops=total_stops[code],
                strips=strips[code],
            )
            for code in order
        )

    def line_pays(self) -> dict[str, dict[int, float]]:
        """Line pays as a paytable is actually read: ``{code: {run: value}}``.

        A combo is one symbol repeated with an ``ANY`` tail, so the two numbers
        that matter are *which* symbol and *how long a run of it* -- and a
        paytable poster is that pivot, a row per symbol and a column per run
        length. Reading the combos back in their declared order instead makes
        the same thing three unordered rows per symbol.

        A mixed combo (two different codes on one line) has no single symbol to
        credit, so it is left out rather than attributed to whichever code came
        first. :attr:`payline_combos` still carries it verbatim.
        """
        pays: dict[str, dict[int, float]] = {}
        for combo in self.payline_combos:
            codes = {code for code in combo.symbols if code != ANY_SYMBOL}
            if len(codes) != 1 or combo.value is None:
                continue
            code = codes.pop()
            run = combo.match_length
            by_run = pays.setdefault(code, {})
            # A file declaring the same run twice keeps the better pay, which is
            # what the game would award.
            by_run[run] = max(by_run.get(run, combo.value), combo.value)
        return pays

    def top_line_pays(self) -> dict[str, float]:
        """Best line pay per symbol, whatever run length earns it."""
        return {
            code: max(by_run.values())
            for code, by_run in self.line_pays().items()
            if by_run
        }

    def roles(self) -> dict[str, str]:
        """What each symbol code *does*, derived rather than declared.

        ``math.xml`` carries no human-readable symbol names at all -- not in
        ``SymbolSetList``, not in ``ReelStripList``, nowhere -- but it does say
        which codes substitute (wild) and which are counted anywhere on screen
        (scatter). Everything else is a plain line symbol. That structure is the
        only thing a name can honestly be derived from.

        Covers declared *and* reel-borne codes, since a combo may name a symbol
        that no base-game strip carries.
        """
        roles: dict[str, str] = {}
        for symbol_set in self.symbol_sets:
            for symbol in symbol_set.symbols:
                roles.setdefault(symbol, "regular")
        for usage in self.reel_symbols():
            roles.setdefault(usage.code, "regular")
        for symbol_set in self.symbol_sets:
            for wild in symbol_set.wilds:
                roles[wild.code] = "wild"
        for combo in self.scatter_combos:
            for symbol in combo.symbols:
                # A wild that also scatters keeps the stronger label. No game so
                # far does both, and picking a winner would be inventing one.
                if roles.get(symbol) != "wild":
                    roles[symbol] = "scatter"
        return roles


def _symbol_set(element: ElementTree.Element) -> SymbolSet:
    """Read one ``<SymbolSet>``: its codes, and which of them substitute."""
    identifier = _text(element, "Identifier")
    if identifier is None:
        raise GameMathError("a <SymbolSet> has no <Identifier>")

    symbol_list = _child(element, "SymbolList")
    symbols = _texts(symbol_list, "Symbol") if symbol_list is not None else ()

    wilds: list[WildSymbol] = []
    wild_list = _child(element, "WildSymbolList")
    for wild in _children(wild_list, "WildSymbol") if wild_list is not None else []:
        code = _text(wild, "Identifier")
        if code is None:
            raise GameMathError(f"a <WildSymbol> in {identifier} has no <Identifier>")
        substitutes = _child(wild, "SymbolList")
        wilds.append(
            WildSymbol(
                code=code,
                substitutes=(
                    _texts(substitutes, "Symbol") if substitutes is not None else ()
                ),
            )
        )
    return SymbolSet(identifier=identifier, symbols=symbols, wilds=tuple(wilds))


def _reel_strip(element: ElementTree.Element) -> ReelStrip:
    """Read one ``<ReelStrip>``: its stops in order, with their weights."""
    identifier = _text(element, "Identifier")
    if identifier is None:
        raise GameMathError("a <ReelStrip> has no <Identifier>")

    symbols: list[str] = []
    weights: list[int] = []
    elements = _child(element, "WeightedElementList")
    for stop in _children(elements, "WeightedElement") if elements is not None else []:
        value = _text(stop, "StringValue")
        if value is None:
            raise GameMathError(
                f"a <WeightedElement> in reel strip {identifier} has no <StringValue>"
            )
        symbols.append(value)
        weights.append(_int(stop, "Weight") or 0)

    return ReelStrip(
        identifier=identifier,
        symbol_set_id=_text(element, "SymbolSetID"),
        symbols=tuple(symbols),
        weights=tuple(weights),
    )


def _reel_strip_set(element: ElementTree.Element) -> ReelStripSet:
    """Read one ``<ReelStripSet>``: its strips, and their visible heights."""
    identifier = _text(element, "Identifier")
    if identifier is None:
        raise GameMathError("a <ReelStripSet> has no <Identifier>")

    ids = _child(element, "ReelStripIDList")
    visible = _child(element, "ReelStripVisSymbols")
    heights: tuple[int, ...] = ()
    if visible is not None:
        heights = tuple(
            int(value) if value.isdigit() else 0
            for value in _texts(visible, "ReelStripVisSymbolsHeight")
        )
    return ReelStripSet(
        identifier=identifier,
        strip_ids=_texts(ids, "ReelStripID") if ids is not None else (),
        visible_heights=heights,
    )


def _combo_symbols(element: ElementTree.Element, *, where: str) -> tuple[str, ...]:
    """The ``<SymbolList>`` of one combo, in order."""
    symbols = _child(element, "SymbolList")
    if symbols is None:
        raise GameMathError(f"a combo in {where} has no <SymbolList>")
    return _texts(symbols, "Symbol")


def _combo_sets(
    root: ElementTree.Element,
) -> tuple[tuple[PaylineCombo, ...], tuple[ScatterCombo, ...]]:
    """Read ``<ComboSetList>`` into line combos and scatter combos.

    Flattened across sets, with the set name carried on each combo: a paytable
    selects whole sets, and the page showing them wants one sortable table.
    """
    payline: list[PaylineCombo] = []
    scatter: list[ScatterCombo] = []

    combo_sets = _child(root, "ComboSetList")
    if combo_sets is None:
        return (), ()

    for group in _children(combo_sets, "PaylineComboSet"):
        name = _text(group, "Identifier") or "PaylineComboSet"
        combo_list = _child(group, "PaylineComboList")
        combos = _children(combo_list, "PaylineCombo") if combo_list is not None else []
        for combo in combos:
            payline.append(
                PaylineCombo(
                    combo_set=name,
                    combo_id=_int(combo, "ComboID"),
                    group=_int(combo, "Group"),
                    value=_float(combo, "Value"),
                    symbols=_combo_symbols(combo, where=name),
                )
            )

    for group in _children(combo_sets, "ScatterComboSet"):
        name = _text(group, "Identifier") or "ScatterComboSet"
        combo_list = _child(group, "ScatterComboList")
        combos = (
            _children(combo_list, "CountScatterCombo") if combo_list is not None else []
        )
        for combo in combos:
            scatter.append(
                ScatterCombo(
                    combo_set=name,
                    combo_id=_int(combo, "ComboID"),
                    group=_int(combo, "Group"),
                    value=_float(combo, "Value"),
                    symbols=_combo_symbols(combo, where=name),
                    min_symbols=_int(combo, "MinNumSymbols"),
                    max_symbols=_int(combo, "MaxNumSymbols"),
                    base_multiplier=_text(combo, "BaseMultiplier"),
                    bonus_code=_int(combo, "BonusCode"),
                )
            )

    return tuple(payline), tuple(scatter)


def _defaults(root: ElementTree.Element) -> MathDefaults:
    """Read ``<DefaultConfiguration>``; an absent one is empty, not an error."""
    element = _child(root, "DefaultConfiguration")
    if element is None:
        return MathDefaults(None, None, None, None, ())

    stops = _child(element, "InitialStops")
    positions: tuple[int, ...] = ()
    if stops is not None:
        positions = tuple(
            int(value)
            for value in _texts(stops, "Position")
            if value.lstrip("-").isdigit()
        )
    return MathDefaults(
        symbol_set_id=_text(element, "SymbolSetID"),
        reel_strip_set_id=_text(element, "ReelStripSetID"),
        paytable_id=_text(element, "PaytableID"),
        payline_set_id=_text(element, "PaylineSetID"),
        initial_stops=positions,
    )


def _paytable_refs(root: ElementTree.Element, *, path: Path) -> tuple[PaytableRef, ...]:
    """Read ``<PaytableList>``: which combo sets each named paytable turns on."""
    paytables = _child(root, "PaytableList")
    refs: list[PaytableRef] = []
    for paytable in _children(paytables, "Paytable") if paytables is not None else []:
        identifier = _text(paytable, "Identifier")
        if identifier is None:
            raise GameMathError(f"a <Paytable> in {path} has no <Identifier>")
        combo_ids = _child(paytable, "ComboSetIDList")
        refs.append(
            PaytableRef(
                identifier=identifier,
                combo_set_ids=(
                    _texts(combo_ids, "ComboSet") if combo_ids is not None else ()
                ),
            )
        )
    return tuple(refs)


def load_game_math(path: Path) -> GameMath:
    """Read one ``math.xml``.

    Only the blocks a reader of the maths needs: ``BonusInfo``'s weight tables
    and ``MysteryReplacementInfo`` are left on disk, since neither is a symbol,
    a strip, or a pay.
    """
    root = _parse(path, what="math file")
    if _local(root.tag) != "GameMath":
        raise GameMathError(
            f"The math file at {path} is a <{_local(root.tag)}>, not a <GameMath>"
        )

    symbol_sets = _child(root, "SymbolSetList")
    strips = _child(root, "ReelStripList")
    strip_sets = _child(root, "ReelStripSetList")
    payline_combos, scatter_combos = _combo_sets(root)

    return GameMath(
        path=path,
        game_id=_text(root, "GameId"),
        game_pct=_float(root, "GamePct"),
        min_game_pct=_float(root, "MinGamePct"),
        game_base_pct=_float(root, "GameBasePct"),
        min_game_base_pct=_float(root, "MinGameBasePct"),
        defaults=_defaults(root),
        symbol_sets=tuple(
            _symbol_set(element)
            for element in (
                _children(symbol_sets, "SymbolSet") if symbol_sets is not None else []
            )
        ),
        reel_strips=tuple(
            _reel_strip(element)
            for element in (
                _children(strips, "ReelStrip") if strips is not None else []
            )
        ),
        reel_strip_sets=tuple(
            _reel_strip_set(element)
            for element in (
                _children(strip_sets, "ReelStripSet") if strip_sets is not None else []
            )
        ),
        payline_combos=payline_combos,
        scatter_combos=scatter_combos,
        paytables=_paytable_refs(root, path=path),
    )
