"""Joining a running game to the maths it actually loaded.

Three separate things have to agree before a symbol code on this page means
anything, and each is owned by someone else:

* the **game's log** says which paytable is loaded, as a ``paytableId`` written
  on every denomination change (:data:`app.utils.game_log.PAYTABLE_LOADED`);
* the **game's install** has one folder per paytable under its ``GameConfig``
  directory, named byte-identically to that id, holding ``math.xml`` and
  ``gameConfig.cfg`` (:mod:`app.utils.game_math`), with one ``winGeometry.xml``
  shared by all of them (:mod:`app.utils.win_geometry`);
* the **game config in this repo** points at that directory, and carries the
  one thing the maths cannot supply: what its two-letter symbol codes are
  called. *Which* codes exist, what they pay and where they sit on the reels are
  all read from the maths -- only the names are declared, and only because no
  element of these files holds display text of any kind.

This module is the only one that knows all three, and it reports how the join
was made rather than presenting the result as fact -- a page showing the wrong
maths is a stale log or a hand-typed id, and only saying which lets a reader
tell. The log is read *backwards* (:func:`app.utils.log_search.last_match`),
because the question is what it last said, not what it is about to say.

Holds state, unlike :mod:`app.services.roi`: ``math.xml`` is close to a
megabyte and parsing it per request would make the page feel broken, so parsed
files are cached against their mtime and :func:`reset` drops them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TypeVar, cast

from app.config.game_config import (
    ActiveGameSelectionError,
    GameConfig,
    GameConfigError,
    load_game_config,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.exceptions.base import (
    GameConfigInvalidError,
    PaytableInvalidError,
    PaytableNotFoundError,
    PaytableUnavailableError,
)
from app.schemas.paytable import (
    GameMathInfo,
    MathDefaultsInfo,
    PaylineComboInfo,
    PaylineInfo,
    PaylinePayRow,
    PaylineSetInfo,
    PaytableIdentityInfo,
    PaytableRefInfo,
    PaytableSource,
    PaytableView,
    ReelStripInfo,
    ReelStripSetInfo,
    ScatterComboInfo,
    SymbolInfo,
    WinGeometryInfo,
)
from app.utils import game_log
from app.utils.game_math import (
    GameMath,
    GameMathError,
    PaytableIdentity,
    ReelStrip,
    load_game_math,
    load_paytable_identity,
)
from app.utils.log_search import last_match
from app.utils.paths import UnsafeNameError, resolve_within
from app.utils.win_geometry import WinGeometry, WinGeometryError, load_win_geometry

logger = get_logger("paytable")

# Name of each file inside a paytable folder. Fixed by the game's build, not
# configurable, so they are constants rather than settings.
MATH_FILE = "math.xml"
IDENTITY_FILE = "gameConfig.cfg"
GEOMETRY_FILE = "winGeometry.xml"

# Fallback names for a game whose config names nothing yet. Both come from
# math.xml's structure rather than from any text in it -- it has none -- so a
# plain line symbol is deliberately absent: there is nothing to call it but its
# code until the config says otherwise.
ROLE_LABELS = {"wild": "Wild", "scatter": "Scatter"}

T = TypeVar("T")

# path -> (stat signature, parsed). Keyed on mtime *and* size so a file
# rewritten within one filesystem timestamp tick is still re-read.
_cache: dict[Path, tuple[tuple[int, int], object]] = {}


def reset() -> None:
    """Drop every parsed maths file, so one test's XML is not another's answer."""
    _cache.clear()


# --- loading --------------------------------------------------------------


def _signature(path: Path) -> tuple[int, int]:
    """Mtime and size of a file, or zeroes when it cannot be stat'd -- a missing
    file is the loader's problem to report, not the cache's."""
    try:
        stat = path.stat()
    except OSError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def _cached(path: Path, load: Callable[[Path], T]) -> T:
    """Parse ``path`` once per revision of the file."""
    signature = _signature(path)
    entry = _cache.get(path)
    if entry is not None and entry[0] == signature:
        return cast(T, entry[1])
    parsed = load(path)
    _cache[path] = (signature, parsed)
    return parsed


def _active_config() -> tuple[str, GameConfig]:
    """The selected game's config, with file errors in the API's terms."""
    try:
        name = settings.ideck_active_game
    except ActiveGameSelectionError as exc:
        raise GameConfigInvalidError(str(exc)) from exc
    path = settings.ideck_game_config_path_for(name)
    try:
        return name, load_game_config(path)
    except GameConfigError as exc:
        raise GameConfigInvalidError(
            f"Could not load game config {path}: {exc}"
        ) from exc


def _root(config: GameConfig) -> Path:
    """The game's installed ``GameConfig`` directory.

    A 409 rather than a 404: the game is not installed on this machine (or its
    config has never been pointed at it), which is something to fix on the
    machine, not in the request.
    """
    root = config.game_config_dir
    if root is None:
        raise PaytableUnavailableError(
            f"The game config for {config.name!r} declares no 'game_config' "
            "directory, so its maths files cannot be found"
        )
    if not root.is_dir():
        raise PaytableUnavailableError(
            f"The GameConfig directory for {config.name!r} is not there: {root}"
        )
    return root


def _available(root: Path) -> list[str]:
    """Paytable folders the game ships, newest maths last is not interesting --
    sorted by name, which is how the ids read.

    A directory only counts when it holds a ``math.xml``: the GameConfig root
    also holds loose files and the occasional tooling directory.
    """
    try:
        entries = sorted(
            entry.name
            for entry in root.iterdir()
            if entry.is_dir() and (entry / MATH_FILE).is_file()
        )
    except OSError as exc:
        raise PaytableUnavailableError(
            f"Could not list the GameConfig directory {root}: {exc}"
        ) from exc
    if not entries:
        raise PaytableUnavailableError(
            f"The GameConfig directory {root} holds no paytable folder with a "
            f"{MATH_FILE}"
        )
    return entries


# --- which paytable ------------------------------------------------------


def _from_log(config: GameConfig) -> tuple[str, PaytableSource] | None:
    """Last paytable the game's log named, or ``None`` if it never did.

    Not an error when it did not: a game that has not been started since the log
    rotated has simply not said yet, and the page can still be asked for a
    specific folder.
    """
    log_path = config.log_path
    if log_path is None:
        return None

    scan = settings.PAYTABLE_LOG_SCAN_BYTES
    found = last_match(
        log_path,
        game_log.PAYTABLE_LOADED,
        max_bytes=scan or None,
    )
    if found is None:
        return None

    paytable = found.match.group("paytable").strip()
    if not paytable:
        return None

    logged_at: datetime | None = None
    line = game_log.parse_line(found.line)
    if line is not None:
        logged_at = line.timestamp

    # The same line lists every denomination the cabinet accepts, and each one
    # loads a different paytable folder -- so this is the set of maths the
    # session can move between, which is what makes the current one worth
    # naming beside it.
    raw_supported = found.match.group("supported") or ""
    supported = [part.strip() for part in raw_supported.split(",") if part.strip()]

    return paytable, PaytableSource(
        origin="log",
        log_path=str(log_path),
        log_line=found.line,
        logged_at=logged_at,
        denomination=found.match.group("denom"),
        supported_denominations=supported,
    )


def _resolve(
    config: GameConfig, root: Path, available: list[str], requested: str | None
) -> tuple[Path, str, PaytableSource]:
    """Pick the paytable folder to read, and say how it was picked."""
    if requested:
        try:
            directory = resolve_within(root, requested)
        except UnsafeNameError as exc:
            raise PaytableNotFoundError(f"Invalid paytable id: {exc.reason}") from exc
        if not (directory / MATH_FILE).is_file():
            raise PaytableNotFoundError(
                f"No paytable folder named {requested!r} with a {MATH_FILE} in {root}"
            )
        return directory, directory.name, PaytableSource(origin="requested")

    logged = _from_log(config)
    if logged is not None:
        paytable, source = logged
        directory = root / paytable
        if not (directory / MATH_FILE).is_file():
            # The join failed, and saying which half is missing is the whole
            # point: the log is right, the install does not have that folder.
            raise PaytableNotFoundError(
                f"The game log says paytable {paytable!r} is loaded, but {root} "
                f"has no folder of that name with a {MATH_FILE}"
            )
        return directory, paytable, source

    if len(available) == 1:
        only = available[0]
        return root / only, only, PaytableSource(origin="only")

    raise PaytableNotFoundError(
        f"The log for {config.name!r} has not named a paytable, and {root} holds "
        f"{len(available)} of them -- ask for one by id"
    )


# --- assembling the view --------------------------------------------------


def _load_math(directory: Path) -> GameMath:
    """Parse the folder's ``math.xml``, cached against its mtime."""
    path = directory / MATH_FILE
    try:
        return _cached(path, load_game_math)
    except GameMathError as exc:
        raise PaytableInvalidError(str(exc)) from exc


def _load_identity(directory: Path) -> PaytableIdentity | None:
    """Parse the folder's ``gameConfig.cfg``. Absent is a real state -- the
    maths is still readable without the identity block."""
    path = directory / IDENTITY_FILE
    if not path.is_file():
        return None
    try:
        return _cached(path, load_paytable_identity)
    except GameMathError as exc:
        raise PaytableInvalidError(str(exc)) from exc


def _geometry_path(config: GameConfig, root: Path) -> Path:
    """The game's ``winGeometry.xml``: the config's own pointer when it has one,
    otherwise the file beside the paytable folders."""
    return config.win_geometry_path or (root / GEOMETRY_FILE)


def _labels(math: GameMath, config: GameConfig) -> dict[str, str]:
    """What to call each symbol code.

    The maths files carry no display text in any element, so a readable name
    exists only where the game config's ``symbols`` block declares one -- this
    is the one thing on the page that is configured rather than read, and it is
    configured because it cannot be read.

    Underneath it is what the maths' own *structure* implies: a member of
    ``WildSymbolList`` is a Wild, a code a ``CountScatterCombo`` counts is a
    Scatter. That fallback is what a game whose config names nothing yet gets,
    so a new game is still readable before anyone fills its block in.
    """
    labels = {
        code: ROLE_LABELS[role]
        for code, role in math.roles().items()
        if role in ROLE_LABELS
    }
    labels.update(config.symbols)
    return labels


def _symbols(math: GameMath, config: GameConfig) -> list[SymbolInfo]:
    """Every symbol code in play, best-paying first.

    Counted off ``ReelStripList``, because that is what can actually land, but
    codes the symbol set merely *declares* are listed too rather than dropped:
    FortuneOx declares eighteen and puts seventeen on a strip, and the
    eighteenth is a real feature symbol with a name and an award. Its zeros are
    what say no spin can produce it here, which is more use than its absence.
    """
    roles = math.roles()
    labels = _labels(math, config)
    pays = math.top_line_pays()
    substitutes = {
        wild.code: list(wild.substitutes)
        for symbol_set in math.symbol_sets
        for wild in symbol_set.wilds
    }

    usages = {
        usage.code: usage
        for usage in math.reel_symbols(base_set_id=math.defaults.reel_strip_set_id)
    }
    declared = [code for group in math.symbol_sets for code in group.symbols]
    codes = list(usages) + [code for code in declared if code not in usages]

    symbols = [
        SymbolInfo(
            code=code,
            name=labels.get(code),
            role=roles.get(code, "regular"),
            substitutes=substitutes.get(code, []),
            top_pay=pays.get(code),
            reel_stops=usages[code].reel_stops if code in usages else 0,
            total_stops=usages[code].total_stops if code in usages else 0,
            strips=usages[code].strips if code in usages else 0,
            on_reels=code in usages,
        )
        for code in codes
    ]
    # Pay order, which is how a paytable reads. Unpaid symbols sort last rather
    # than first (a plain descending sort on None would invert that), and a code
    # no strip carries sorts below one that pays nothing but can still land.
    symbols.sort(
        key=lambda symbol: (
            -(symbol.top_pay or 0.0),
            not symbol.on_reels,
            symbol.code,
        )
    )
    return symbols


def _strip_positions(math: GameMath) -> dict[str, tuple[str, int]]:
    """Which set each strip belongs to and where in it, so a strip's row can
    say "reel 3 of Reels_BG_0" rather than just its name.

    A strip listed by two sets keeps its first placement; nothing in the shipped
    files does that, and picking the later one would be arbitrary.
    """
    placement: dict[str, tuple[str, int]] = {}
    for strip_set in math.reel_strip_sets:
        for index, identifier in enumerate(strip_set.strip_ids):
            placement.setdefault(identifier, (strip_set.identifier, index))
    return placement


def _reel_strips(math: GameMath) -> list[ReelStripInfo]:
    """Every strip, the default set's reels first and in reel order."""
    placement = _strip_positions(math)
    default_set = math.defaults.reel_strip_set_id
    cap = settings.PAYTABLE_MAX_STRIP_STOPS

    def sort_key(strip: ReelStrip) -> tuple[int, str, int]:
        set_id, index = placement.get(strip.identifier, ("", 0))
        return (0 if set_id == default_set else 1, set_id, index)

    rows: list[ReelStripInfo] = []
    for strip in sorted(math.reel_strips, key=sort_key):
        set_id, index = placement.get(strip.identifier, (None, None))
        rows.append(
            ReelStripInfo(
                identifier=strip.identifier,
                set_id=set_id,
                reel_index=index,
                symbol_set_id=strip.symbol_set_id,
                length=strip.length,
                symbols=list(strip.symbols[:cap]),
                weights=list(strip.weights[:cap]),
                truncated=strip.length > cap,
            )
        )
    return rows


def _named(codes: tuple[str, ...], labels: dict[str, str]) -> list[str | None]:
    """Symbol codes resolved through the maths' own labels, in step."""
    return [labels.get(code) for code in codes]


def _combos(math: GameMath, config: GameConfig) -> list[PaylineComboInfo]:
    """Line pays, best first. The file is already value-descending; sorting
    makes that a property of the response rather than of the file."""
    labels = _labels(math, config)
    rows = [
        PaylineComboInfo(
            combo_id=combo.combo_id,
            combo_set=combo.combo_set,
            group=combo.group,
            value=combo.value,
            symbols=list(combo.symbols),
            names=_named(combo.symbols, labels),
            match_length=combo.match_length,
        )
        for combo in math.payline_combos
    ]
    rows.sort(key=lambda row: (-(row.value or 0.0), row.combo_id or 0))
    return rows


def _pay_table(
    math: GameMath, config: GameConfig
) -> tuple[list[int], list[PaylinePayRow]]:
    """The line pays pivoted into a paytable poster.

    A combo is one symbol repeated with an ``ANY`` tail, so every pay is really
    a (symbol, run length, value) triple and the natural shape is a grid --
    which is how every paytable a player has ever seen is laid out. Symbols with
    an identical row are merged (a game gives its card ranks one profile, and
    five identical rows say less than one row naming five symbols), and the
    merge is on the values alone, so two symbols only share a row when they
    genuinely pay the same at every length.
    """
    pays = math.line_pays()
    if not pays:
        return [], []

    lengths = sorted({run for by_run in pays.values() for run in by_run}, reverse=True)
    labels = _labels(math, config)

    grouped: dict[tuple[float | None, ...], list[str]] = {}
    for code in sorted(pays):
        values = tuple(pays[code].get(run) for run in lengths)
        grouped.setdefault(values, []).append(code)

    rows = [
        PaylinePayRow(
            codes=codes,
            names=[labels.get(code) for code in codes],
            values=list(values),
            top_pay=max((value for value in values if value is not None), default=None),
        )
        for values, codes in grouped.items()
    ]
    # Best first, and ties broken by the first symbol so the order is stable
    # rather than dictionary order.
    rows.sort(key=lambda row: (-(row.top_pay or 0.0), row.codes[0]))
    return lengths, rows


def _scatters(math: GameMath, config: GameConfig) -> list[ScatterComboInfo]:
    """Scatter and feature awards, in the order the file declares them -- they
    are grouped by feature, and value-sorting would break that grouping."""
    labels = _labels(math, config)
    return [
        ScatterComboInfo(
            combo_id=combo.combo_id,
            combo_set=combo.combo_set,
            group=combo.group,
            value=combo.value,
            symbols=list(combo.symbols),
            names=_named(combo.symbols, labels),
            min_symbols=combo.min_symbols,
            max_symbols=combo.max_symbols,
            base_multiplier=combo.base_multiplier,
            bonus_code=combo.bonus_code,
        )
        for combo in math.scatter_combos
    ]


def _applicable_set(
    math: GameMath, identity: PaytableIdentity | None
) -> tuple[str | None, str]:
    """Which payline set is in play, and on whose authority.

    The paytable folder is per line configuration, so its own ``NumberOfLines``
    outranks ``math.xml``'s default -- the same ``math.xml`` ships in folders
    that play 5, 20 and 40 lines.
    """
    if identity is not None and identity.number_of_lines:
        return str(identity.number_of_lines), "game_config"
    if math.defaults.payline_set_id:
        return math.defaults.payline_set_id, "math_default"
    return None, "unresolved"


def _win_geometry(
    path: Path, math: GameMath, identity: PaytableIdentity | None
) -> WinGeometryInfo:
    """Read the geometry file and narrow it to the set in play.

    An unreadable one is carried as an ``error`` rather than raised: the symbols,
    strips and combos above it are all still worth looking at, and a page that
    404s because one of four tables is missing is worse than one that says so.
    """
    set_id, resolved_from = _applicable_set(math, identity)
    try:
        geometry: WinGeometry = _cached(path, load_win_geometry)
    except WinGeometryError as exc:
        return WinGeometryInfo(
            path=str(path),
            payline_set_id=set_id,
            resolved_from=resolved_from,
            line_count=None,
            sets=[],
            paylines=[],
            error=str(exc),
        )

    applicable = geometry.set_for(set_id)
    sets = [
        PaylineSetInfo(
            payline_set_id=candidate.payline_set_id,
            line_count=candidate.line_count,
            is_applicable=candidate is applicable,
        )
        for candidate in geometry.sets
    ]
    error = None
    if set_id is not None and applicable is None:
        error = (
            f"No payline set {set_id!r} in {path.name}; it declares "
            f"{', '.join(s.payline_set_id for s in geometry.sets)}"
        )

    return WinGeometryInfo(
        path=str(geometry.path),
        payline_set_id=set_id,
        resolved_from=resolved_from,
        line_count=applicable.line_count if applicable else None,
        sets=sets,
        paylines=[
            PaylineInfo(
                line=payline.label,
                number=payline.number,
                elements=[list(pair) for pair in payline.elements],
                grid=[list(pair) for pair in payline.grid()],
            )
            for payline in (applicable.paylines if applicable else ())
        ],
        error=error,
    )


def _identity_info(identity: PaytableIdentity | None) -> PaytableIdentityInfo | None:
    """Narrow a parsed ``gameConfig.cfg`` to its public shape."""
    if identity is None:
        return None
    return PaytableIdentityInfo(
        path=str(identity.path),
        game_type=identity.game_type,
        game_id=identity.game_id,
        display_game_id=identity.display_game_id,
        game_pct=identity.game_pct,
        min_game_pct=identity.min_game_pct,
        game_base_pct=identity.game_base_pct,
        min_game_base_pct=identity.min_game_base_pct,
        number_of_lines=identity.number_of_lines,
        min_total_bet=identity.min_total_bet,
        max_bets=list(identity.max_bets),
        denominations=list(identity.denominations),
    )


def _math_info(math: GameMath, config: GameConfig) -> GameMathInfo:
    """Narrow a parsed ``math.xml`` to its public shape."""
    default_set = math.defaults.reel_strip_set_id
    pay_lengths, pay_table = _pay_table(math, config)
    return GameMathInfo(
        path=str(math.path),
        game_id=math.game_id,
        game_pct=math.game_pct,
        min_game_pct=math.min_game_pct,
        game_base_pct=math.game_base_pct,
        min_game_base_pct=math.min_game_base_pct,
        defaults=MathDefaultsInfo(
            symbol_set_id=math.defaults.symbol_set_id,
            reel_strip_set_id=math.defaults.reel_strip_set_id,
            paytable_id=math.defaults.paytable_id,
            payline_set_id=math.defaults.payline_set_id,
            initial_stops=list(math.defaults.initial_stops),
        ),
        symbols=_symbols(math, config),
        reel_strip_sets=[
            ReelStripSetInfo(
                identifier=strip_set.identifier,
                strip_ids=list(strip_set.strip_ids),
                visible_heights=list(strip_set.visible_heights),
                is_default=strip_set.identifier == default_set,
            )
            for strip_set in math.reel_strip_sets
        ],
        reel_strips=_reel_strips(math),
        payline_combos=_combos(math, config),
        pay_lengths=pay_lengths,
        pay_table=pay_table,
        scatter_combos=_scatters(math, config),
        paytables=[
            PaytableRefInfo(
                identifier=ref.identifier, combo_set_ids=list(ref.combo_set_ids)
            )
            for ref in math.paytables
        ],
    )


def _view(paytable_id: str | None) -> PaytableView:
    """Blocking half of :func:`view`: file reads and XML parsing."""
    game, config = _active_config()
    root = _root(config)
    available = _available(root)
    directory, resolved_id, source = _resolve(config, root, available, paytable_id)

    math = _load_math(directory)
    identity = _load_identity(directory)

    logger.info("Read paytable %s for %s (%s)", resolved_id, game, source.origin)

    return PaytableView(
        game=game,
        label=config.name,
        paytable_id=resolved_id,
        directory=str(directory),
        source=source,
        available=available,
        identity=_identity_info(identity),
        math=_math_info(math, config),
        win_geometry=_win_geometry(_geometry_path(config, root), math, identity),
    )


async def view(paytable_id: str | None = None) -> PaytableView:
    """The active game's loaded maths. ``paytable_id`` inspects another folder
    of the same game instead of the one its log named."""
    return await asyncio.to_thread(_view, paytable_id)
