"""Scoring one picture against a folder of candidates.

Everything here runs off real image files in ``tmp_path``. The pictures are
solid colours because cosine similarity of two uniform images is exact and easy
to reason about: the same colour scores 1.0, orthogonal channels score 0.0, and
``(200, 100, 50)`` against ``(50, 100, 200)`` scores 30000/52500 = 0.571429 --
so a test can place scores on either side of the threshold deliberately rather
than hoping two photographs land where it needs them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient
from PIL import Image

from app.core.config import settings
from tests.asserts import assert_failure, assert_success

API = "/api/similarity"

# Pinned so a change to the shipped default doesn't move these tests.
CUT = 0.9

SOURCE_COLOR = (200, 100, 50)
# One channel nudged: scores ~0.999755 against the source -- a match that is
# not 1.0, so matched_min has something below the ceiling to report.
NEAR_COLOR = (190, 100, 50)
# The source's channels reversed: scores 0.571429, comfortably under the cut.
FAR_COLOR = (50, 100, 200)

FAR_SCORE = 0.571429


@pytest.fixture(autouse=True)
def _pin_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PAYLINE_MATCH_THRESHOLD", CUT)


def write_image(
    path: Path,
    *,
    color: tuple[int, int, int] = SOURCE_COLOR,
    size: tuple[int, int] = (40, 40),
) -> Path:
    """Write one solid-colour picture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def payload(source: Path, directory: Path) -> dict[str, str]:
    return {"source": str(source), "directory": str(directory)}


async def test_compare_scores_every_image_best_first(
    client: AsyncClient, tmp_path: Path
) -> None:
    source = write_image(tmp_path / "source.png")
    library = tmp_path / "library"
    write_image(library / "twin.png")
    write_image(library / "near.png", color=NEAR_COLOR)
    write_image(library / "far.png", color=FAR_COLOR)

    response = await client.post(f"{API}/compare", json=payload(source, library))

    assert response.status_code == 200
    data = assert_success(response.json())
    assert [match["file_name"] for match in data["results"]] == [
        "twin.png",
        "near.png",
        "far.png",
    ]
    twin, near, far = data["results"]
    assert twin["score"] == pytest.approx(1.0)
    assert twin["matched"] is True
    assert CUT < near["score"] < 1.0
    assert near["matched"] is True
    assert far["score"] == pytest.approx(FAR_SCORE, abs=1e-6)
    assert far["matched"] is False
    assert data["threshold"] == CUT
    assert data["source"]["file_name"] == "source.png"
    assert data["stats"]["candidates"] == 3


async def test_omitted_paths_fall_back_to_the_settings_defaults(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write_image(tmp_path / "default-source.png")
    library = tmp_path / "default-library"
    write_image(library / "twin.png")
    monkeypatch.setattr(settings, "SIMILARITY_SOURCE", source)
    monkeypatch.setattr(settings, "SIMILARITY_CANDIDATES_DIR", library)

    response = await client.post(f"{API}/compare", json={})

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["source"]["file_name"] == "default-source.png"
    assert data["directory"] == str(library)
    assert data["stats"]["candidates"] == 1


async def test_candidates_in_subfolders_are_found_under_relative_names(
    client: AsyncClient, tmp_path: Path
) -> None:
    source = write_image(tmp_path / "source.png")
    library = tmp_path / "library"
    write_image(library / "AA" / "AA_00000.png")

    response = await client.post(f"{API}/compare", json=payload(source, library))

    data = assert_success(response.json())
    assert [match["file_name"] for match in data["results"]] == ["AA/AA_00000.png"]


async def test_a_differently_sized_candidate_is_resized_and_flagged(
    client: AsyncClient, tmp_path: Path
) -> None:
    source = write_image(tmp_path / "source.png")
    library = tmp_path / "library"
    write_image(library / "big.png", size=(80, 80))

    response = await client.post(f"{API}/compare", json=payload(source, library))

    data = assert_success(response.json())
    (big,) = data["results"]
    assert big["resized"] is True
    assert (big["width"], big["height"]) == (80, 80)
    assert big["score"] == pytest.approx(1.0)
    assert data["stats"]["resized"] == 1


async def test_an_unreadable_candidate_fails_alone_on_its_own_row(
    client: AsyncClient, tmp_path: Path
) -> None:
    source = write_image(tmp_path / "source.png")
    library = tmp_path / "library"
    write_image(library / "good.png")
    (library / "bad.png").write_bytes(b"not a picture")

    response = await client.post(f"{API}/compare", json=payload(source, library))

    assert response.status_code == 200
    data = assert_success(response.json())
    assert [match["file_name"] for match in data["results"]] == [
        "good.png",
        "bad.png",
    ]
    bad = data["results"][-1]
    assert bad["score"] is None
    assert bad["matched"] is False
    assert "could not be read" in bad["error"]
    stats = data["stats"]
    assert (stats["candidates"], stats["compared"], stats["errors"]) == (2, 1, 1)


async def test_a_request_threshold_overrides_the_settings_cut(
    client: AsyncClient, tmp_path: Path
) -> None:
    source = write_image(tmp_path / "source.png")
    library = tmp_path / "library"
    write_image(library / "far.png", color=FAR_COLOR)

    body = payload(source, library) | {"threshold": 0.5}
    response = await client.post(f"{API}/compare", json=body)

    data = assert_success(response.json())
    assert data["threshold"] == 0.5
    assert data["results"][0]["matched"] is True


async def test_stats_bracket_the_threshold(client: AsyncClient, tmp_path: Path) -> None:
    source = write_image(tmp_path / "source.png")
    library = tmp_path / "library"
    write_image(library / "twin.png")
    write_image(library / "near.png", color=NEAR_COLOR)
    write_image(library / "far.png", color=FAR_COLOR)

    response = await client.post(f"{API}/compare", json=payload(source, library))

    stats = assert_success(response.json())["stats"]
    assert stats["matches"] == 2
    assert stats["score_min"] == pytest.approx(FAR_SCORE, abs=1e-6)
    assert stats["score_max"] == pytest.approx(1.0)
    assert CUT < stats["matched_min"] < 1.0
    assert stats["rejected_max"] == pytest.approx(FAR_SCORE, abs=1e-6)


async def test_non_image_files_are_not_candidates(
    client: AsyncClient, tmp_path: Path
) -> None:
    source = write_image(tmp_path / "source.png")
    library = tmp_path / "library"
    write_image(library / "twin.png")
    (library / "notes.txt").write_text("not a candidate", encoding="utf-8")

    response = await client.post(f"{API}/compare", json=payload(source, library))

    data = assert_success(response.json())
    assert data["stats"]["candidates"] == 1


async def test_404s_on_a_source_that_is_not_there(
    client: AsyncClient, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    write_image(library / "twin.png")

    body = payload(tmp_path / "missing.png", library)
    response = await client.post(f"{API}/compare", json=body)

    assert response.status_code == 404
    body = response.json()
    assert_failure(body, code="SIMILARITY_SOURCE_NOT_FOUND")
    assert "missing.png" in body["message"]


async def test_404s_on_a_folder_that_is_not_there(
    client: AsyncClient, tmp_path: Path
) -> None:
    source = write_image(tmp_path / "source.png")

    body = payload(source, tmp_path / "nowhere")
    response = await client.post(f"{API}/compare", json=body)

    assert response.status_code == 404
    body = response.json()
    assert_failure(body, code="SIMILARITY_DIRECTORY_NOT_FOUND")
    assert "nowhere" in body["message"]


async def test_404s_on_a_folder_with_nothing_to_compare_against(
    client: AsyncClient, tmp_path: Path
) -> None:
    source = write_image(tmp_path / "source.png")
    library = tmp_path / "library"
    library.mkdir()

    response = await client.post(f"{API}/compare", json=payload(source, library))

    assert response.status_code == 404
    body = response.json()
    assert_failure(body, code="SIMILARITY_NO_CANDIDATES")
    assert ".png" in body["message"]


async def test_502s_on_a_source_that_is_not_a_picture(
    client: AsyncClient, tmp_path: Path
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"not a picture")
    library = tmp_path / "library"
    write_image(library / "twin.png")

    response = await client.post(f"{API}/compare", json=payload(source, library))

    assert response.status_code == 502
    body = response.json()
    assert_failure(body, code="SIMILARITY_SOURCE_UNREADABLE")
    assert "source.png" in body["message"]


async def test_unknown_request_fields_are_rejected(
    client: AsyncClient, tmp_path: Path
) -> None:
    response = await client.post(f"{API}/compare", json={"bogus": True})

    assert response.status_code == 422
    assert_failure(response.json(), code="VALIDATION_ERROR")
