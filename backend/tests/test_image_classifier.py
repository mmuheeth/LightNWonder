"""Image classifier endpoint and service tests.

Almost none of these train anything. A real fit is four minutes of CPU, so the
model is stubbed at the one seam that matters -- :func:`symbol_model.predict`,
which turns pictures into probabilities -- and everything above it (the floor, the
grid arrangement, the display names, the overlay, the run record) is exercised
against known numbers. The one test that really trains is opt-in and skipped by
default, because a four-minute unit test is a broken suite.

Assertions are about *pixels and positions* wherever a mistake would be
invisible otherwise: each tile is painted a colour encoding its own position, so
a transposed ``symbol_grid`` fails rather than merely looking plausible. That is
the thesis ``test_grid.py`` argues for and it applies here for the same reason.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import random
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from httpx import AsyncClient
from PIL import Image

from app.config.game_config.selection import save_active_game
from app.config.runtime import settings
from app.services import image_classifier as classifier_service
from app.utils import symbol_dataset
from tests.asserts import assert_failure, assert_success
from tests.test_grid import write_game_config

CLASSES = ["AA", "BB", "CC", "DD", "EE"]
LABELS = {"AA": "Ox", "BB": "Pisces", "CC": "Wealth Pot", "DD": "Arm Band", "EE": "Ace"}

REELS = {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.6}
REEL_BOUNDS = {"rows": 3, "columns": 5}

TILE_SIZE = (24, 18)
CROP_SIZE = (TILE_SIZE[0] * 5, TILE_SIZE[1] * 3)


def tile_colour(row: int, column: int) -> tuple[int, int, int]:
    """A colour that encodes the tile's own position.

    So an assertion can be about *which tile* a prediction was made for, not just
    how many there were -- the only thing that catches a matrix built column-major.
    """
    return (row * 60, column * 40, 200)


def write_split(root: Path, name: str, rows: int = 3, columns: int = 5) -> Path:
    """Write a split of position-coloured tiles, as the reel grid would."""
    directory = root / "grid" / name
    tiles = directory / "tiles"
    tiles.mkdir(parents=True, exist_ok=True)
    crop = Image.new("RGB", (TILE_SIZE[0] * columns, TILE_SIZE[1] * rows), (0, 0, 0))
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            tile = Image.new("RGB", TILE_SIZE, tile_colour(row, column))
            tile.save(tiles / f"r{row}c{column}.png")
            crop.paste(tile, ((column - 1) * TILE_SIZE[0], (row - 1) * TILE_SIZE[1]))
    crop.save(directory / "reels.png")
    return directory


def write_dataset(root: Path, counts: dict[str, int]) -> Path:
    """An ImageFolder tree of transparent cut-outs, one directory per code.

    Two small blobs at opposite ends rather than one filled square, so the alpha
    box spans them while most of it stays transparent. That matters: the composer
    routes on how opaque a source is inside its own box, and a filled square would
    take the already-composited path that eight of the nine real classes do not.
    """
    for symbol, count in counts.items():
        directory = root / symbol
        directory.mkdir(parents=True, exist_ok=True)
        for index in range(count):
            image = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
            blob = Image.new("RGBA", (6, 6), (200, 40 + index, 60, 255))
            image.paste(blob, (8, 8))
            image.paste(blob, (34, 34))
            image.save(directory / f"{symbol}_{index:05d}.png")
    return root


def decode(image_data: str) -> Image.Image:
    assert image_data.startswith("data:image/png;base64,")
    raw = base64.b64decode(image_data.split(",", 1)[1])
    return Image.open(io.BytesIO(raw))


@pytest.fixture
def captures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the capture directory, which is where splits live."""
    directory = tmp_path / "capture"
    monkeypatch.setattr(
        type(settings), "obs_capture_dir", property(lambda _self: directory)
    )
    return directory


@pytest.fixture
def model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the model directory, and with it every per-architecture path.

    ``classifier_checkpoint_for`` is a method rather than a property, and the
    ``classifier_checkpoint_path`` property is built on top of it -- so patching
    the method alone covers both, and the two cannot disagree about where a
    checkpoint lives.
    """
    directory = tmp_path / "model"
    monkeypatch.setattr(
        type(settings), "classifier_model_dir", property(lambda _self: directory)
    )
    monkeypatch.setattr(
        type(settings),
        "classifier_checkpoint_for",
        lambda _self, architecture: directory / f"model-{architecture}.pt",
    )
    monkeypatch.setattr(
        type(settings),
        "classifier_sample_dir",
        property(lambda _self: directory / "samples"),
    )
    return directory


@pytest.fixture
def dataset_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "dataset"
    monkeypatch.setattr(
        type(settings), "classifier_dataset_dir", property(lambda _self: directory)
    )
    return directory


@pytest.fixture
def game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Make one game config active, and return the factory that wrote it."""

    def activate(document: dict[str, Any]) -> Path:
        directory = tmp_path / "games"
        path = write_game_config(directory, document)
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
def active_game(game) -> Path:
    """A game naming its symbols, which is all the classifier reads a config for."""
    return game(
        {
            "name": "FortuneOx",
            "process": "FortuneOx.exe",
            "roi": {"reels": REELS},
            "reel_bounds": REEL_BOUNDS,
            "symbols": LABELS,
        }
    )


class FakeCheckpoint:
    """A stand-in for a loaded model, carrying only what the service reads."""

    def __init__(
        self,
        classes: list[str],
        *,
        version: int = 1,
        architecture: str = "efficientnet_b0",
    ) -> None:
        self.classes = classes
        self.image_size = 64
        self.architecture = architecture
        self.transform_version = version
        self.dataset_fingerprint = "fixture"
        self.trained_at = "2026-08-28T00:00:00"
        self.metrics: dict[str, Any] = {}
        self.path = Path(f"model-{architecture}.pt")
        self.model = object()

    @property
    def label(self) -> str:
        import app.utils.symbol_model as symbol_model

        return symbol_model.architecture_label(self.architecture)

    @property
    def stale(self) -> bool:
        import app.utils.symbol_model as symbol_model

        return self.transform_version != symbol_model.TRANSFORM_VERSION


@pytest.fixture
def stub_model(monkeypatch: pytest.MonkeyPatch):
    """Replace the torch layer with a scripted one.

    Returns a setter taking the per-tile probability rows to hand back, in
    ``r1c1, r1c2, ... `` order, so a test states the model's opinion outright and
    asserts what the service does with it.
    """
    import app.utils.symbol_model as symbol_model

    state: dict[str, Any] = {"rows": None, "classes": CLASSES, "version": 1}

    def fake_predict(checkpoint, images, threads):
        rows = state["rows"]
        if rows is None:
            # Uniform: nothing clears any sensible floor.
            width = len(state["classes"])
            return np.full((len(images), width), 1.0 / width)
        return np.asarray(rows, dtype=np.float64)

    def fake_load(path):
        # Derive the architecture from the filename, so the stub behaves like the
        # real loader: asking for one engine's model must not hand back another's.
        name = Path(path).stem.removeprefix("model-") or "efficientnet_b0"
        return FakeCheckpoint(
            list(state["classes"]), version=state["version"], architecture=name
        )

    monkeypatch.setattr(symbol_model, "predict", fake_predict)
    monkeypatch.setattr(symbol_model, "load", fake_load)
    monkeypatch.setattr(symbol_model, "TRANSFORM_VERSION", state["version"])

    def configure(
        rows: list[list[float]] | None = None,
        *,
        classes: list[str] | None = None,
    ) -> None:
        state["rows"] = rows
        if classes is not None:
            state["classes"] = classes

    return configure


@pytest.fixture
def trained(model_dir: Path, stub_model) -> Path:
    """A checkpoint file that exists, so the service believes a model is there."""
    model_dir.mkdir(parents=True, exist_ok=True)
    path = model_dir / f"model-{settings.CLASSIFIER_ARCHITECTURE}.pt"
    path.write_bytes(b"not really a model; symbol_model.load is stubbed")
    classifier_service.reset()
    return path


def row(**scores: float) -> list[float]:
    """One tile's probabilities, named by symbol, the rest sharing what is left."""
    remaining = max(0.0, 1.0 - sum(scores.values()))
    spare = remaining / max(1, len(CLASSES) - len(scores))
    return [scores.get(symbol, spare) for symbol in CLASSES]


# --- status ---------------------------------------------------------------


async def test_status_is_a_200_when_torch_is_not_installed(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    dataset_dir: Path,
    active_game: Path,
) -> None:
    """A missing engine is a state, not an error -- the page has to render why."""
    write_dataset(dataset_dir, {"AA": 2})
    monkeypatch.setattr(classifier_service, "_import_model", lambda: None)

    response = await client.get("/api/image-classifier/status")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["state"] == "not_installed"
    assert "pip install" in data["detail"]


async def test_status_reports_untrained_before_anything_is_fitted(
    client: AsyncClient,
    dataset_dir: Path,
    model_dir: Path,
    active_game: Path,
    stub_model,
) -> None:
    write_dataset(dataset_dir, {"AA": 2, "BB": 1})

    data = assert_success((await client.get("/api/image-classifier/status")).json())

    assert data["state"] == "untrained"
    assert data["model"] is None


async def test_status_reports_stale_when_the_checkpoint_predates_preprocessing(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    dataset_dir: Path,
    trained: Path,
    active_game: Path,
) -> None:
    """The whole point of the version travelling on the checkpoint: a model that
    would predict differently than it was measured says so, rather than quietly
    being wrong."""
    write_dataset(dataset_dir, {"AA": 2})
    import app.utils.symbol_model as symbol_model

    monkeypatch.setattr(symbol_model, "TRANSFORM_VERSION", 99)

    data = assert_success((await client.get("/api/image-classifier/status")).json())

    assert data["state"] == "stale"
    assert "older preprocessing" in data["detail"]


# --- dataset --------------------------------------------------------------


async def test_dataset_warns_about_classes_holding_a_single_picture(
    client: AsyncClient,
    dataset_dir: Path,
    active_game: Path,
) -> None:
    """Invisible from a file listing, and the weakest part of the model."""
    write_dataset(dataset_dir, {"AA": 4, "BB": 1, "CC": 1})

    data = assert_success((await client.get("/api/image-classifier/dataset")).json())

    assert data["total_images"] == 6
    assert data["single_image_classes"] == ["BB", "CC"]
    assert any("single picture" in warning for warning in data["warnings"])


async def test_dataset_names_the_symbols_the_game_declares_but_has_no_art_for(
    client: AsyncClient,
    dataset_dir: Path,
    active_game: Path,
) -> None:
    """These are the tiles that can only come back as another symbol or unknown,
    so the page has to be able to say which they are."""
    write_dataset(dataset_dir, {"AA": 2, "BB": 2})

    data = assert_success((await client.get("/api/image-classifier/dataset")).json())

    assert data["missing_symbols"] == ["CC", "DD", "EE"]
    assert any("no artwork" in warning for warning in data["warnings"])


async def test_dataset_reports_a_missing_directory_on_a_200(
    client: AsyncClient,
    dataset_dir: Path,
    active_game: Path,
) -> None:
    data = assert_success((await client.get("/api/image-classifier/dataset")).json())

    assert data["exists"] is False
    assert str(dataset_dir) in data["error"]


async def test_dataset_uses_the_game_config_for_display_names(
    client: AsyncClient,
    dataset_dir: Path,
    active_game: Path,
) -> None:
    write_dataset(dataset_dir, {"AA": 1})

    data = assert_success((await client.get("/api/image-classifier/dataset")).json())

    assert data["classes"][0]["symbol"] == "AA"
    assert data["classes"][0]["label"] == "Ox"


# --- classifying ----------------------------------------------------------


async def test_classify_names_every_tile_and_arranges_them_row_major(
    client: AsyncClient,
    captures: Path,
    dataset_dir: Path,
    trained: Path,
    active_game: Path,
    stub_model,
) -> None:
    """A transposed matrix is the failure this is really about, so the model is
    told to name a different symbol per position and the grid is read back."""
    write_dataset(dataset_dir, {"AA": 1})
    write_split(captures, "shot", rows=2, columns=3)
    # r1c1 r1c2 r1c3 r2c1 r2c2 r2c3
    stub_model(
        [
            row(AA=0.9),
            row(BB=0.9),
            row(CC=0.9),
            row(DD=0.9),
            row(EE=0.9),
            row(AA=0.8),
        ]
    )

    # An explicit floor: this test is about *arrangement*, so it must not move
    # when the shipped default does. The floor has its own tests below.
    data = assert_success(
        (
            await client.post(
                "/api/image-classifier/classify",
                json={"split": "shot", "min_confidence": 0.5},
            )
        ).json()
    )

    assert data["rows"] == 2
    assert data["columns"] == 3
    assert data["symbol_grid"] == [["AA", "BB", "CC"], ["DD", "EE", "AA"]]
    assert [tile["name"] for tile in data["tiles"]] == [
        "r1c1",
        "r1c2",
        "r1c3",
        "r2c1",
        "r2c2",
        "r2c3",
    ]
    assert data["named"] == 6
    assert data["unknown"] == 0


async def test_the_label_grid_mirrors_the_symbol_grid_in_display_names(
    client: AsyncClient,
    captures: Path,
    dataset_dir: Path,
    trained: Path,
    active_game: Path,
    stub_model,
) -> None:
    """Both matrices are built on the server so they cannot disagree about which
    cell is which -- a dashboard deriving one of them from `tiles` could."""
    write_dataset(dataset_dir, {"AA": 1})
    write_split(captures, "shot", rows=1, columns=3)
    stub_model([row(AA=0.9), row(CC=0.9), row(BB=0.4)])

    data = assert_success(
        (
            await client.post("/api/image-classifier/classify", json={"split": "shot"})
        ).json()
    )

    assert data["symbol_grid"] == [["AA", "CC", None]]
    assert data["label_grid"] == [["Ox", "Wealth Pot", None]]


async def test_the_overlay_survives_turning_the_tile_pictures_off(
    client: AsyncClient,
    captures: Path,
    dataset_dir: Path,
    trained: Path,
    active_game: Path,
    stub_model,
) -> None:
    """`include_images` is about the per-tile pictures. The overlay is one picture
    the dashboard does show, so gating it on the fifteen it does not would mean
    paying for all of them to get the one that is rendered."""
    write_dataset(dataset_dir, {"AA": 1})
    write_split(captures, "shot")
    stub_model(None)

    data = assert_success(
        (
            await client.post(
                "/api/image-classifier/classify",
                json={"split": "shot", "include_images": False},
            )
        ).json()
    )

    assert all(tile["image_data"] is None for tile in data["tiles"])
    assert data["overlay_image"] is not None
    assert data["overlay_file"] == "symbols.png"


async def test_classify_returns_the_tile_that_was_actually_classified(
    client: AsyncClient,
    captures: Path,
    dataset_dir: Path,
    trained: Path,
    active_game: Path,
    stub_model,
) -> None:
    """Every tile is painted a colour encoding its position, so this checks the
    picture returned for r2c3 really is r2c3's picture."""
    write_dataset(dataset_dir, {"AA": 1})
    write_split(captures, "shot")
    stub_model(None)

    data = assert_success(
        (
            await client.post("/api/image-classifier/classify", json={"split": "shot"})
        ).json()
    )

    tile = next(entry for entry in data["tiles"] if entry["name"] == "r2c3")
    picture = decode(tile["image_data"]).convert("RGB")
    assert picture.getpixel((2, 2)) == tile_colour(2, 3)
    assert (tile["width"], tile["height"]) == TILE_SIZE


async def test_a_tile_below_the_floor_is_unknown_and_keeps_its_predictions(
    client: AsyncClient,
    captures: Path,
    dataset_dir: Path,
    trained: Path,
    active_game: Path,
    stub_model,
) -> None:
    """The floor is where "none of these" gets decided, and the numbers behind it
    are the only way to tell a close call from the model having no idea."""
    write_dataset(dataset_dir, {"AA": 1})
    write_split(captures, "shot", rows=1, columns=2)
    stub_model([row(AA=0.95), row(BB=0.42, AA=0.30)])

    data = assert_success(
        (
            await client.post(
                "/api/image-classifier/classify",
                json={"split": "shot", "min_confidence": 0.6},
            )
        ).json()
    )

    named, unknown = data["tiles"]
    assert named["symbol"] == "AA"
    assert named["label"] == "Ox"
    assert named["known"] is True

    assert unknown["symbol"] is None
    assert unknown["label"] == "unknown"
    assert unknown["known"] is False
    assert unknown["confidence"] == pytest.approx(0.42)
    assert [entry["symbol"] for entry in unknown["predictions"][:2]] == ["BB", "AA"]
    assert data["symbol_grid"] == [["AA", None]]
    assert data["unknown"] == 1


async def test_the_floor_can_be_lowered_for_one_request(
    client: AsyncClient,
    captures: Path,
    dataset_dir: Path,
    trained: Path,
    active_game: Path,
    stub_model,
) -> None:
    write_dataset(dataset_dir, {"AA": 1})
    write_split(captures, "shot", rows=1, columns=1)
    stub_model([row(AA=0.42)])

    strict = assert_success(
        (
            await client.post(
                "/api/image-classifier/classify",
                json={"split": "shot", "min_confidence": 0.6},
            )
        ).json()
    )
    lenient = assert_success(
        (
            await client.post(
                "/api/image-classifier/classify",
                json={"split": "shot", "min_confidence": 0.4},
            )
        ).json()
    )

    assert strict["symbol_grid"] == [[None]]
    assert lenient["symbol_grid"] == [["AA"]]


async def test_classify_accepts_a_split_the_active_config_does_not_describe(
    client: AsyncClient,
    captures: Path,
    dataset_dir: Path,
    trained: Path,
    active_game: Path,
    stub_model,
) -> None:
    """Deliberately unlike the payline check, which 409s a differently shaped
    split because only the config says where a line runs. A classifier names each
    tile on its own, so refusing an older split would cost something and buy
    nothing."""
    write_dataset(dataset_dir, {"AA": 1})
    write_split(captures, "older", rows=4, columns=6)
    stub_model(None)

    response = await client.post(
        "/api/image-classifier/classify", json={"split": "older"}
    )

    data = assert_success(response.json())
    assert (data["rows"], data["columns"]) == (4, 6)
    assert len(data["tiles"]) == 24


async def test_classify_falls_back_to_bare_codes_when_a_game_names_no_symbols(
    client: AsyncClient,
    captures: Path,
    dataset_dir: Path,
    trained: Path,
    game,
    stub_model,
) -> None:
    """HuffNPuffLink declares no ``symbols`` block, and that must not be fatal."""
    game({"name": "HuffNPuffLink", "process": "HuffNPuff.exe", "roi": {}})
    write_dataset(dataset_dir, {"AA": 1})
    write_split(captures, "shot", rows=1, columns=1)
    stub_model([row(AA=0.9)])

    data = assert_success(
        (
            await client.post("/api/image-classifier/classify", json={"split": "shot"})
        ).json()
    )

    assert data["tiles"][0]["symbol"] == "AA"
    assert data["tiles"][0]["label"] == "AA"


async def test_classify_writes_the_overlay_into_the_split_it_read(
    client: AsyncClient,
    captures: Path,
    dataset_dir: Path,
    trained: Path,
    active_game: Path,
    stub_model,
) -> None:
    write_dataset(dataset_dir, {"AA": 1})
    directory = write_split(captures, "shot")
    stub_model(None)

    data = assert_success(
        (
            await client.post("/api/image-classifier/classify", json={"split": "shot"})
        ).json()
    )

    assert data["overlay_file"] == "symbols.png"
    assert (directory / "classifier" / "symbols.png").is_file()
    assert data["overlay_image"] is not None


async def test_classifying_the_same_split_twice_gives_the_same_answer(
    client: AsyncClient,
    captures: Path,
    dataset_dir: Path,
    trained: Path,
    active_game: Path,
    stub_model,
) -> None:
    """It reads a written split rather than taking a screenshot, which is the
    whole reason a threshold can be re-tried after the spin has gone."""
    write_dataset(dataset_dir, {"AA": 1})
    write_split(captures, "shot")
    stub_model([row(AA=0.9)] * 15)

    first = assert_success(
        (
            await client.post("/api/image-classifier/classify", json={"split": "shot"})
        ).json()
    )
    second = assert_success(
        (
            await client.post("/api/image-classifier/classify", json={"split": "shot"})
        ).json()
    )

    assert first["symbol_grid"] == second["symbol_grid"]
    assert [t["confidence"] for t in first["tiles"]] == [
        t["confidence"] for t in second["tiles"]
    ]


async def test_classify_without_a_model_is_a_409_naming_what_to_do(
    client: AsyncClient,
    captures: Path,
    dataset_dir: Path,
    model_dir: Path,
    active_game: Path,
    stub_model,
) -> None:
    write_dataset(dataset_dir, {"AA": 1})
    write_split(captures, "shot")

    response = await client.post(
        "/api/image-classifier/classify", json={"split": "shot"}
    )

    assert response.status_code == 409
    error = assert_failure(response.json(), code="CLASSIFIER_UNTRAINED")
    assert error["code"] == "CLASSIFIER_UNTRAINED"


async def test_classify_of_an_unknown_split_is_a_404(
    client: AsyncClient,
    captures: Path,
    dataset_dir: Path,
    trained: Path,
    active_game: Path,
    stub_model,
) -> None:
    write_dataset(dataset_dir, {"AA": 1})
    write_split(captures, "shot")

    response = await client.post(
        "/api/image-classifier/classify", json={"split": "nope"}
    )

    assert response.status_code == 404


async def test_classify_rejects_a_split_name_that_is_not_a_bare_name(
    client: AsyncClient,
    captures: Path,
    dataset_dir: Path,
    trained: Path,
    active_game: Path,
    stub_model,
) -> None:
    write_dataset(dataset_dir, {"AA": 1})
    write_split(captures, "shot")

    response = await client.post(
        "/api/image-classifier/classify", json={"split": "../secrets"}
    )

    assert response.status_code == 400


async def test_classify_forbids_unknown_request_fields(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/image-classifier/classify", json={"threshold": 0.5}
    )

    assert response.status_code == 422


# --- training -------------------------------------------------------------


async def test_training_without_a_dataset_is_a_404_naming_the_setting(
    client: AsyncClient,
    dataset_dir: Path,
    model_dir: Path,
    stub_model,
) -> None:
    response = await client.post("/api/image-classifier/train", json={})

    assert response.status_code == 404
    assert_failure(response.json(), code="CLASSIFIER_DATASET_NOT_FOUND")
    assert "CLASSIFIER_DATASET_DIR" in response.json()["message"]


async def test_training_creates_every_stage_up_front(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    dataset_dir: Path,
    model_dir: Path,
) -> None:
    """A failure in stage two has to leave stages three onward visibly unreached,
    which is only possible if they existed before the run started."""
    write_dataset(dataset_dir, {"AA": 2, "BB": 2})
    import app.utils.symbol_model as symbol_model

    started = asyncio.Event()
    release = asyncio.Event()

    def slow_train(**kwargs: Any) -> Any:
        started.set()
        # Blocks on the worker thread until the test lets go.
        asyncio.run(asyncio.sleep(0))
        while not release.is_set():
            if kwargs["should_cancel"]():
                raise symbol_model.TrainingCancelled
        raise symbol_model.TrainingCancelled

    monkeypatch.setattr(symbol_model, "train", slow_train)

    data = assert_success(
        (await client.post("/api/image-classifier/train", json={})).json()
    )
    assert data["state"] == "running"
    assert [stage["key"] for stage in data["stages"]] == [
        "prepare",
        "head",
        "finetune",
        "evaluate",
        "save",
    ]
    assert {stage["state"] for stage in data["stages"]} == {"pending"}
    assert data["epoch_total"] == (
        settings.CLASSIFIER_EPOCHS_HEAD + settings.CLASSIFIER_EPOCHS_FINETUNE
    )

    release.set()
    await classifier_service.abort()


async def test_a_second_training_run_is_refused_while_one_is_going(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    dataset_dir: Path,
    model_dir: Path,
) -> None:
    write_dataset(dataset_dir, {"AA": 2})
    import app.utils.symbol_model as symbol_model

    release = asyncio.Event()

    def slow_train(**kwargs: Any) -> Any:
        while not release.is_set():
            pass
        raise symbol_model.TrainingCancelled

    monkeypatch.setattr(symbol_model, "train", slow_train)

    await client.post("/api/image-classifier/train", json={})
    response = await client.post("/api/image-classifier/train", json={})

    assert response.status_code == 409
    assert_failure(response.json(), code="CLASSIFIER_ALREADY_TRAINING")

    release.set()
    await classifier_service.abort()


async def test_cancelling_when_nothing_is_training_is_a_409(
    client: AsyncClient,
) -> None:
    response = await client.post("/api/image-classifier/train/cancel")

    assert response.status_code == 409
    assert_failure(response.json(), code="CLASSIFIER_NOT_TRAINING")


async def test_a_failed_run_records_why_and_marks_the_rest_unreached(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    dataset_dir: Path,
    model_dir: Path,
    active_game: Path,
) -> None:
    write_dataset(dataset_dir, {"AA": 2})
    import app.utils.symbol_model as symbol_model

    def failing_train(**kwargs: Any) -> Any:
        kwargs["on_stage"]("prepare")
        raise symbol_model.ModelError("the weights could not be downloaded")

    monkeypatch.setattr(symbol_model, "train", failing_train)

    await client.post("/api/image-classifier/train", json={})
    for _ in range(200):
        run = assert_success((await client.get("/api/image-classifier/status")).json())[
            "training"
        ]
        if run["state"] != "running":
            break
        await asyncio.sleep(0.01)

    assert run["state"] == "failed"
    assert "weights could not be downloaded" in run["error"]
    assert run["error_code"] == "CLASSIFIER_TRAIN_FAILED"
    states = {stage["key"]: stage["state"] for stage in run["stages"]}
    assert states["save"] in {"skipped", "failed"}


# --- splits ---------------------------------------------------------------


async def test_splits_lists_what_could_be_classified_newest_first(
    client: AsyncClient,
    captures: Path,
) -> None:
    write_split(captures, "older")
    directory = write_split(captures, "newer")
    os.utime(directory, (0, 4_000_000_000))

    data = assert_success((await client.get("/api/image-classifier/splits")).json())

    assert [entry["name"] for entry in data["splits"]] == ["newer", "older"]
    assert data["splits"][0]["tiles"] == 15
    assert (data["splits"][0]["rows"], data["splits"][0]["columns"]) == (3, 5)


async def test_splits_says_so_when_nothing_has_been_split(
    client: AsyncClient,
    captures: Path,
) -> None:
    data = assert_success((await client.get("/api/image-classifier/splits")).json())

    assert data["splits"] == []
    assert data["error"] is not None


# --- the dataset utilities ------------------------------------------------


def test_a_transparent_cutout_is_composed_onto_the_reel_field(
    tmp_path: Path,
) -> None:
    """The fix the previous attempt was missing: a cut-out trained against
    nothing never matches a tile whose symbol sits on dark purple."""
    write_dataset(tmp_path, {"AA": 1})
    classes = symbol_dataset.load_sources(tmp_path)

    picture = symbol_dataset.compose(
        classes[0].sources[0], 64, random.Random(3), style="solid"
    )

    corner = picture.convert("RGB").getpixel((1, 1))
    assert corner is not None
    # Near the measured field colour rather than white, black or transparent.
    assert abs(corner[0] - symbol_dataset.FIELD_RGB[0]) <= 8
    assert abs(corner[2] - symbol_dataset.FIELD_RGB[2]) <= 8


def test_frames_are_held_back_from_the_middle_of_the_loop(tmp_path: Path) -> None:
    """Holding back the *last* frames is nearly worthless -- an animation loop
    closes, so its final frame is a near-duplicate of its first."""
    kept, held = symbol_dataset.frame_split(48, 8)

    assert held == list(range(20, 28))
    assert len(kept) == 40
    assert 47 in kept


def test_a_class_too_small_to_spare_a_block_holds_nothing_back(
    tmp_path: Path,
) -> None:
    """A single-image class contributes to no honest accuracy figure, and saying
    otherwise would be the one genuinely misleading thing here."""
    kept, held = symbol_dataset.frame_split(1, 8)

    assert (kept, held) == ([0], [])


def test_leakage_is_zero_when_the_held_back_frames_are_duplicates(
    tmp_path: Path,
) -> None:
    write_dataset(tmp_path, {"AA": 4})
    sources = symbol_dataset.load_sources(tmp_path)[0].sources
    identical = tuple(sources[0] for _ in range(4))

    assert symbol_dataset.leakage(identical, [0, 1, 2], [3]) == 0.0


# --- the real thing, opt-in ----------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("CLASSIFIER_SLOW_TESTS"),
    reason="trains a real model; set CLASSIFIER_SLOW_TESTS=1 to run",
)
def test_a_real_fit_learns_the_training_symbols(
    tmp_path: Path,
) -> None:
    """The only test that really trains. Opt-in: a four-minute unit test is a
    broken suite, but never running the real path at all is worse."""
    import app.utils.symbol_model as symbol_model

    write_dataset(tmp_path / "data", {"AA": 4, "BB": 4, "CC": 4})
    result = symbol_model.train(
        dataset_dir=tmp_path / "data",
        checkpoint_path=tmp_path / "model.pt",
        image_size=64,
        epochs_head=1,
        epochs_finetune=1,
        samples_per_epoch=48,
        batch_size=8,
        lr_head=1e-3,
        lr_finetune=1e-4,
        background="plate",
        holdout_frames=0,
        pretrained=False,
        seed=1,
        threads=2,
    )

    assert (tmp_path / "model.pt").is_file()
    assert result.metrics.classes == ["AA", "BB", "CC"]
    loaded = symbol_model.load(tmp_path / "model.pt")
    assert loaded.classes == ["AA", "BB", "CC"]
    assert loaded.transform_version == symbol_model.TRANSFORM_VERSION
