"""Reading the game's own maths files.

These are foreign formats -- written by the game's build, not by anything here
-- so what is worth testing is the shape they actually arrive in: a UTF-8 BOM,
a namespace on one file and not the other, values indented inside their tags,
and paylines in an order that is not line order. ``tests/test_paytable.py``
covers what a *service* does with the result; this file is only about reading.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.utils.game_math import GameMathError, load_game_math, load_paytable_identity
from app.utils.win_geometry import WinGeometryError, load_win_geometry

MATH = """<?xml version="1.0" encoding="utf-8"?>
<GameMath xmlns="http://scientificgames.com/slotMathXMLSchema.xsd">
  <GameId>1101YX_1c_90</GameId>
  <GamePct>88.01</GamePct>
  <MinGamePct>88.01</MinGamePct>
  <GameBasePct>82.90</GameBasePct>
  <DefaultConfiguration>
    <SymbolSetID>SymbolSet_Main</SymbolSetID>
    <ReelStripSetID>Reels_BG_0</ReelStripSetID>
    <PaytableID>Paytable_Main</PaytableID>
    <PaylineSetID>40</PaylineSetID>
    <InitialStops>
      <Position>0</Position><Position>28</Position>
    </InitialStops>
  </DefaultConfiguration>
  <SymbolSetList>
    <SymbolSet>
      <Identifier>SymbolSet_Main</Identifier>
      <SymbolList>
        <Symbol>WC</Symbol><Symbol>AA</Symbol><Symbol>FG</Symbol>
      </SymbolList>
      <WildSymbolList>
        <WildSymbol>
          <Identifier>WC</Identifier>
          <SymbolList><Symbol>AA</Symbol></SymbolList>
        </WildSymbol>
      </WildSymbolList>
    </SymbolSet>
  </SymbolSetList>
  <ReelStripList>
    <ReelStrip>
      <Identifier>Reels_BG_0_0</Identifier>
      <SymbolSetID>SymbolSet_Main</SymbolSetID>
      <WeightedElementList>
        <WeightedElement><Weight>10</Weight><StringValue>AA</StringValue></WeightedElement>
        <WeightedElement><Weight>9</Weight><StringValue>AA</StringValue></WeightedElement>
        <WeightedElement><Weight>1</Weight><StringValue>WC</StringValue></WeightedElement>
      </WeightedElementList>
    </ReelStrip>
  </ReelStripList>
  <ReelStripSetList>
    <ReelStripSet>
      <Identifier>Reels_BG_0</Identifier>
      <ReelStripIDList><ReelStripID>Reels_BG_0_0</ReelStripID></ReelStripIDList>
      <ReelStripVisSymbols>
        <ReelStripVisSymbolsHeight>3</ReelStripVisSymbolsHeight>
      </ReelStripVisSymbols>
    </ReelStripSet>
  </ReelStripSetList>
  <ComboSetList>
    <PaylineComboSet>
      <Identifier>PaylineComboSet_Main</Identifier>
      <PaylineComboList>
        <PaylineCombo>
          <SymbolList>
            <Symbol>AA</Symbol><Symbol>AA</Symbol><Symbol>ANY</Symbol>
          </SymbolList>
          <ComboID>2</ComboID><Group>100</Group><Value>10</Value>
        </PaylineCombo>
      </PaylineComboList>
    </PaylineComboSet>
    <ScatterComboSet>
      <Identifier>CountScatterComboSet_Main</Identifier>
      <ScatterComboList>
        <CountScatterCombo>
          <SymbolList><Symbol>FG</Symbol></SymbolList>
          <MinNumSymbols>3</MinNumSymbols><MaxNumSymbols>5</MaxNumSymbols>
          <ComboID>31</ComboID><Group>200</Group><Value>2</Value>
          <BaseMultiplier>TotalBet</BaseMultiplier><BonusCode>1</BonusCode>
        </CountScatterCombo>
      </ScatterComboList>
    </ScatterComboSet>
  </ComboSetList>
  <PaytableList>
    <Paytable>
      <Identifier>Paytable_Main</Identifier>
      <ComboSetIDList><ComboSet>PaylineComboSet_Main</ComboSet></ComboSetIDList>
    </Paytable>
  </PaytableList>
  <MysteryReplacementInfo>
    <ReplacementInstruction><DoReplace originalSymbol="MS" newSymbol="WC" /></ReplacementInstruction>
  </MysteryReplacementInfo>
</GameMath>
"""

# Values indented inside their tags, exactly as the shipped file writes them.
IDENTITY = """<?xml version="1.0" encoding="utf-8"?>
<GameConfig>
	<GameType>FortuneOx</GameType>
	<GameId>FortuneOx-1101YX-1c-90</GameId>
	<DisplayGameId>FortuneOx-40-900 88.01% MinTotalBet:88</DisplayGameId>
	<GamePct>88.01</GamePct>
	<MinimumMaxBet>88</MinimumMaxBet>
	<MinTotalBet>88</MinTotalBet>
	<NumberOfLines>40</NumberOfLines>
	<MinDenomMultiplier> 1 </MinDenomMultiplier>
	<DenomConfig>
		<Denom>1</Denom>
		<SpecificMaxBets>88 176 880</SpecificMaxBets>
	</DenomConfig>
	<DenomConfig>
		<Denom>5</Denom>
		<SpecificMaxBets>88 176 880</SpecificMaxBets>
	</DenomConfig>
</GameConfig>
"""

# paylineNumber 2 written before 0 and 1, which document order would preserve.
GEOMETRY = """<?xml version="1.0" encoding="utf-8"?>
<WinGeometryData>
  <PaylineSetList>
    <PaylineSet paylineSetID="040">
      <Payline paylineNumber="2">
        <PaylineElement reelIndex="0" position="2" />
        <PaylineElement reelIndex="1" position="2" />
      </Payline>
      <Payline paylineNumber="0">
        <PaylineElement reelIndex="0" position="1" />
        <PaylineElement reelIndex="1" position="1" />
      </Payline>
      <Payline paylineNumber="1">
        <PaylineElement reelIndex="0" position="0" />
        <PaylineElement reelIndex="1" position="0" />
      </Payline>
    </PaylineSet>
  </PaylineSetList>
</WinGeometryData>
"""


def write(path: Path, text: str, *, bom: bool = True) -> Path:
    """Write a maths file the way the game's build does: with a BOM."""
    path.write_text(text, encoding="utf-8-sig" if bom else "utf-8")
    return path


# --- math.xml -------------------------------------------------------------


@pytest.fixture
def math(tmp_path: Path):
    """One parsed ``math.xml``, BOM and namespace and all."""
    return load_game_math(write(tmp_path / "math.xml", MATH))


def test_reads_a_namespaced_file_written_with_a_bom(math) -> None:
    """Both are properties of every shipped file, and either one unhandled
    turns a working parse into "not valid XML" or an empty document."""
    assert math.game_id == "1101YX_1c_90"
    assert math.game_pct == 88.01
    assert math.game_base_pct == 82.90
    assert math.symbol_sets, "the namespace must not hide the symbol set"


def test_reads_the_default_configuration(math) -> None:
    """What the base game starts on, which is what selects everything else."""
    assert math.defaults.symbol_set_id == "SymbolSet_Main"
    assert math.defaults.reel_strip_set_id == "Reels_BG_0"
    assert math.defaults.paytable_id == "Paytable_Main"
    assert math.defaults.payline_set_id == "40"
    assert math.defaults.initial_stops == (0, 28)


def test_a_strip_keeps_its_stop_order_and_weights(math) -> None:
    """Stop order *is* the strip -- a set would lose the game entirely, and the
    weights run parallel rather than being folded into the symbols."""
    strip = math.reel_strip("Reels_BG_0_0")

    assert strip is not None
    assert strip.symbols == ("AA", "AA", "WC")
    assert strip.weights == (10, 9, 1)
    assert strip.length == 3
    assert strip.counts() == {"AA": 2, "WC": 1}


def test_roles_are_derived_because_the_file_declares_none(math) -> None:
    """math.xml never says "wild" or "scatter"; it says what substitutes and
    what is counted. Reading that back is what a symbol's *role* is -- its
    display name cannot come from here at all, since the file carries no such
    text in any element (see the game config's ``symbols`` block)."""
    assert math.roles() == {"WC": "wild", "AA": "regular", "FG": "scatter"}


def test_symbols_come_from_the_strips_not_the_declaration(math) -> None:
    """``SymbolSetList`` declares WC, AA and FG; only WC and AA are on a strip.

    A table built from the declaration would carry a row for a symbol no spin
    can produce, which is the whole reason this reads ``ReelStripList``.
    """
    usage = math.reel_symbols(base_set_id="Reels_BG_0")

    assert [item.code for item in usage] == ["AA", "WC"]
    assert "FG" in math.symbol_sets[0].symbols


def test_a_symbol_is_counted_on_the_base_reels_and_across_every_strip(
    math,
) -> None:
    """Two numbers, because 0 on the base reels beside a non-zero total is what
    tells a feature-only symbol from one this game does not have."""
    usage = {item.code: item for item in math.reel_symbols(base_set_id="Reels_BG_0")}

    assert usage["AA"].reel_stops == 2
    assert usage["AA"].total_stops == 2
    assert usage["AA"].strips == 1

    # Counted nowhere as a base-game stop: no set was named.
    unplaced = {item.code: item for item in math.reel_symbols()}
    assert unplaced["AA"].reel_stops == 0
    assert unplaced["AA"].total_stops == 2


def test_a_line_pay_is_credited_only_to_a_single_symbol_combo(math) -> None:
    """A mixed combo has no one symbol to credit, so it is left out rather than
    attributed to whichever code happened to come first."""
    assert math.top_line_pays() == {"AA": 10.0}


def test_line_pays_pivot_to_symbol_by_run_length(math) -> None:
    """A combo is one symbol repeated with an ``ANY`` tail, so every pay is a
    (symbol, run, value) triple -- which is what a paytable poster is a grid of.

    The fixture's only line combo is ``AA AA ANY``: two of a kind paying 10.
    """
    assert math.line_pays() == {"AA": {2: 10.0}}


def test_line_and_scatter_combos_are_read_apart(math) -> None:
    """They pay on different questions, so they are never one list."""
    assert len(math.payline_combos) == 1
    assert math.payline_combos[0].symbols == ("AA", "AA", "ANY")
    assert math.payline_combos[0].match_length == 2
    assert math.payline_combos[0].combo_set == "PaylineComboSet_Main"

    scatter = math.scatter_combos[0]
    assert scatter.symbols == ("FG",)
    assert (scatter.min_symbols, scatter.max_symbols) == (3, 5)
    assert scatter.base_multiplier == "TotalBet"
    assert scatter.bonus_code == 1


def test_a_symbol_set_can_be_looked_up_or_taken_when_it_is_the_only_one(math) -> None:
    """A file with one set needs no identifier to find it."""
    assert math.symbol_set("SymbolSet_Main") is math.symbol_sets[0]
    assert math.symbol_set(None) is math.symbol_sets[0]
    assert math.symbol_set("SymbolSet_Missing") is None


def test_a_reel_set_carries_its_visible_height(math) -> None:
    """How much of each reel is on screen, which is what makes a 5x3 a 5x3."""
    reels = math.reel_strip_set("Reels_BG_0")

    assert reels is not None
    assert reels.strip_ids == ("Reels_BG_0_0",)
    assert reels.visible_heights == (3,)


def test_a_file_that_is_not_maths_is_rejected_by_name(tmp_path: Path) -> None:
    """Pointing at the wrong XML should say so, not come back empty."""
    path = write(tmp_path / "math.xml", "<WinGeometryData />")

    with pytest.raises(GameMathError, match="not a <GameMath>"):
        load_game_math(path)


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("<GameMath><GamePct>lots</GamePct></GameMath>", "must be a number"),
        (
            "<GameMath><ReelStripList><ReelStrip><SymbolSetID>S</SymbolSetID>"
            "</ReelStrip></ReelStripList></GameMath>",
            "no <Identifier>",
        ),
    ],
    ids=["non-numeric", "unnamed-strip"],
)
def test_a_broken_file_is_an_error_not_a_guess(
    tmp_path: Path, text: str, match: str
) -> None:
    """Silently coercing would put a plausible wrong number on the page."""
    path = write(tmp_path / "math.xml", text)

    with pytest.raises(GameMathError, match=match):
        load_game_math(path)


def test_a_missing_math_file_names_the_path(tmp_path: Path) -> None:
    """The path is the useful half of the message -- it says which install."""
    with pytest.raises(GameMathError, match="No math file at"):
        load_game_math(tmp_path / "absent.xml")


# --- gameConfig.cfg -------------------------------------------------------


def test_reads_the_paytable_identity(tmp_path: Path) -> None:
    """Including the values the file indents inside their own tags."""
    identity = load_paytable_identity(write(tmp_path / "gameConfig.cfg", IDENTITY))

    assert identity.game_type == "FortuneOx"
    assert identity.game_id == "FortuneOx-1101YX-1c-90"
    assert identity.display_game_id.startswith("FortuneOx-40-900")
    assert identity.number_of_lines == 40
    assert identity.min_total_bet == 88
    assert identity.max_bets == (88, 176, 880)
    assert identity.denominations == (1.0, 5.0)
    # Indented inside its tag in the shipped file, and the one declared statement
    # of a denomination's amount -- the folder is `-1c-`, so it reads 1.
    assert identity.min_denom_multiplier == 1


def test_max_bets_are_split_on_whitespace(tmp_path: Path) -> None:
    """``SpecificMaxBets`` is one space-separated string, not a list of tags."""
    identity = load_paytable_identity(write(tmp_path / "gameConfig.cfg", IDENTITY))

    assert all(isinstance(bet, int) for bet in identity.max_bets)


# --- winGeometry.xml ------------------------------------------------------


@pytest.fixture
def geometry(tmp_path: Path):
    """One parsed ``winGeometry.xml``."""
    return load_win_geometry(write(tmp_path / "winGeometry.xml", GEOMETRY))


def test_paylines_come_back_in_line_order_whatever_the_file_did(geometry) -> None:
    """Document order is not line order, and a page listing line 3 first would
    be describing a different game."""
    lines = geometry.sets[0].paylines

    assert [line.number for line in lines] == [0, 1, 2]
    assert [line.label for line in lines] == [1, 2, 3]
    assert geometry.sets[0].line_count == 3


def test_a_line_converts_to_the_game_config_form(geometry) -> None:
    """The file is 0-indexed and reel-first; a game config's ``paylines`` block
    is 1-indexed and row-first. Without this the two cannot be compared."""
    line = geometry.sets[0].paylines[0]

    assert line.elements == ((0, 1), (1, 1))
    assert line.grid() == ((2, 1), (2, 2))


def test_a_set_is_found_by_its_number_not_its_spelling(geometry) -> None:
    """The file writes ``040`` and a paytable asks for ``40``; the same set."""
    assert geometry.set_for("40") is geometry.sets[0]
    assert geometry.set_for("040") is geometry.sets[0]
    assert geometry.set_for("20") is None
    assert geometry.set_for(None) is None


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("<GameMath />", "not a <WinGeometryData>"),
        ("<WinGeometryData />", "declares no payline set"),
        (
            "<WinGeometryData><PaylineSetList><PaylineSet />"
            "</PaylineSetList></WinGeometryData>",
            "no paylineSetID",
        ),
        (
            '<WinGeometryData><PaylineSetList><PaylineSet paylineSetID="5">'
            '<Payline paylineNumber="x" /></PaylineSet>'
            "</PaylineSetList></WinGeometryData>",
            "non-numeric paylineNumber",
        ),
    ],
    ids=["wrong-root", "empty", "unnamed-set", "non-numeric-line"],
)
def test_a_broken_geometry_file_is_an_error(
    tmp_path: Path, text: str, match: str
) -> None:
    """A geometry that cannot be read must not look like a game with no lines."""
    path = write(tmp_path / "winGeometry.xml", text)

    with pytest.raises(WinGeometryError, match=match):
        load_win_geometry(path)
