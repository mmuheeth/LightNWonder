"""Payloads for the paytable view: what maths the running game has loaded."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# --- where the id came from -----------------------------------------------


class PaytableSource(BaseModel):
    """How the paytable id being shown was arrived at.

    Reported rather than assumed: a page showing the wrong maths is either a
    stale log or a hand-typed id, and only saying which lets the reader tell.
    """

    origin: str = Field(
        description=(
            "'log' when the game's log named it, 'requested' when the caller "
            "asked for a specific folder, 'only' when the game has exactly one "
            "paytable folder and the log said nothing."
        )
    )
    log_path: str | None = Field(
        default=None, description="Game log that was searched, when there is one."
    )
    log_line: str | None = Field(
        default=None, description="The whole log line the id was read out of."
    )
    logged_at: datetime | None = Field(
        default=None, description="Timestamp on that line, in the host's local time."
    )
    denomination: str | None = Field(
        default=None,
        description="Denomination the same line reported, which is what selects "
        "the paytable on this cabinet.",
    )
    supported_denominations: list[str] = Field(
        default_factory=list,
        description=(
            "Every denomination that line said the cabinet accepts. Each one "
            "loads a different paytable folder, so this is the set of maths a "
            "session can move between without restarting the game."
        ),
    )


# --- the maths ------------------------------------------------------------


class SymbolInfo(BaseModel):
    """One symbol code, what it is called, and what the maths does with it.

    Which codes exist and what they are worth is read from ``math.xml``; only
    the display name is configured, because the maths files carry no display
    text in any element. The reel counts are what separate the two: a code the
    strips never carry is still listed when the config names it, but its zeros
    say so.
    """

    code: str = Field(description="Two-letter code as the maths files write it.")
    name: str | None = Field(
        default=None,
        description=(
            "Display name from the game config's 'symbols' block. Falls back to "
            "what the maths' structure implies -- 'Wild' for a member of "
            "WildSymbolList, 'Scatter' for a code a CountScatterCombo counts -- "
            "and is null for an unnamed line symbol, whose code is its name."
        ),
    )
    role: str = Field(
        description="'wild', 'scatter' or 'regular', derived from math.xml."
    )
    substitutes: list[str] = Field(
        default_factory=list,
        description="Codes this symbol stands in for. Wilds only.",
    )
    top_pay: float | None = Field(
        default=None,
        description=(
            "Best line pay for a combo made of this code alone. Null when no "
            "line combo pays for it -- a feature or trigger symbol."
        ),
    )
    reel_stops: int = Field(
        description="Stops this symbol occupies on the base game's own reels."
    )
    total_stops: int = Field(
        description="Stops across every strip in the file, features included."
    )
    strips: int = Field(description="How many reel strips carry this symbol.")
    on_reels: bool = Field(
        default=True,
        description=(
            "Whether any strip carries this code. False for one the symbol set "
            "declares and no strip uses -- listed because the config names it, "
            "but no spin can produce it."
        ),
    )


class ReelStripInfo(BaseModel):
    """One reel strip, in stop order."""

    identifier: str = Field(description="Strip name as math.xml declares it.")
    set_id: str | None = Field(
        default=None, description="Reel strip set this strip belongs to."
    )
    reel_index: int | None = Field(
        default=None,
        description="Position within its set, 0-indexed; null if unplaced.",
    )
    symbol_set_id: str | None = Field(
        default=None, description="Symbol set it draws on."
    )
    length: int = Field(description="Stops on the strip, before any truncation.")
    symbols: list[str] = Field(description="Symbol code at each stop, in order.")
    weights: list[int] = Field(
        description="Stop weighting parallel to 'symbols'. Usually uniform."
    )
    truncated: bool = Field(
        default=False,
        description="Whether 'symbols' was cut short of 'length' by the send cap.",
    )


class ReelStripSetInfo(BaseModel):
    """One named set of reels."""

    identifier: str = Field(description="Set name as math.xml declares it.")
    strip_ids: list[str] = Field(description="Its strips, in reel order.")
    visible_heights: list[int] = Field(
        description="Rows of each reel on screen: [3,3,3,3,3] for a 5x3 game."
    )
    is_default: bool = Field(
        default=False, description="Whether the base game starts on this set."
    )


class PaylineComboInfo(BaseModel):
    """One paying pattern along a line."""

    combo_id: int | None = Field(default=None, description="Id from math.xml.")
    combo_set: str = Field(description="Combo set it belongs to.")
    group: int | None = Field(
        default=None, description="100 for line pays; 200/300 for feature awards."
    )
    value: float | None = Field(default=None, description="Credits paid per line.")
    symbols: list[str] = Field(
        description="The pattern left to right; 'ANY' is the trailing wildcard."
    )
    names: list[str | None] = Field(
        description="'symbols' resolved through the maths' own labels, in step: "
        "'Wild', 'Scatter', or null for a plain code.",
    )
    match_length: int = Field(
        description="Symbols that must match before the 'ANY' tail."
    )


class PaylinePayRow(BaseModel):
    """One row of the paytable poster: symbols that pay the same, and what they
    pay for each run length.

    Symbols are grouped because a game gives several of them one pay profile --
    the card ranks, usually -- and four identical rows say less than one row
    naming four symbols.
    """

    codes: list[str] = Field(description="Symbols sharing this row, in pay order.")
    names: list[str | None] = Field(description="Those codes' names, in step.")
    values: list[float | None] = Field(
        description=(
            "Pay per run length, parallel to the enclosing 'pay_lengths'. Null "
            "where that run does not pay for these symbols."
        )
    )
    top_pay: float | None = Field(
        default=None, description="Best value in the row, which is what it sorts on."
    )


class ScatterComboInfo(BaseModel):
    """One award for a count of symbols anywhere on screen."""

    combo_id: int | None = Field(default=None, description="Id from math.xml.")
    combo_set: str = Field(description="Combo set it belongs to.")
    group: int | None = Field(default=None, description="Award group from math.xml.")
    value: float | None = Field(default=None, description="Multiplier applied, if any.")
    symbols: list[str] = Field(description="Symbol codes counted.")
    names: list[str | None] = Field(
        description="Those codes resolved through the maths' own labels."
    )
    min_symbols: int | None = Field(default=None, description="Fewest that award.")
    max_symbols: int | None = Field(default=None, description="Most that award.")
    base_multiplier: str | None = Field(
        default=None,
        description="What 'value' multiplies: TotalBet, BetPerLine, or none.",
    )
    bonus_code: int | None = Field(
        default=None, description="Bonus this triggers, if any."
    )


class MathDefaultsInfo(BaseModel):
    """What the maths starts a base game with."""

    symbol_set_id: str | None = None
    reel_strip_set_id: str | None = None
    paytable_id: str | None = Field(
        default=None, description="Named paytable inside math.xml, not the folder id."
    )
    payline_set_id: str | None = Field(
        default=None, description="Payline set math.xml defaults to."
    )
    initial_stops: list[int] = Field(default_factory=list)


class PaytableRefInfo(BaseModel):
    """A named paytable inside math.xml and the combo sets it turns on."""

    identifier: str
    combo_set_ids: list[str] = Field(default_factory=list)


class GameMathInfo(BaseModel):
    """Everything read out of one ``math.xml``."""

    path: str = Field(description="File it was read from.")
    game_id: str | None = Field(default=None, description="math.xml's own GameId.")
    game_pct: float | None = Field(
        default=None, description="Theoretical return, percent."
    )
    min_game_pct: float | None = None
    game_base_pct: float | None = Field(
        default=None, description="Return from base game alone, percent."
    )
    min_game_base_pct: float | None = None
    defaults: MathDefaultsInfo
    symbols: list[SymbolInfo] = Field(
        description="Every symbol the reel strips carry, best-paying first."
    )
    reel_strip_sets: list[ReelStripSetInfo]
    reel_strips: list[ReelStripInfo] = Field(
        description="Every strip, default set first."
    )
    payline_combos: list[PaylineComboInfo] = Field(
        description=(
            "Line pays exactly as math.xml declares them, highest value first. "
            "The faithful reading; 'pay_table' is the same data pivoted."
        )
    )
    pay_lengths: list[int] = Field(
        default_factory=list,
        description=(
            "Run lengths the game pays for, longest first -- the columns of "
            "'pay_table', e.g. [5, 4, 3, 2]."
        ),
    )
    pay_table: list[PaylinePayRow] = Field(
        default_factory=list,
        description=(
            "The line pays pivoted into a paytable poster: a row per symbol (or "
            "per group of symbols paying alike), a column per run length. "
            "Excludes any combo mixing two symbols, which has no row to sit in."
        ),
    )
    scatter_combos: list[ScatterComboInfo]
    paytables: list[PaytableRefInfo]


# --- win geometry ---------------------------------------------------------


class PaylineInfo(BaseModel):
    """One line of the applicable set."""

    line: int = Field(description="Line number as a player counts it, 1-indexed.")
    number: int = Field(description="'paylineNumber' as the file writes it, 0-indexed.")
    elements: list[list[int]] = Field(
        description="[reel_index, position] pairs, both 0-indexed, in reel order."
    )
    grid: list[list[int]] = Field(
        description="The same line as [row, column], 1-indexed -- the form a game "
        "config's 'paylines' block uses, so the two can be compared."
    )


class PaylineSetInfo(BaseModel):
    """One selectable payline set and how many lines it holds."""

    payline_set_id: str
    line_count: int
    is_applicable: bool = Field(
        description="Whether this is the set the loaded paytable plays."
    )


class WinGeometryInfo(BaseModel):
    """``winGeometry.xml``, narrowed to the set actually in play."""

    path: str = Field(description="File it was read from.")
    payline_set_id: str | None = Field(
        default=None, description="Set the loaded paytable plays, e.g. '40'."
    )
    resolved_from: str = Field(
        description=(
            "Where that id came from: 'game_config' (the paytable's own "
            "NumberOfLines), 'math_default' (math.xml's DefaultConfiguration), "
            "or 'unresolved'."
        )
    )
    line_count: int | None = Field(
        default=None,
        description="Lines the applicable set declares. Null if unresolved.",
    )
    sets: list[PaylineSetInfo] = Field(description="Every set the file declares.")
    paylines: list[PaylineInfo] = Field(
        default_factory=list, description="The applicable set's lines, line 1 first."
    )
    error: str | None = Field(
        default=None,
        description="Why the geometry could not be read. Null when it was; the "
        "rest of the page is still worth showing without it.",
    )


class BetConfigInfo(BaseModel):
    """``betPerUnitConfig.xml`` and ``betUnitConfig.xml``: what a spin costs.

    The pair answers what neither ``math.xml`` nor ``gameConfig.cfg`` answers
    directly. A payline combo's ``value`` is a rate **per bet unit**, so what a
    line actually awards is ``value x bet_per_unit`` -- 25 becomes 25 at the
    minimum and 250 at ten. ``unit_cost`` is the other factor: a spin costs
    ``unit_cost x bet_per_unit`` credits.

    Read as a pair because only these two files separate the factors.
    ``gameConfig.cfg``'s ``SpecificMaxBets`` and ``math.xml``'s ``AllowedBetsTbl``
    both carry the *product* (``88 176 264 440 880``), which cannot be divided
    back into a cost and a ladder without already knowing one of them.
    """

    path: str = Field(description="The betPerUnitConfig.xml it was read from.")
    ladder: list[int] = Field(
        default_factory=list,
        description=(
            "The bets per unit a player can select, in the file's own order -- "
            "1, 2, 3, 5, 10 on FortuneOx. A ladder and never a range: there is "
            "no 4."
        ),
    )
    minimum: int | None = Field(default=None, description="MinBetPerUnit, as declared.")
    maximum: int | None = Field(
        default=None,
        description=(
            "MaxBetPerUnit. The file's own comment calls it the highest value "
            "across all mappings, so it need not be the last rung of any one."
        ),
    )
    units: int | None = Field(
        default=None,
        description="numUnits of the block that answered -- the paytable's lines.",
    )
    unit_cost: int | None = Field(
        default=None,
        description=(
            "Credits one spin costs at one bet per unit. Should equal the "
            "folder's own MinTotalBet, which is a free corroboration of this "
            "read rather than its source."
        ),
    )
    total_bets: list[int] = Field(
        default_factory=list,
        description=(
            "unit_cost x each rung -- what the cabinet can actually be bet at. "
            "Reproduces SpecificMaxBets exactly, which is what says the pair was "
            "read correctly."
        ),
    )
    error: str | None = Field(
        default=None,
        description=(
            "Why the bet configuration could not be read. Null when it was; the "
            "symbols, strips and combos above it are still true without it."
        ),
    )


# --- the whole view -------------------------------------------------------


class PaytableIdentityInfo(BaseModel):
    """``gameConfig.cfg``: the paytable folder in its own words."""

    path: str
    game_type: str | None = None
    game_id: str | None = Field(
        default=None, description="Should equal the folder name and the log's id."
    )
    display_game_id: str | None = None
    game_pct: float | None = None
    min_game_pct: float | None = None
    game_base_pct: float | None = None
    min_game_base_pct: float | None = None
    number_of_lines: int | None = Field(
        default=None,
        description="Lines the cabinet plays: what selects the payline set.",
    )
    min_total_bet: int | None = None
    min_denom_multiplier: int | None = Field(
        default=None,
        description=(
            "How many of the base unit one credit is worth on this folder -- 2 on "
            "a '-2c-' paytable. The only *declared* statement of a denomination's "
            "amount, so it is what corroborates the logged one."
        ),
    )
    max_bets: list[int] = Field(default_factory=list)
    denominations: list[float] = Field(
        default_factory=list,
        description=(
            "This folder's own DenomConfig entries. Not the denominations the "
            "cabinet currently offers, and not guaranteed to contain the current "
            "one -- the '-2c-' folder lists 1, 5, 10, 50, 100 while running at 2. "
            "Do not check a live denomination against it."
        ),
    )


class DenominationInfo(BaseModel):
    """The denomination the cabinet is running, interpreted.

    Three fields on this response name a denomination and they are not
    interchangeable: ``source.denomination`` is the raw string the game's log
    wrote, ``identity.denominations`` is the loaded folder's own ``DenomConfig``
    list (which does *not* contain the current one), and this is the
    interpretation of the first. Anything doing arithmetic wants
    ``money_per_credit`` from here and nothing else.
    """

    value: float = Field(
        description=(
            "The number the log reported, in `unit` -- 2.0 for a 2c game. Not a "
            "rate: a bet divided by this is wrong by a factor of a hundred."
        )
    )
    unit: str = Field(
        description="'cent' or 'unknown', taken from the paytable id's suffix."
    )
    label: str = Field(
        description=(
            "The denomination as an operator says it, e.g. '2c'. Carries no "
            "currency: which currency those cents are in is a property of the "
            "meter and is read off the glass."
        )
    )
    money_per_credit: float | None = Field(
        default=None,
        description=(
            "One credit in money -- 0.02 for a 2c game. The only field to "
            "multiply or divide by. Null when the paytable id named no unit, "
            "which is a refusal to guess rather than a missing value: the "
            "cabinet's supported ladder looks like proof of cents, but a machine "
            "denominated in whole currency units prints the same shape."
        ),
    )
    declared_multiplier: int | None = Field(
        default=None,
        description="gameConfig.cfg's MinDenomMultiplier, the evidence behind `agrees`.",
    )
    agrees: bool | None = Field(
        default=None,
        description=(
            "Whether every source that named an amount named the same one. Null "
            "when there was nothing to check against. False changes no number "
            "here -- the logged value is the current one, the others are "
            "properties of a folder -- but it means a reading somewhere is stale."
        ),
    )
    resolved_from: str = Field(
        description="'paytable-id' when the unit was resolved, else 'unresolved'."
    )


class PaytableView(BaseModel):
    """The active game's loaded maths, joined from log, folder name and XML."""

    game: str = Field(description="Filename stem of the active game config.")
    label: str = Field(description="Display name declared by that config.")
    paytable_id: str = Field(description="Folder the maths was read from.")
    directory: str = Field(description="Absolute path of that folder.")
    source: PaytableSource
    available: list[str] = Field(
        description="Every paytable folder the game ships, so another can be inspected."
    )
    identity: PaytableIdentityInfo | None = Field(
        default=None, description="Null when the folder ships no gameConfig.cfg."
    )
    denomination: DenominationInfo | None = Field(
        default=None,
        description=(
            "The denomination in play, interpreted from the logged value and the "
            "paytable id. Null when no value was reported -- a requested id, or a "
            "log that never named one."
        ),
    )
    math: GameMathInfo
    win_geometry: WinGeometryInfo
    bet_config: BetConfigInfo | None = Field(
        default=None,
        description=(
            "What a spin costs and what multiplies a line's value. Null when the "
            "folder ships neither bet configuration file -- older installs, and "
            "any machine without the game."
        ),
    )
