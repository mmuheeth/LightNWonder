"""Repair an OCR reading of a caption against its known, tiny vocabulary (three words
plus the digits -- ``GAME PAYS 500`` or ``LINE 12 PAYS 25``, since PaddleOCR has no
``char_whitelist`` equivalent to apply up front). A number slot (after ``Line``/``Pays``)
is fixed digit-lookalike by character -- FortuneOx's font reads its 3 as ``a`` and 8 as
``B`` at 90-98 confidence -- while a vocabulary word is fixed only as a whole, never
per-character. Digit is never repaired against digit (real captions differ by one
character, so there is no safe tolerance), and the raw reading always ships beside the
repaired one, because this guarantees a shape, not a value."""

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
    """Break a token where a vocabulary word is welded to something else, splitting on
    the *word* rather than the letter/digit boundary -- so ``1e`` stays whole and reaches
    the number slot as ``16``, while ``11Pas`` becomes ``11`` and ``Pays`` instead of
    reaching the number slot whole and corrupting to ``1135``."""
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


def _rejoin(tokens: list[str], vocabulary: tuple[str, ...]) -> list[str]:
    """Put back together a vocabulary word the recogniser split in two -- the
    counterpart of :func:`_split_words`. PaddleOCR reads "PLAY 880 CREDITS" as
    ``Play 880 Cred its`` often enough to matter, and each half then loosely matches
    "Credits" on its own (see :func:`_as_word`), doubling it. Joins only on an **exact**
    match of the concatenation, deliberately stricter than that loose match, so two
    unrelated short tokens can never be welded into a word the strip never drew."""
    known = {word.casefold(): word for word in vocabulary}
    out: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        if following is not None and token.isalpha() and following.isalpha():
            joined = known.get((token + following).casefold())
            if joined is not None:
                out.append(joined)
                index += 2
                continue
        out.append(token)
        index += 1
    return out


def repair(
    text: str, *, vocabulary: tuple[str, ...], number_after: tuple[str, ...]
) -> str:
    """Return ``text`` with its number slots and its vocabulary put right.
    ``vocabulary`` is every word the strip draws and ``number_after`` the ones a number
    follows; an empty ``vocabulary`` disables the repair, since guessing an unknown
    strip's grammar is how a repair becomes a corruption."""
    if not vocabulary or not text:
        return text

    known = {word.casefold(): word for word in vocabulary}
    follows = {word.casefold() for word in number_after}

    # Words are separated from whatever they are welded to *before* the slots
    # are walked, so a number slot never receives a token with a word still
    # inside it. This also canonicalises them, so `Lie` arrives as `Line`.
    tokens = [
        part
        # Rejoined before being split: a word broken in two has to be made whole
        # before the splitter is allowed to snap each half to the vocabulary
        # separately, or one caption becomes two of the same word.
        for token in _rejoin(text.split(), vocabulary)
        for part in _split_words(token, vocabulary)
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
