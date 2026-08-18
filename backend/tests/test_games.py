"""Game catalog and runtime-selection endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.config.game_config import load_active_game, save_active_game
from app.core.config import settings
from tests.asserts import assert_failure, assert_success


def write_game(directory: Path, name: str, process: str) -> None:
    (directory / f"{name}.json").write_text(
        json.dumps({"name": name, "process": process}), encoding="utf-8"
    )


def configure_games(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_game(tmp_path, "HuffNPuffLink", "HuffNPuffLink.exe")
    write_game(tmp_path, "FortuneOx", "FortuneOx.exe")
    monkeypatch.setattr(settings, "IDECK_GAME_CONFIG_DIR", tmp_path)
    save_active_game(settings.ideck_active_game_path, "HuffNPuffLink")


async def test_catalog_lists_available_configs_and_active_game(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_games(tmp_path, monkeypatch)

    response = await client.get("/api/games/")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["active_game"] == "HuffNPuffLink"
    assert [game["game"] for game in data["games"]] == ["FortuneOx", "HuffNPuffLink"]


async def test_selecting_game_changes_runtime_config(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_games(tmp_path, monkeypatch)

    response = await client.put("/api/games/active", json={"game": "FortuneOx"})

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["game"] == "FortuneOx"
    assert data["process"] == "FortuneOx.exe"
    assert data["obs_window_selected"] is None
    assert load_active_game(settings.ideck_active_game_path) == "FortuneOx"


async def test_selecting_unknown_game_returns_not_found(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_games(tmp_path, monkeypatch)

    response = await client.put("/api/games/active", json={"game": "Missing"})

    assert response.status_code == 404
    assert_failure(response.json(), code="GAME_NOT_FOUND")
