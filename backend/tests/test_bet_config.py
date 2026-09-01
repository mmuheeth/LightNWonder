"""Reading the two files that say what a spin costs.

Another pair of foreign formats, so the fixtures below are the shipped files
byte for byte -- tags, indentation and all. Only two numbers are read out of
them, and what they are *for* is one multiplication: ``cost x bet_per_unit`` is
the total bet, and ``bet_per_unit`` is what a paytable's line values are priced
by. ``tests/test_paytable.py`` covers what the service does with the result.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.utils.bet_config import BetConfigError, load_bet_ladder, load_unit_costs

# FortuneOx-1101YX-1c-90/betPerUnitConfig.xml, verbatim -- leading comment,
# tagged mapping and all.
FORTUNE_OX_PER_UNIT = """<!--    tag "1" regular bet scheme for 40 lines
        tag "2" enhance bet scheme for 40 lines
 -->
<BetPerUnitData>
  <BetPerUnitConfigurationList>
    <BetPerUnitConfiguration>
      <MinBetPerUnit>1</MinBetPerUnit>
      <MaxBetPerUnit>10</MaxBetPerUnit>

      <BetPerUnitMappings tag="1">
        <BetPerUnit>1</BetPerUnit>
        <BetPerUnit>2</BetPerUnit>
        <BetPerUnit>3</BetPerUnit>
        <BetPerUnit>5</BetPerUnit>
        <BetPerUnit>10</BetPerUnit>
      </BetPerUnitMappings>

    </BetPerUnitConfiguration>
  </BetPerUnitConfigurationList>
</BetPerUnitData>"""

# FortuneOx-1101YX-1c-90/betUnitConfig.xml, verbatim -- note the tab indent.
FORTUNE_OX_UNITS = """<BetUnitData>
  <UnitConfigurationList>
	<UnitConfiguration numUnits="40">
        <UnitSelectMappings tag="1">
            <UnitSelectData units="40" cost="88"/>
        </UnitSelectMappings>
    </UnitConfiguration>
  </UnitConfigurationList>
</BetUnitData>"""

# HuffNPuffLink-1D8XXX/betPerUnitConfig.xml, verbatim. The comment is the
# file's own account of why the untagged entry exists.
HUFF_PER_UNIT = """<!--
- added 'tag's match between betPerUnitConfig and betUnitConfig.xml
- Each configuration's key doesn't change
- Each entry has a default (currently untagged)
- Shouldn't break other games
- MaxBetPerUnit is the highest value acress all mappings
-->
<BetPerUnitData>

    <BetPerUnitConfigurationList>

        <BetPerUnitConfiguration>
          <MinBetPerUnit>1</MinBetPerUnit>
          <MaxBetPerUnit>8</MaxBetPerUnit>
          <BetPerUnitMappings>
            <BetPerUnit>1</BetPerUnit>
            <BetPerUnit>2</BetPerUnit>
            <BetPerUnit>3</BetPerUnit>
            <BetPerUnit>5</BetPerUnit>
            <BetPerUnit>8</BetPerUnit>
          </BetPerUnitMappings>
        </BetPerUnitConfiguration>

    </BetPerUnitConfigurationList>

</BetPerUnitData>"""

HUFF_UNITS = """<BetUnitData>

  <UnitConfigurationList>

    <UnitConfiguration numUnits="243">
      <UnitSelectMappings>
        <UnitSelectData units="243" cost="100"/>
      </UnitSelectMappings>
    </UnitConfiguration>
  </UnitConfigurationList>

</BetUnitData>"""


def write(path: Path, text: str) -> Path:
    """Write a bet config the way the game's build does."""
    path.write_text(text, encoding="utf-8")
    return path


# --- the ladder -----------------------------------------------------------


def test_the_ladder_is_read_in_file_order(tmp_path: Path) -> None:
    """The rungs are the ladder a player steps through, so their order is data.

    1, 2, 3, 5, 10 -- there is no 4, which is why this can never be a range.
    """
    path = write(tmp_path / "betPerUnitConfig.xml", FORTUNE_OX_PER_UNIT)

    assert load_bet_ladder(path) == (1, 2, 3, 5, 10)


def test_an_untagged_mapping_reads_the_same(tmp_path: Path) -> None:
    """HuffNPuffLink leaves the tag off and FortuneOx does not, which changes
    nothing about the rungs -- so neither file has to be asked about its tag."""
    path = write(tmp_path / "betPerUnitConfig.xml", HUFF_PER_UNIT)

    assert load_bet_ladder(path) == (1, 2, 3, 5, 8)


def test_a_file_with_no_rungs_is_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path / "betPerUnitConfig.xml",
        "<BetPerUnitData><BetPerUnitConfigurationList/></BetPerUnitData>",
    )

    with pytest.raises(BetConfigError, match="no <BetPerUnitMappings>"):
        load_bet_ladder(path)


def test_a_non_numeric_rung_is_refused(tmp_path: Path) -> None:
    """Silently dropping it would shorten the ladder, which is what a bet read
    off the meter is checked against."""
    path = write(
        tmp_path / "betPerUnitConfig.xml",
        "<BetPerUnitData><BetPerUnitConfiguration>"
        "<BetPerUnitMappings><BetPerUnit>two</BetPerUnit></BetPerUnitMappings>"
        "</BetPerUnitConfiguration></BetPerUnitData>",
    )

    with pytest.raises(BetConfigError, match="not a whole number"):
        load_bet_ladder(path)


def test_the_wrong_root_names_what_it_got(tmp_path: Path) -> None:
    path = write(tmp_path / "betPerUnitConfig.xml", "<BetUnitData/>")

    with pytest.raises(BetConfigError, match="<BetUnitData>, not a <BetPerUnitData>"):
        load_bet_ladder(path)


def test_a_missing_file_is_a_config_error_not_an_oserror(tmp_path: Path) -> None:
    """The service turns this into an `error` on a 200, so it has to be the
    reader's own exception type."""
    with pytest.raises(BetConfigError, match="No bet per unit file"):
        load_bet_ladder(tmp_path / "absent.xml")


# --- what a spin costs ----------------------------------------------------


def test_the_cost_is_read_against_its_line_count(tmp_path: Path) -> None:
    """88 credits for 40 units is the number every FortuneOx line value is
    multiplied against -- and the `MinTotalBet` its gameConfig.cfg declares."""
    path = write(tmp_path / "betUnitConfig.xml", FORTUNE_OX_UNITS)

    assert load_unit_costs(path) == ((40, 88),)


def test_every_block_is_kept_so_the_line_count_can_pick_one(tmp_path: Path) -> None:
    """The same file ships in folders playing different line counts -- 40 lines
    at 88, 20 at 50 -- and only the paytable knows which it is."""
    path = write(
        tmp_path / "betUnitConfig.xml",
        "<BetUnitData><UnitConfigurationList>"
        '<UnitConfiguration numUnits="40"><UnitSelectMappings>'
        '<UnitSelectData units="40" cost="88"/></UnitSelectMappings></UnitConfiguration>'
        '<UnitConfiguration numUnits="20"><UnitSelectMappings>'
        '<UnitSelectData units="20" cost="50"/></UnitSelectMappings></UnitConfiguration>'
        "</UnitConfigurationList></BetUnitData>",
    )

    assert load_unit_costs(path) == ((40, 88), (20, 50))


def test_an_untagged_selection_reads_the_same(tmp_path: Path) -> None:
    path = write(tmp_path / "betUnitConfig.xml", HUFF_UNITS)

    assert load_unit_costs(path) == ((243, 100),)


def test_a_non_numeric_cost_is_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path / "betUnitConfig.xml",
        "<BetUnitData><UnitConfiguration><UnitSelectMappings>"
        '<UnitSelectData units="40" cost="lots"/>'
        "</UnitSelectMappings></UnitConfiguration></BetUnitData>",
    )

    with pytest.raises(BetConfigError, match="non-numeric cost"):
        load_unit_costs(path)


def test_a_file_with_no_cost_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path / "betUnitConfig.xml", "<BetUnitData/>")

    with pytest.raises(BetConfigError, match="no unit cost"):
        load_unit_costs(path)


# --- the pair -------------------------------------------------------------


def test_the_two_files_multiply_out_to_the_declared_bet_ladder(tmp_path: Path) -> None:
    """The claim the pair exists to support, checked against the third statement
    of it: `gameConfig.cfg`'s own `SpecificMaxBets` reads `88 176 264 440 880`."""
    ladder = load_bet_ladder(
        write(tmp_path / "betPerUnitConfig.xml", FORTUNE_OX_PER_UNIT)
    )
    cost = load_unit_costs(write(tmp_path / "betUnitConfig.xml", FORTUNE_OX_UNITS))[0][
        1
    ]

    assert [cost * rung for rung in ladder] == [88, 176, 264, 440, 880]
