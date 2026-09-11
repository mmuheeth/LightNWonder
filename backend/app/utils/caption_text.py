"""Repair an OCR reading of a caption drawn from a known, tiny vocabulary.

The cyclic message strip says one of two things -- ``GAME PAYS 500`` or
``LINE 12 PAYS 25`` -- so its whole alphabet is three words and the digits.
That is a much stronger constraint than a general recogniser can use, and it
is the constraint this module applies afterwards. Tesseract has
``char_whitelist`` for exactly this; PaddleOCR, which reads these captions
better, has no equivalent, so the whitelist has to be applied to the output.

Two repairs, and the difference between them is why only one of them is safe
to do character by character:

* **A number slot cannot hold a letter.** After ``Line`` and after ``Pays``
  the strip draws digits, always. So a letter there is a misread by
  definition, and mapping it to the digit it resembles cannot make the reading
  *less* right. Measured on FortuneOx's own font, the recogniser reads its 3
  as ``a`` and its 8 as ``B`` at 90-98 confidence, through every model and
  every upscale tried -- the glyphs simply look like that. This is what fixes
  "Line a Pays 25".
* **A word is repaired only as a whole.** The vocabulary has three entries and
  none resembles another, so a token of letters near one of them is that one:
  ``Lie`` is ``Line``, ``Pa`` is ``Pays``. Doing the same *per character*
  would be the dangerous version -- see below.

**What this deliberately does not do is fix a digit against another digit.**
Two real captions differ by one character (``Line 1`` against ``Line 4``), so
there is no safe tolerance there at all: the repair only ever moves a
character from the wrong *class* to the right one, never from one digit to
another.

**And it guarantees a shape, not a value.** If a font's 8 also read as ``a``
then ``Line a`` would come back as ``Line 3`` and be confidently wrong. The
raw reading is kept beside the repaired one on every frame for that reason --
``CyclicTextFrame.text`` is still verbatim, and ``repaired`` is this.
"""

from __future__ import annotations

import difflib
import re

__all__ = ["DIGIT_LOOKALIKES", "repair"]

# Letters a recogniser returns where a digit was drawn. `a`->3 and `B`->8 are
# measured on FortuneOx's caption font, which is the whole reason this exists;
# the rest are the conventional confusions, harmless here because they are only
# ever consulted in a slot that cannot hold a letter anyway.
DIGIT_LOOKALIKES = {
    "a": "3",
    "A": "4",
    "b": "6",
    "B": "8",
    "D": "0",
    "e": "6",
    "E": "8",
    "g": "9",
    "G": "6",
    "i": "1",
    "I": "1",
    "J": "1",
    "l": "1",
    "o": "0",
    "O": "0",
    "q": "9",
    "Q": "0",
    "s": "5",
    "S": "5",
    "t": "7",
    "T": "7",
    "z": "2",
    "Z": "2",
}

# Below this a token of letters is not one of the vocabulary words misread, it
# is something else the strip said. 0.6 accepts `Lie`->`Line` and `Pa`->`Pays`
# while leaving a genuinely different word alone.
_NEAR_ENOUGH = 0.6

# Two letters, because a single stray character off the artwork is not a word.
_MIN_WORD = 2


def _letters(token: str) -> str:
    return "".join(character for character in token if character.isalpha())


def _as_word(run: str, vocabulary: tuple[str, ...]) -> str | None:
    """The vocabulary word ``run`` is, or ``None`` if it is not one of them."""
    if len(run) < _MIN_WORD:
        return None
    near = difflib.get_close_matches(run, vocabulary, n=1, cutoff=_NEAR_ENOUGH)
    return near[0] if near else None


def _split_words(token: str, vocabulary: tuple[str, ...]) -> list[str]:
    """Break a token where a vocabulary word is welded to something else.

    ``Line11`` and ``11Pas`` are one token to a splitter and two slots to the
    grammar. Splitting on the *word* rather than on the letter/digit boundary
    is the whole point: a letter run becomes its own token only when it is
    recognisably one of the three words, so ``1e`` stays whole and reaches the
    number slot as ``16``, while ``11Pas`` becomes ``11`` and ``Pays``.

    Getting this wrong is not a missed repair but a corruption. An unsplit
    ``11Pas`` reaches the number slot entire, where the lookalike map turns
    its own ``a`` and ``s`` into 3 and 5 and it reads as ``1135``. Matching
    the word loosely here is what covers the case the exact spelling misses,
    which is the common one -- a welded word is usually a misread word.
    """
    parts: list[str] = []
    pending = ""
    for run in re.findall(r"[A-Za-z]+|[^A-Za-z]+", token):
        word = _as_word(run, vocabulary) if run.isalpha() else None
        if word is None:
            pending += run
            continue
        if pending:
            parts.append(pending)
            pending = ""
        parts.append(word)
    if pending:
        parts.append(pending)
    return parts or [token]


def repair(
    text: str, *, vocabulary: tuple[str, ...], number_after: tuple[str, ...]
) -> str:
    """Return ``text`` with its number slots and its vocabulary put right.

    ``vocabulary`` is every word the strip is known to draw and ``number_after``
    the ones a number follows. An empty ``vocabulary`` turns the whole thing
    off and returns the text unchanged, which is what a game nobody has
    described yet should get -- guessing at an unknown strip's grammar is how
    a repair becomes a corruption.
    """
    if not vocabulary or not text:
        return text

    known = {word.casefold(): word for word in vocabulary}
    follows = {word.casefold() for word in number_after}

    # Words are separated from whatever they are welded to *before* the slots
    # are walked, so a number slot never receives a token with a word still
    # inside it. This also canonicalises them, so `Lie` arrives as `Line`.
    tokens = [
        part for token in text.split() for part in _split_words(token, vocabulary)
    ]

    out: list[str] = []
    expect_number = False
    for token in tokens:
        letters = _letters(token)
        if expect_number and letters.casefold() not in known:
            digits = "".join(
                character
                for character in (DIGIT_LOOKALIKES.get(c, c) for c in token)
                if character.isdigit()
            )
            if digits:
                out.append(digits)
                expect_number = False
                continue
        out.append(token)
        expect_number = letters.casefold() in follows
    return " ".join(out)
