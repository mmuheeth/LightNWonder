"""Repairing an OCR reading against the caption strip's own tiny vocabulary.

Fast and engine-free on purpose: this is the part most likely to be subtly
wrong, and every case here is a string a real clip produced.
"""

from __future__ import annotations

import pytest

from app.utils.caption_text import repair

VOCABULARY = ("Line", "Game", "Pays")
NUMBER_AFTER = ("Line", "Pays")


def fixed(text: str) -> str:
    return repair(text, vocabulary=VOCABULARY, number_after=NUMBER_AFTER)


@pytest.mark.parametrize(
    ("reading", "expected"),
    [
        # The whole reason this exists: FortuneOx's font draws a 3 the
        # recogniser reads as `a` and an 8 it reads as `B`, at 90-98
        # confidence, through every model and upscale tried.
        ("Line a Pays 25", "Line 3 Pays 25"),
        ("Line B Pays 25", "Line 8 Pays 25"),
        # A lookalike *inside* a number stays part of it rather than splitting
        # off as a word -- one letter is not a word.
        ("Line 1e Pays 250", "Line 16 Pays 250"),
        # A word is repaired whole, against a vocabulary of three that
        # resemble nothing but themselves.
        ("Lie 7 Pays 250", "Line 7 Pays 250"),
        ("Line11 Pa 250", "Line 11 Pays 250"),
    ],
)
def test_a_slot_that_cannot_hold_a_letter_is_repaired(
    reading: str, expected: str
) -> None:
    assert fixed(reading) == expected


@pytest.mark.parametrize(
    ("reading", "expected"),
    [
        ("Line 11Pas 15", "Line 11 Pays 15"),
        ("Line 11Pa 15", "Line 11 Pays 15"),
        ("Line 4Pays 250", "Line 4 Pays 250"),
        ("Lie7 Pays 250", "Line 7 Pays 250"),
    ],
)
def test_a_word_welded_to_a_number_is_split_off_not_eaten(
    reading: str, expected: str
) -> None:
    """The regression this guards is a corruption, not a missed repair.

    An unsplit ``11Pas`` reaches the number slot entire, and the lookalike map
    turns the word's own ``a`` and ``s`` into 3 and 5: it came back as
    ``Line 1135 15``, which reads like a line number nobody can explain.
    Splitting on a *loosely* matched word is what covers it -- an exact-
    spelling split misses every case that matters, since a welded word is
    usually a misread one.
    """
    assert fixed(reading) == expected


@pytest.mark.parametrize(
    "reading",
    [
        "Game Pays 600",
        "Line 1 Pays 15",
        "Line 40 Pays 250",
        "Game Pays 10000",
    ],
)
def test_a_clean_reading_is_left_alone(reading: str) -> None:
    assert fixed(reading) == reading


@pytest.mark.parametrize("reading", ["Line 10 F20", "a 1 Pays 250", "Lss 32 Pps"])
def test_a_reading_it_cannot_explain_is_left_as_it_was(reading: str) -> None:
    """A garbled read stays garbled rather than being forced into the shape of
    a caption. The repair moves a character to the class the grammar demands;
    it does not invent one that was never read."""
    assert fixed(reading) == reading


def test_a_digit_is_never_corrected_against_another_digit() -> None:
    """The invariant that stops this from being made cleverer: two real
    captions differ by exactly one character, so there is no safe tolerance
    between digits at all."""
    assert fixed("Line 1 Pays 250") == "Line 1 Pays 250"
    assert fixed("Line 4 Pays 250") == "Line 4 Pays 250"


def test_an_empty_vocabulary_is_a_passthrough() -> None:
    """What a game whose strip nobody has described gets: guessing at an
    unknown grammar is how a repair becomes a corruption."""
    assert repair("Line a Pays 25", vocabulary=(), number_after=()) == "Line a Pays 25"


def test_empty_text_is_not_a_caption() -> None:
    assert fixed("") == ""
