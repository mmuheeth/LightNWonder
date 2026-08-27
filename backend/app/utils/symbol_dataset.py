"""A folder of symbol artwork read back as training pictures.

The artwork is not what the classifier will see. A game ships each symbol as a
transparent cut-out at 380-600px square; the reel grid writes tiles of roughly
117x88 with the symbol already composited onto the reel's own dark purple field.
Training straight off the artwork therefore teaches the network that a symbol
sits on *nothing*, and every tile it is later shown disagrees -- which is how the
earlier cosine-similarity attempt came to miss the card symbols completely while
scoring only 0.35-0.44 on the picture symbols.

So this module's job is to put the background back. Every constant below was
measured off the 600 tiles the grid has already written, not chosen:

* the reel field is ``rgb(35, 0, 56)``, and two thirds of tiles' corners sit
  within a couple of levels of it;
* 238 of those 600 tiles keep a sliver of the gold reel divider at one edge,
  averaging ``rgb(201, 121, 37)`` -- the ``inset`` crop trims most of it, not all;
* a couple of percent are pure black, which is a frame caught mid-fade.

Deliberately torch-free: Pillow and numpy only, so the sample synthesis can be
tested, and looked at, on a machine with no ML stack installed at all.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

__all__ = [
    "BLACK_PROBABILITY",
    "DENSE_OPACITY",
    "FIELD_RGB",
    "GOLD_PROBABILITY",
    "GOLD_RGB",
    "IMAGE_SUFFIXES",
    "ClassSources",
    "ClassSummary",
    "DatasetError",
    "SymbolSource",
    "compose",
    "fingerprint",
    "load_sources",
    "plate",
    "summarise",
    "symbols",
]

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".webp"})

# The reel cell's own colour, the modal corner across every written tile.
FIELD_RGB = (35, 0, 56)

# The gold reel divider that survives the inset crop at one edge of a tile,
# averaged over the 238 of 600 tiles that keep some.
GOLD_RGB = (201, 121, 37)

# How often to draw that sliver, and how often to emit a black (mid-fade) cell.
GOLD_PROBABILITY = 0.40
BLACK_PROBABILITY = 0.02

# Opaque fraction inside its own alpha box, above which a source is taken to
# already carry the game's field and frame and is left alone. Measured: the Ox is
# 0.994 because its artwork is a framed portrait; every other symbol in this
# game's set is a cut-out at 0.45-0.71, so the cut is wide rather than delicate.
DENSE_OPACITY = 0.90

# Fraction of the plate's shorter side a pasted symbol is scaled to occupy.
_SYMBOL_SCALE = (0.72, 0.95)

# Per-channel jitter of the field colour, plus the noise and top-down gradient
# that stop the plate being a flat rectangle no real cell resembles.
_FIELD_JITTER = 6
_NOISE_SIGMA = 2.0
_GRADIENT = 0.08


class DatasetError(Exception):
    """The training images cannot be read as a dataset."""


@dataclass(frozen=True)
class SymbolSource:
    """One artwork file, cropped to its own opaque box and ready to compose.

    ``dense`` is what decides whether a background is needed at all, and it is a
    measured property of this file rather than a fact about its class -- a game
    whose whole set ships pre-composited needs no special case, and one that
    mixes the two kinds gets each of them right.
    """

    path: Path
    symbol: str
    image: Image.Image
    """RGBA, cropped to the alpha bounding box."""
    dense: bool
    opacity: float

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class ClassSources:
    """Every source for one symbol code, in filename order."""

    symbol: str
    sources: tuple[SymbolSource, ...]

    def __len__(self) -> int:
        return len(self.sources)


@dataclass(frozen=True)
class ClassSummary:
    """What one class holds, for the dashboard to warn about."""

    symbol: str
    count: int
    widths: tuple[int, ...]
    heights: tuple[int, ...]
    dense: int
    mean_opacity: float


def _alpha_box(image: Image.Image) -> tuple[int, int, int, int]:
    """The box enclosing every pixel that is not fully transparent.

    Falls back to the whole picture: an image with no alpha, or one transparent
    throughout, has nothing to crop to and is better handed on unchanged than
    rejected.
    """
    if image.mode != "RGBA":
        return (0, 0, image.width, image.height)
    alpha = np.asarray(image.getchannel("A"))
    rows = np.flatnonzero(alpha.max(axis=1) > 0)
    columns = np.flatnonzero(alpha.max(axis=0) > 0)
    if rows.size == 0 or columns.size == 0:
        return (0, 0, image.width, image.height)
    return (int(columns[0]), int(rows[0]), int(columns[-1]) + 1, int(rows[-1]) + 1)


def _opacity(image: Image.Image) -> float:
    """Fraction of the picture that is very nearly opaque."""
    if image.mode != "RGBA":
        return 1.0
    alpha = np.asarray(image.getchannel("A"))
    if alpha.size == 0:
        return 0.0
    return float((alpha > 250).mean())


def _load(path: Path, symbol: str) -> SymbolSource:
    with Image.open(path) as handle:
        handle.load()
        image = handle.convert("RGBA")
    cropped = image.crop(_alpha_box(image))
    opacity = _opacity(cropped)
    return SymbolSource(
        path=path,
        symbol=symbol,
        image=cropped,
        dense=opacity >= DENSE_OPACITY,
        opacity=opacity,
    )


def _class_dirs(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        raise DatasetError(f"{root} is not a directory")
    yield from sorted(
        (child for child in root.iterdir() if child.is_dir()),
        key=lambda child: child.name,
    )


def _image_files(directory: Path) -> list[Path]:
    return sorted(
        (
            child
            for child in directory.iterdir()
            if child.is_file() and child.suffix.lower() in IMAGE_SUFFIXES
        ),
        key=lambda child: child.name,
    )


def load_sources(root: Path) -> list[ClassSources]:
    """Every class under ``root``, each with its artwork opened and cropped.

    Classes holding no readable image are dropped rather than carried as an empty
    label: a directory someone made and never filled would otherwise become a
    class the model can predict and never be right about.
    """
    classes: list[ClassSources] = []
    for directory in _class_dirs(root):
        sources = tuple(_load(path, directory.name) for path in _image_files(directory))
        if sources:
            classes.append(ClassSources(symbol=directory.name, sources=sources))
    if not classes:
        raise DatasetError(f"no class directories with images under {root}")
    return classes


def summarise(root: Path) -> list[ClassSummary]:
    """Per-class counts, sizes and opacity, without composing anything.

    Reads pixels rather than only counting files, because the one number worth
    warning about -- how many classes hold a single distinct picture -- is not
    visible from a listing, and neither is whether a class carries its own
    background.
    """
    summaries: list[ClassSummary] = []
    for directory in _class_dirs(root):
        widths: list[int] = []
        heights: list[int] = []
        opacities: list[float] = []
        dense = 0
        for path in _image_files(directory):
            try:
                source = _load(path, directory.name)
            except OSError:
                continue
            widths.append(source.image.width)
            heights.append(source.image.height)
            opacities.append(source.opacity)
            dense += int(source.dense)
        if not widths:
            continue
        summaries.append(
            ClassSummary(
                symbol=directory.name,
                count=len(widths),
                widths=tuple(sorted(set(widths))),
                heights=tuple(sorted(set(heights))),
                dense=dense,
                mean_opacity=float(np.mean(opacities)),
            )
        )
    return summaries


def fingerprint(root: Path) -> str:
    """A short digest of the dataset's filenames, sizes and mtimes.

    Travels on the checkpoint so a model can say it was trained on different
    pictures than the ones on disk now. A stale model that merely predicts badly
    is much harder to notice than one that says it is stale.
    """
    digest = hashlib.sha256()
    try:
        directories = list(_class_dirs(root))
    except DatasetError:
        return "missing"
    for directory in directories:
        for path in _image_files(directory):
            stat = path.stat()
            digest.update(directory.name.encode("utf-8"))
            digest.update(path.name.encode("utf-8"))
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(str(int(stat.st_mtime)).encode("ascii"))
    return digest.hexdigest()[:16]


def plate(size: int, rng: random.Random) -> Image.Image:
    """One empty reel cell, ``size`` square.

    Not a flat rectangle: a cell carries a little noise, is lit slightly from the
    top, and 40% of the time keeps a sliver of the gold divider at one edge. The
    flat version trains a network that any texture at all is a symbol.
    """
    if rng.random() < BLACK_PROBABILITY:
        return Image.new("RGB", (size, size), (0, 0, 0))

    field = np.array(
        [channel + rng.randint(-_FIELD_JITTER, _FIELD_JITTER) for channel in FIELD_RGB],
        dtype=np.float64,
    )
    canvas = np.repeat(np.repeat(field[None, None, :], size, 0), size, 1)

    # Lit top-down by a fraction rather than an absolute level, so a dark field
    # is never pushed negative.
    canvas *= np.linspace(1.0 + _GRADIENT, 1.0 - _GRADIENT, size)[:, None, None]
    canvas += np.random.default_rng(rng.randrange(2**32)).normal(
        0.0, _NOISE_SIGMA, (size, size, 3)
    )

    if rng.random() < GOLD_PROBABILITY:
        width = max(1, round(size * rng.uniform(0.02, 0.06)))
        gold = np.array(
            [channel + rng.randint(-16, 16) for channel in GOLD_RGB], dtype=np.float64
        )
        edge = rng.choice(("left", "right", "top", "bottom"))
        if edge == "left":
            canvas[:, :width] = gold
        elif edge == "right":
            canvas[:, -width:] = gold
        elif edge == "top":
            canvas[:width, :] = gold
        else:
            canvas[-width:, :] = gold

    return Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), mode="RGB")


def _solid(size: int, rng: random.Random) -> Image.Image:
    colour = tuple(
        int(np.clip(channel + rng.randint(-_FIELD_JITTER, _FIELD_JITTER), 0, 255))
        for channel in FIELD_RGB
    )
    return Image.new("RGB", (size, size), colour)


def _jitter(slack: int, rng: random.Random) -> int:
    """A small offset from centre, at most a quarter of the space going spare."""
    limit = slack // 4
    return slack // 2 + (rng.randint(-limit, limit) if limit > 0 else 0)


def compose(
    source: SymbolSource,
    size: int,
    rng: random.Random,
    *,
    style: str = "plate",
) -> Image.Image:
    """One training picture: this artwork, on the background the game gives it.

    A source that already carries its own field and frame (``dense``) is only
    fitted to the canvas -- pasting it onto a plate would hide the plate anyway,
    and scaling it down to make room would invent a border no real tile has.
    """
    if style == "none":
        background = Image.new("RGB", (size, size), (0, 0, 0))
    elif style == "solid":
        background = _solid(size, rng)
    else:
        background = plate(size, rng)

    art = source.image
    if source.dense:
        return Image.alpha_composite(
            background.convert("RGBA"),
            art.resize((size, size), Image.Resampling.LANCZOS),
        ).convert("RGB")

    target = max(1, round(size * rng.uniform(*_SYMBOL_SCALE)))
    ratio = art.width / art.height if art.height else 1.0
    if ratio >= 1.0:
        width, height = target, max(1, round(target / ratio))
    else:
        width, height = max(1, round(target * ratio)), target

    resized = art.resize((width, height), Image.Resampling.LANCZOS)
    # Centred with a nudge: the game does not draw every symbol on the exact
    # centre of its cell either, and a network trained on perfect centring reads
    # an off-centre tile as a different picture.
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    layer.paste(
        resized, (_jitter(size - width, rng), _jitter(size - height, rng)), resized
    )
    return Image.alpha_composite(background.convert("RGBA"), layer).convert("RGB")


def symbols(classes: Sequence[ClassSources]) -> list[str]:
    """The class labels, in the order the model's outputs will be in."""
    return [entry.symbol for entry in classes]


# --- Holding frames back, and admitting how little that proves ------------
# A class here is an animation *loop*, not a set of independent pictures, and
# neighbouring frames are near-identical -- so no split of it is honestly
# unseen. Two things follow, and both are implemented rather than assumed.
#
# First, *which* frames to hold back matters. Holding back the last few is
# nearly worthless because the loop closes: measured on this dataset, the last
# frame of AA and of DD sit 0.02/255 from a retained frame, i.e. the same
# picture. A contiguous block from the middle is the better choice and is what
# `frame_split` returns.
#
# Second, even that leaks. `leakage` measures how much, so the accuracy built on
# it can be reported with its own caveat attached instead of being passed off as
# a holdout score.

_LEAK_THUMBNAIL = 64


def frame_split(count: int, holdout: int) -> tuple[list[int], list[int]]:
    """Indices to train on and to hold back, for one class of ``count`` frames.

    The held-back block is taken from the middle of the loop rather than the end.
    A class with too few frames to spare a block keeps all of them and holds
    nothing back -- a single-image class contributes to no honest accuracy figure
    and pretending otherwise is the one genuinely misleading thing this could do.
    """
    if holdout <= 0 or count < 3 * holdout:
        return list(range(count)), []
    start = (count - holdout) // 2
    held = list(range(start, start + holdout))
    kept = [index for index in range(count) if index not in set(held)]
    return kept, held


def _thumbnails(sources: Sequence[SymbolSource]) -> np.ndarray:
    flat = []
    for source in sources:
        small = source.image.convert("RGB").resize(
            (_LEAK_THUMBNAIL, _LEAK_THUMBNAIL), Image.Resampling.BILINEAR
        )
        flat.append(np.asarray(small, dtype=np.float64).ravel())
    return np.array(flat)


def leakage(
    sources: Sequence[SymbolSource], kept: Sequence[int], held: Sequence[int]
) -> float:
    """How close the held-back frames sit to the ones trained on, in ``0..1``.

    Each held-back frame's distance to the nearest retained frame, divided by the
    mean distance between any two frames of the class. 0 means the held-back
    frames are duplicates of training data and the accuracy over them says
    nothing; 1 means they are as unlike the training frames as two random frames
    of the same symbol are, which is the most this dataset can offer.
    """
    if not held or not kept:
        return 0.0
    vectors = _thumbnails(sources)
    pairwise = np.abs(vectors[:, None, :] - vectors[None, :, :]).mean(-1)
    typical = float(pairwise[~np.eye(len(vectors), dtype=bool)].mean())
    if typical <= 0.0:
        return 0.0
    nearest = pairwise[np.ix_(list(held), list(kept))].min(axis=1)
    return float(min(1.0, nearest.mean() / typical))
