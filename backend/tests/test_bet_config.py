"""Reading the two files that say what a spin costs.

Another pair of foreign formats, so the fixtures below are the shipped files
byte for byte -- including the parts that differ between the two games, which is
the whole reason this reader is more forgiving than it looks:

* FortuneOx **tags** its mappings (``tag="1"``); HuffNPuffLink leaves them
  untagged and its own comment calls the untagged entry the default.
* The two indent the containers differently, and HuffNPuffLink separates every
  block with blank lines.

What the pair is *for* is one multiplication -- ``cost x bet_per_unit`` is the
total bet, and ``bet_per_unit`` is what a paytable's line values are priced by.
``tests/test_paytable.py`` covers what the service does with the result.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.utils.bet_config import (
    BetConfigError,
    load_bet_per_unit_config,
    load_bet_unit_config,
)

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


# --- betPerUnitConfig.xml -------------------------------------------------


def test_the_tagged_ladder_is_read_in_file_order(tmp_path: Path) -> None:
    """The rungs are the ladder a player steps through, so their order is data.

    1, 2, 3, 5, 10 -- there is no 4, which is why this can never be a range.
    """
    config = load_bet_per_unit_config(
        write(tmp_path / "betPerUnitConfig.xml", FORTUNE_OX_PER_UNIT)
    )

    assert config.minimum == 1
    assert config.maximum == 10
    assert config.ladder("1") == (1, 2, 3, 5, 10)


def test_an_untagged_mapping_is_the_default(tmp_path: Path) -> None:
    """HuffNPuffLink's shape, and the file's own comment says so. A caller with
    no tag to offer still gets the ladder."""
    config = load_bet_per_unit_config(
        write(tmp_path / "betPerUnitConfig.xml", HUFF_PER_UNIT)
    )

    assert config.maximum == 8
    assert config.ladder() == (1, 2, 3, 5, 8)
    # A tag that names nothing falls through to the untagged default rather
    # than coming back empty.
    assert config.ladder("1") == (1, 2, 3, 5, 8)


def test_a_lone_tagged_mapping_answers_a_caller_with_no_tag(tmp_path: Path) -> None:
    """FortuneOx tags its only mapping, so "the tagged one" and "the only one"
    are the same ladder -- a reader should not need to know the tag."""
    config = load_bet_per_unit_config(
        write(tmp_path / "betPerUnitConfig.xml", FORTUNE_OX_PER_UNIT)
    )

    assert config.ladder() == (1, 2, 3, 5, 10)


def test_a_file_with_no_mappings_is_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path / "betPerUnitConfig.xml",
        "<BetPerUnitData><BetPerUnitConfigurationList/></BetPerUnitData>",
    )

    with pytest.raises(BetConfigError, match="no <BetPerUnitMappings>"):
        load_bet_per_unit_config(path)


def test_a_non_numeric_rung_is_refused(tmp_path: Path) -> None:
    """Silently dropping it would shorten the ladder, which is the one thing a
    caller checks a requested bet against."""
    path = write(
        tmp_path / "betPerUnitConfig.xml",
        "<BetPerUnitData><BetPerUnitConfiguration>"
        "<BetPerUnitMappings><BetPerUnit>two</BetPerUnit></BetPerUnitMappings>"
        "</BetPerUnitConfiguration></BetPerUnitData>",
    )

    with pytest.raises(BetConfigError, match="not a whole number"):
        load_bet_per_unit_config(path)


def test_the_wrong_root_names_what_it_got(tmp_path: Path) -> None:
    path = write(tmp_path / "betPerUnitConfig.xml", "<BetUnitData/>")

    with pytest.raises(BetConfigError, match="<BetUnitData>, not a <BetPerUnitData>"):
        load_bet_per_unit_config(path)


def test_a_missing_file_is_a_config_error_not_an_oserror(tmp_path: Path) -> None:
    """The service turns this into an `error` on a 200, so it has to be the
    reader's own exception type."""
    with pytest.raises(BetConfigError, match="No bet per unit file"):
        load_bet_per_unit_config(tmp_path / "absent.xml")


# --- betUnitConfig.xml ----------------------------------------------------


def test_the_unit_cost_is_read_for_a_line_count(tmp_path: Path) -> None:
    """88 credits for 40 units is the number every FortuneOx line value is
    multiplied against -- and the `MinTotalBet` its gameConfig.cfg declares."""
    config = load_bet_unit_config(
        write(tmp_path / "betUnitConfig.xml", FORTUNE_OX_UNITS)
    )

    assert config.cost(units=40, tag="1") == 88
    assert config.configuration(40) is not None
    assert config.configuration(40).num_units == 40


def test_an_untagged_selection_is_the_default(tmp_path: Path) -> None:
    config = load_bet_unit_config(write(tmp_path / "betUnitConfig.xml", HUFF_UNITS))

    assert config.cost(units=243) == 100
    assert config.cost() == 100


def test_a_lone_configuration_answers_without_a_line_count(tmp_path: Path) -> None:
    """A paytable whose line count could not be read still prices, because
    there is only one block it could mean."""
    config = load_bet_unit_config(
        write(tmp_path / "betUnitConfig.xml", FORTUNE_OX_UNITS)
    )

    assert config.cost() == 88


def test_a_line_count_no_block_declares_is_not_guessed(tmp_path: Path) -> None:
    """Two blocks and neither is the one asked for: better no cost than the
    wrong one, since the cost multiplies every award."""
    path = write(
        tmp_path / "betUnitConfig.xml",
        "<BetUnitData><UnitConfigurationList>"
        '<UnitConfiguration numUnits="40"><UnitSelectMappings>'
        '<UnitSelectData units="40" cost="88"/></UnitSelectMappings></UnitConfiguration>'
        '<UnitConfiguration numUnits="20"><UnitSelectMappings>'
        '<UnitSelectData units="20" cost="50"/></UnitSelectMappings></UnitConfiguration>'
        "</UnitConfigurationList></BetUnitData>",
    )
    config = load_bet_unit_config(path)

    assert config.cost(units=20) == 50
    assert config.cost(units=25) is None


def test_a_non_numeric_cost_is_refused(tmp_path: Path) -> None:
    path = write(
        tmp_path / "betUnitConfig.xml",
        "<BetUnitData><UnitConfiguration><UnitSelectMappings>"
        '<UnitSelectData units="40" cost="lots"/>'
        "</UnitSelectMappings></UnitConfiguration></BetUnitData>",
    )

    with pytest.raises(BetConfigError, match="non-numeric cost"):
        load_bet_unit_config(path)


def test_a_file_with_no_configurations_is_refused(tmp_path: Path) -> None:
    path = write(tmp_path / "betUnitConfig.xml", "<BetUnitData/>")

    with pytest.raises(BetConfigError, match="no <UnitConfiguration>"):
        load_bet_unit_config(path)


# --- the pair -------------------------------------------------------------


def test_the_two_files_multiply_out_to_the_declared_bet_ladder(tmp_path: Path) -> None:
    """The claim the pair exists to support, checked against the third and
    fourth statements of it: `gameConfig.cfg`'s `SpecificMaxBets` and
    `math.xml`'s `AllowedBetsTbl` both read `88 176 264 440 880`."""
    per_unit = load_bet_per_unit_config(
        write(tmp_path / "betPerUnitConfig.xml", FORTUNE_OX_PER_UNIT)
    )
    units = load_bet_unit_config(
        write(tmp_path / "betUnitConfig.xml", FORTUNE_OX_UNITS)
    )

    cost = units.cost(units=40, tag="1")
    assert [cost * rung for rung in per_unit.ladder("1")] == [88, 176, 264, 440, 880]
