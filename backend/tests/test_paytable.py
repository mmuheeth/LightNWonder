"""Joining a running game to the maths it loaded.

Everything here runs off XML written into ``tmp_path``: a fake ``GameConfig``
tree with a folder per paytable, and a fake game log naming one of them. That
is deliberate -- the real files live in the game's own install, which is not
part of this repo and is not on a CI machine.

The join is the thing under test, not the XML parsing (that is
``test_game_math.py``'s job). So most assertions here are about *which* folder
was read and *how the answer says it got there*: a page showing the wrong
maths is either a stale log or a hand-typed id, and the response is supposed to
be able to tell you which.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.config.game_config import save_active_game
from app.core.config import settings
from tests.asserts import assert_failure, assert_success

API = "/api/paytable"

MATH_XML = """<?xml version="1.0" encoding="utf-8"?>
<GameMath xmlns="http://scientificgames.com/slotMathXMLSchema.xsd">
  <GameId>{game_id}</GameId>
  <GamePct>88.01</GamePct>
  <GameBasePct>82.90</GameBasePct>
  <DefaultConfiguration>
    <SymbolSetID>SymbolSet_Main</SymbolSetID>
    <ReelStripSetID>Reels_BG_0</ReelStripSetID>
    <PaytableID>Paytable_Main</PaytableID>
    <PaylineSetID>{math_lines}</PaylineSetID>
    <InitialStops><Position>0</Position><Position>7</Position></InitialStops>
  </DefaultConfiguration>
  <SymbolSetList>
    <SymbolSet>
      <Identifier>SymbolSet_Main</Identifier>
      <SymbolList>
        <Symbol>WC</Symbol><Symbol>AA</Symbol><Symbol>FF</Symbol><Symbol>FG</Symbol>
      </SymbolList>
      <WildSymbolList>
        <WildSymbol>
          <Identifier>WC</Identifier>
          <SymbolList><Symbol>AA</Symbol><Symbol>FF</Symbol></SymbolList>
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
        <WeightedElement><Weight>10</Weight><StringValue>FF</StringValue></WeightedElement>
        <WeightedElement><Weight>1</Weight><StringValue>WC</StringValue></WeightedElement>
      </WeightedElementList>
    </ReelStrip>
    <ReelStrip>
      <Identifier>Reels_BG_0_1</Identifier>
      <SymbolSetID>SymbolSet_Main</SymbolSetID>
      <WeightedElementList>
        <WeightedElement><Weight>1</Weight><StringValue>AA</StringValue></WeightedElement>
        <WeightedElement><Weight>1</Weight><StringValue>FG</StringValue></WeightedElement>
      </WeightedElementList>
    </ReelStrip>
    <ReelStrip>
      <Identifier>Reels_FG_0</Identifier>
      <SymbolSetID>SymbolSet_Main</SymbolSetID>
      <WeightedElementList>
        <WeightedElement><Weight>1</Weight><StringValue>FF</StringValue></WeightedElement>
      </WeightedElementList>
    </ReelStrip>
  </ReelStripList>
  <ReelStripSetList>
    <ReelStripSet>
      <Identifier>Reels_BG_0</Identifier>
      <ReelStripIDList>
        <ReelStripID>Reels_BG_0_0</ReelStripID>
        <ReelStripID>Reels_BG_0_1</ReelStripID>
      </ReelStripIDList>
      <ReelStripVisSymbols>
        <ReelStripVisSymbolsHeight>3</ReelStripVisSymbolsHeight>
        <ReelStripVisSymbolsHeight>3</ReelStripVisSymbolsHeight>
      </ReelStripVisSymbols>
    </ReelStripSet>
    <ReelStripSet>
      <Identifier>Reels_FG</Identifier>
      <ReelStripIDList><ReelStripID>Reels_FG_0</ReelStripID></ReelStripIDList>
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
        <PaylineCombo>
          <SymbolList>
            <Symbol>WC</Symbol><Symbol>WC</Symbol><Symbol>WC</Symbol>
          </SymbolList>
          <ComboID>1</ComboID><Group>100</Group><Value>250</Value>
        </PaylineCombo>
      </PaylineComboList>
    </PaylineComboSet>
    <ScatterComboSet>
      <Identifier>CountScatterComboSet_Main</Identifier>
      <ScatterComboList>
        <CountScatterCombo>
          <SymbolList><Symbol>FG</Symbol></SymbolList>
          <MinNumSymbols>3</MinNumSymbols><MaxNumSymbols>3</MaxNumSymbols>
          <ComboID>31</ComboID><Group>200</Group><Value>2</Value>
          <BaseMultiplier>TotalBet</BaseMultiplier><BonusCode>1</BonusCode>
        </CountScatterCombo>
      </ScatterComboList>
    </ScatterComboSet>
  </ComboSetList>
  <PaytableList>
    <Paytable>
      <Identifier>Paytable_Main</Identifier>
      <ComboSetIDList>
        <ComboSet>PaylineComboSet_Main</ComboSet>
        <ComboSet>CountScatterComboSet_Main</ComboSet>
      </ComboSetIDList>
    </Paytable>
  </PaytableList>
</GameMath>
"""

IDENTITY_CFG = """<?xml version="1.0" encoding="utf-8"?>
<GameConfig>
  <GameType>FortuneOx</GameType>
  <GameId>{game_id}</GameId>
  <DisplayGameId>FortuneOx-{lines}-900 88.01%</DisplayGameId>
  <GamePct>88.01</GamePct>
  <NumberOfLines>{lines}</NumberOfLines>
  <MinTotalBet>88</MinTotalBet>
  <DenomConfig>
    <Denom>1</Denom>
    <SpecificMaxBets>88 176 880</SpecificMaxBets>
  </DenomConfig>
</GameConfig>
"""

# Two sets, so choosing between them is a real choice. Line 0 of the 5-set is
# the middle row straight across; line 1 of the 20-set is the top row -- picking
# the wrong set is visible in the geometry, not just in a count.
GEOMETRY_XML = """<?xml version="1.0" encoding="utf-8"?>
<WinGeometryData>
  <PaylineSetList>
    <PaylineSet paylineSetID="20">
      <Payline paylineNumber="1">
        <PaylineElement reelIndex="0" position="0" />
        <PaylineElement reelIndex="1" position="0" />
      </Payline>
      <Payline paylineNumber="0">
        <PaylineElement reelIndex="0" position="1" />
        <PaylineElement reelIndex="1" position="1" />
      </Payline>
    </PaylineSet>
    <PaylineSet paylineSetID="5">
      <Payline paylineNumber="0">
        <PaylineElement reelIndex="0" position="1" />
        <PaylineElement reelIndex="1" position="1" />
      </Payline>
    </PaylineSet>
  </PaylineSetList>
</WinGeometryData>
"""

LOGGED = "FortuneOx-1101YX-1c-90"
OTHER = "FortuneOx-1102RX-100c-90"


def log_line(paytable: str, *, denom: str = "1.000") -> str:
    """One real ``UpdatePayTable`` line, which is where the id comes from."""
    return (
        "08/24/26 19:06:03.063 01 FortuneOx:22372 DBG: [WagerGameApp.UpdatePayTable] "
        f"current denom[{denom}] current paytableId[{paytable}] "
        "current supported denoms[1.000,2.000]"
    )


def write_paytable(
    root: Path,
    paytable_id: str,
    *,
    lines: int | None = 5,
    math_lines: str = "20",
    math: str | None = None,
) -> Path:
    """Write one paytable folder: its maths, and its identity when it has one."""
    directory = root / paytable_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "math.xml").write_text(
        math
        if math is not None
        else MATH_XML.format(game_id=paytable_id, math_lines=math_lines),
        encoding="utf-8-sig",
    )
    if lines is not None:
        (directory / "gameConfig.cfg").write_text(
            IDENTITY_CFG.format(game_id=paytable_id, lines=lines), encoding="utf-8-sig"
        )
    return directory


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """A game install: two paytable folders and the geometry they share."""
    root = tmp_path / "GameConfig"
    write_paytable(root, LOGGED, lines=5)
    write_paytable(root, OTHER, lines=20)
    (root / "winGeometry.xml").write_text(GEOMETRY_XML, encoding="utf-8-sig")
    return root


@pytest.fixture
def game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Make one game config active, and return the factory that wrote it."""

    def activate(document: dict[str, Any]) -> Path:
        directory = tmp_path / "games"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{document['name']}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        selection = tmp_path / "active_game.json"
        save_active_game(selection, document["name"])
        monkeypatch.setattr(
            type(settings), "ideck_game_config_dir", property(lambda _self: directory)
        )
        monkeypatch.setattr(
            type(settings), "ideck_active_game_path", property(lambda _self: selection)
        )
        return path

    return activate


@pytest.fixture
def active_game(game, install: Path, tmp_path: Path):
    """A game pointed at the install, with a log naming one of its paytables."""

    def activate(*, log: str | None = LOGGED, **overrides: Any) -> Path:
        document: dict[str, Any] = {
            "name": "FortuneOx",
            "process": "FortuneOx.exe",
            "game_config": str(install),
            "symbols": {"AA": "Ox", "FF": "King"},
        }
        if log is not None:
            log_path = tmp_path / "FortuneOx_Client.log"
            log_path.write_text(
                "\n".join(
                    ["08/24/26 19:06:02.000 00 FortuneOx:1 DBG: noise", log_line(log)]
                ),
                encoding="utf-8",
            )
            document["log"] = str(log_path)
        document.update(overrides)
        return game(document)

    return activate


# --- resolving which paytable --------------------------------------------


async def test_the_log_names_the_folder_that_is_read(
    client: AsyncClient, active_game
) -> None:
    """The whole join: a line in the game's log picks a directory on disk."""
    active_game()

    data = assert_success((await client.get(f"{API}/")).json())

    assert data["paytable_id"] == LOGGED
    assert data["directory"].endswith(LOGGED)
    assert data["math"]["game_id"] == LOGGED
    assert data["identity"]["game_id"] == LOGGED


async def test_the_log_line_also_says_which_denominations_are_available(
    client: AsyncClient, active_game
) -> None:
    """Each denomination loads a different paytable folder, so the same line
    that names the current one names the maths this session can move between."""
    active_game()

    source = assert_success((await client.get(f"{API}/")).json())["source"]

    assert source["denomination"] == "1.000"
    assert source["supported_denominations"] == ["1.000", "2.000"]


async def test_the_answer_says_it_came_from_the_log(
    client: AsyncClient, active_game
) -> None:
    """Evidence, not just a verdict -- the line itself travels on the response,
    so a stale log is visible as a stale timestamp rather than as wrong maths."""
    active_game()

    source = assert_success((await client.get(f"{API}/")).json())["source"]

    assert source["origin"] == "log"
    assert source["log_line"] == log_line(LOGGED)
    assert source["logged_at"].startswith("2026-08-24T19:06:03")
    assert source["denomination"] == "1.000"


async def test_the_newest_line_wins(
    client: AsyncClient, active_game, tmp_path: Path
) -> None:
    """A session that changed denomination loaded a second paytable; the page
    is about the one loaded now, not the one loaded first."""
    active_game()
    log = tmp_path / "FortuneOx_Client.log"
    log.write_text("\n".join([log_line(OTHER), log_line(LOGGED)]), encoding="utf-8")

    data = assert_success((await client.get(f"{API}/")).json())

    assert data["paytable_id"] == LOGGED


async def test_a_requested_id_overrides_the_log(
    client: AsyncClient, active_game
) -> None:
    """Any paytable the game ships can be inspected without running it."""
    active_game()

    data = assert_success((await client.get(f"{API}/?paytable_id={OTHER}")).json())

    assert data["paytable_id"] == OTHER
    assert data["source"]["origin"] == "requested"
    assert data["source"]["log_line"] is None


async def test_every_paytable_of_the_game_is_listed(
    client: AsyncClient, active_game
) -> None:
    """``available`` is what makes the override usable from the page."""
    active_game()

    data = assert_success((await client.get(f"{API}/")).json())

    assert data["available"] == sorted([LOGGED, OTHER])


async def test_a_directory_without_maths_is_not_a_paytable(
    client: AsyncClient, active_game, install: Path
) -> None:
    """The GameConfig root holds loose files and the odd tooling directory."""
    (install / "Tools").mkdir()
    active_game()

    data = assert_success((await client.get(f"{API}/")).json())

    assert data["available"] == sorted([LOGGED, OTHER])


async def test_the_only_paytable_is_used_when_the_log_is_silent(
    client: AsyncClient, game, tmp_path: Path
) -> None:
    """A game that has never been run still has exactly one answer possible."""
    root = tmp_path / "SoloConfig"
    write_paytable(root, LOGGED)
    (root / "winGeometry.xml").write_text(GEOMETRY_XML, encoding="utf-8-sig")
    game({"name": "FortuneOx", "game_config": str(root)})

    data = assert_success((await client.get(f"{API}/")).json())

    assert data["paytable_id"] == LOGGED
    assert data["source"]["origin"] == "only"


async def test_a_silent_log_with_several_paytables_asks_for_an_id(
    client: AsyncClient, active_game
) -> None:
    """Guessing between 53 folders would be worse than saying so."""
    active_game(log=None)

    response = await client.get(f"{API}/")

    assert response.status_code == 404
    payload = response.json()
    assert_failure(payload, code="PAYTABLE_NOT_FOUND")
    assert "ask for one by id" in payload["message"]


async def test_an_id_the_install_does_not_have_names_both_halves(
    client: AsyncClient, active_game, tmp_path: Path
) -> None:
    """The interesting failure: the log is right and the install is incomplete.
    Saying only "not found" would leave the reader unable to tell which."""
    active_game()
    (tmp_path / "FortuneOx_Client.log").write_text(
        log_line("FortuneOx-9999XX-1c-90"), encoding="utf-8"
    )

    response = await client.get(f"{API}/")

    assert response.status_code == 404
    payload = response.json()
    assert_failure(payload, code="PAYTABLE_NOT_FOUND")
    assert "FortuneOx-9999XX-1c-90" in payload["message"]
    assert "has no folder of that name" in payload["message"]


@pytest.mark.parametrize(
    "requested", ["../escape", "sub/escape", "..\\escape", "C:/escape", ".."]
)
async def test_a_requested_id_cannot_escape_the_install(
    client: AsyncClient, active_game, requested: str
) -> None:
    """The id comes off the wire, so it goes through the same guard as a
    screenshot filename."""
    active_game()

    response = await client.get(f"{API}/", params={"paytable_id": requested})

    assert response.status_code == 404
    assert_failure(response.json(), code="PAYTABLE_NOT_FOUND")


async def test_a_game_that_declares_no_install_is_a_409(
    client: AsyncClient, game
) -> None:
    """409, not 404: the machine is missing the game, not the request an id.
    Every other dashboard slice works without the game installed."""
    game({"name": "HuffNPuffLink", "process": "HuffNPuffLink.exe"})

    response = await client.get(f"{API}/")

    assert response.status_code == 409
    payload = response.json()
    assert_failure(payload, code="PAYTABLE_UNAVAILABLE")
    assert "game_config" in payload["message"]


async def test_an_install_that_is_not_there_is_a_409(
    client: AsyncClient, game, tmp_path: Path
) -> None:
    """Same reason: the path is declared but the game was never installed."""
    game({"name": "FortuneOx", "game_config": str(tmp_path / "nowhere")})

    response = await client.get(f"{API}/")

    assert response.status_code == 409
    assert_failure(response.json(), code="PAYTABLE_UNAVAILABLE")


async def test_unreadable_maths_is_a_502(
    client: AsyncClient, active_game, install: Path
) -> None:
    """502: the files are present but the game wrote something we cannot read,
    which is neither the caller's fault nor a missing install."""
    write_paytable(install, LOGGED, math="<GameMath><unclosed>")
    active_game()

    response = await client.get(f"{API}/")

    assert response.status_code == 502
    assert_failure(response.json(), code="PAYTABLE_INVALID")


# --- what the maths says --------------------------------------------------


async def test_a_symbol_row_joins_the_config_name_to_the_maths(
    client: AsyncClient, active_game
) -> None:
    """The two halves come from different files on purpose: math.xml has the
    codes, the pays and the reel counts and no names at all; the game config has
    the names and nothing else."""
    active_game()

    symbols = assert_success((await client.get(f"{API}/")).json())["math"]["symbols"]
    by_code = {symbol["code"]: symbol for symbol in symbols}

    assert by_code["AA"] == {
        "code": "AA",
        "name": "Ox",
        "role": "regular",
        "substitutes": [],
        "top_pay": 10.0,
        "reel_stops": 2,
        "total_stops": 2,
        "strips": 2,
        "on_reels": True,
    }
    assert by_code["WC"]["substitutes"] == ["AA", "FF"]
    assert by_code["FG"]["role"] == "scatter"


async def test_an_unnamed_code_falls_back_to_what_the_maths_implies(
    client: AsyncClient, active_game
) -> None:
    """A game whose config names nothing yet is still readable: the structure
    says which codes substitute and which are counted, and that is a better
    label than a bare code."""
    active_game(symbols={})

    symbols = assert_success((await client.get(f"{API}/")).json())["math"]["symbols"]
    by_code = {symbol["code"]: symbol for symbol in symbols}

    assert by_code["WC"]["name"] == "Wild"
    assert by_code["FG"]["name"] == "Scatter"
    # Nothing in either file can name a plain line symbol.
    assert by_code["AA"]["name"] is None


async def test_symbols_are_ordered_by_what_they_pay(
    client: AsyncClient, active_game
) -> None:
    """Pay order is how a paytable reads. Symbols no line combo pays for sort
    last rather than first, which a plain descending sort on null would do."""
    active_game()

    symbols = assert_success((await client.get(f"{API}/")).json())["math"]["symbols"]

    assert [symbol["code"] for symbol in symbols] == ["WC", "AA", "FF", "FG"]
    assert symbols[-1]["top_pay"] is None


async def test_a_symbol_is_counted_on_the_base_reels_and_everywhere(
    client: AsyncClient, active_game
) -> None:
    """Two numbers, because 0 on the base reels beside a non-zero total is what
    tells a feature-only symbol from one this game does not have at all."""
    active_game()

    symbols = assert_success((await client.get(f"{API}/")).json())["math"]["symbols"]
    by_code = {symbol["code"]: symbol for symbol in symbols}

    # Reels_BG_0 is AA/FF/WC and AA/FG; Reels_FG's lone FF is outside it.
    assert by_code["AA"]["reel_stops"] == 2
    assert by_code["FF"]["reel_stops"] == 1
    assert by_code["FF"]["total_stops"] == 2
    assert by_code["FF"]["strips"] == 2


async def test_line_pays_come_back_pivoted_into_a_paytable(
    client: AsyncClient, active_game
) -> None:
    """A row per symbol and a column per run length, because that is what a
    paytable is -- the combos in their declared order are three unordered rows
    per symbol instead."""
    active_game()

    math = assert_success((await client.get(f"{API}/")).json())["math"]

    # WC pays 250 for three; AA pays 10 for two. Longest run first.
    assert math["pay_lengths"] == [3, 2]
    assert math["pay_table"] == [
        {"codes": ["WC"], "names": ["Wild"], "values": [250.0, None], "top_pay": 250.0},
        {"codes": ["AA"], "names": ["Ox"], "values": [None, 10.0], "top_pay": 10.0},
    ]
    # The faithful reading is still there for anything the pivot cannot hold.
    assert len(math["payline_combos"]) == 2


async def test_reel_strips_lead_with_the_reels_the_base_game_spins(
    client: AsyncClient, active_game
) -> None:
    """A page opening on a feature strip would be showing the wrong reels."""
    active_game()

    strips = assert_success((await client.get(f"{API}/")).json())["math"]["reel_strips"]

    assert [strip["identifier"] for strip in strips] == [
        "Reels_BG_0_0",
        "Reels_BG_0_1",
        "Reels_FG_0",
    ]
    assert strips[0]["reel_index"] == 0
    assert strips[1]["reel_index"] == 1
    assert strips[0]["symbols"] == ["AA", "FF", "WC"]
    assert strips[0]["weights"] == [10, 10, 1]
    assert strips[0]["length"] == 3
    assert strips[0]["truncated"] is False


async def test_a_strip_longer_than_the_cap_says_it_was_cut(
    client: AsyncClient, active_game, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silent truncation would read as a two-stop reel."""
    monkeypatch.setattr(settings, "PAYTABLE_MAX_STRIP_STOPS", 2)
    active_game()

    strips = assert_success((await client.get(f"{API}/")).json())["math"]["reel_strips"]

    assert strips[0]["symbols"] == ["AA", "FF"]
    assert strips[0]["length"] == 3
    assert strips[0]["truncated"] is True


async def test_line_combos_come_back_best_first_and_named(
    client: AsyncClient, active_game
) -> None:
    """Document order is not pay order in every file, and ``ANY`` is the tail
    rather than a symbol -- both are things a combo table gets wrong."""
    active_game()

    combos = assert_success((await client.get(f"{API}/")).json())["math"][
        "payline_combos"
    ]

    assert [combo["value"] for combo in combos] == [250.0, 10.0]
    assert combos[0]["symbols"] == ["WC", "WC", "WC"]
    assert combos[0]["names"] == ["Wild", "Wild", "Wild"]
    assert combos[0]["match_length"] == 3
    assert combos[1]["symbols"] == ["AA", "AA", "ANY"]
    assert combos[1]["names"] == ["Ox", "Ox", None]
    assert combos[1]["match_length"] == 2


async def test_scatter_combos_are_kept_apart_from_line_combos(
    client: AsyncClient, active_game
) -> None:
    """They pay on a count anywhere, not on a line; one table for both would be
    a table whose rows mean two different things."""
    active_game()

    math = assert_success((await client.get(f"{API}/")).json())["math"]

    assert [combo["symbols"] for combo in math["payline_combos"]] != [["FG"]]
    assert math["scatter_combos"] == [
        {
            "combo_id": 31,
            "combo_set": "CountScatterComboSet_Main",
            "group": 200,
            "value": 2.0,
            "symbols": ["FG"],
            "names": ["Scatter"],
            "min_symbols": 3,
            "max_symbols": 3,
            "base_multiplier": "TotalBet",
            "bonus_code": 1,
        }
    ]


# --- win geometry ---------------------------------------------------------


async def test_the_paytable_picks_the_payline_set_not_the_maths_default(
    client: AsyncClient, active_game
) -> None:
    """The same math.xml ships in folders that play 5, 20 and 40 lines, so its
    own default cannot be the answer -- ``NumberOfLines`` is."""
    active_game()

    geometry = assert_success((await client.get(f"{API}/")).json())["win_geometry"]

    assert geometry["payline_set_id"] == "5"
    assert geometry["resolved_from"] == "game_config"
    assert geometry["line_count"] == 1
    assert [s["payline_set_id"] for s in geometry["sets"] if s["is_applicable"]] == [
        "5"
    ]


async def test_the_maths_default_is_the_fallback(
    client: AsyncClient, active_game, install: Path
) -> None:
    """A folder shipping no ``gameConfig.cfg`` still has an answerable question."""
    (install / LOGGED / "gameConfig.cfg").unlink()
    write_paytable(install, LOGGED, lines=None, math_lines="20")
    active_game()

    data = assert_success((await client.get(f"{API}/")).json())

    assert data["identity"] is None
    assert data["win_geometry"]["payline_set_id"] == "20"
    assert data["win_geometry"]["resolved_from"] == "math_default"
    assert data["win_geometry"]["line_count"] == 2


async def test_paylines_come_back_in_line_order_and_in_both_forms(
    client: AsyncClient, active_game
) -> None:
    """The file is 0-indexed and reel-first, a game config is 1-indexed and
    row-first. Sending both is what lets the two be compared at all."""
    active_game()

    data = assert_success((await client.get(f"{API}/?paytable_id={OTHER}")).json())
    paylines = data["win_geometry"]["paylines"]

    assert [line["line"] for line in paylines] == [1, 2]
    assert paylines[0]["number"] == 0
    assert paylines[0]["elements"] == [[0, 1], [1, 1]]
    assert paylines[0]["grid"] == [[2, 1], [2, 2]]
    assert paylines[1]["grid"] == [[1, 1], [1, 2]]


async def test_missing_geometry_does_not_lose_the_rest_of_the_page(
    client: AsyncClient, active_game, install: Path
) -> None:
    """Three of the four tables are still worth reading, so the failure is
    carried as an error on a 200 rather than raised."""
    (install / "winGeometry.xml").unlink()
    active_game()

    data = assert_success((await client.get(f"{API}/")).json())

    assert data["math"]["symbols"], "the maths should still be there"
    assert data["win_geometry"]["error"] is not None
    assert data["win_geometry"]["line_count"] is None
    assert data["win_geometry"]["sets"] == []


async def test_a_payline_set_the_geometry_does_not_declare_is_reported(
    client: AsyncClient, active_game, install: Path
) -> None:
    """A 40-line paytable against a geometry file that only knows 5 and 20 is a
    real mismatch, and one that a bare empty list would hide."""
    write_paytable(install, LOGGED, lines=40)
    active_game()

    geometry = assert_success((await client.get(f"{API}/")).json())["win_geometry"]

    assert geometry["payline_set_id"] == "40"
    assert geometry["paylines"] == []
    assert "No payline set '40'" in geometry["error"]


# --- caching --------------------------------------------------------------


async def test_a_rewritten_math_file_is_read_again(
    client: AsyncClient, active_game, install: Path
) -> None:
    """Parsed files are cached against their mtime, and a game reinstalled
    under the same name must not keep serving the old maths."""
    active_game()
    first = assert_success((await client.get(f"{API}/")).json())
    assert first["math"]["defaults"]["payline_set_id"] == "20"

    write_paytable(install, LOGGED, lines=5, math_lines="5")

    second = assert_success((await client.get(f"{API}/")).json())
    assert second["math"]["defaults"]["payline_set_id"] == "5"
