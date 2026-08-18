"""Health endpoint behaviour."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.schemas.health import DependencyCheck
from app.services import health
from tests.asserts import assert_failure, assert_success


async def test_health_returns_report(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["status"] == "healthy"
    assert data["service"]
    assert data["version"]
    assert data["environment"]
    assert data["uptime_seconds"] >= 0
    assert data["checks"] == []


async def test_liveness_is_dependency_free(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["status"] == "healthy"


async def test_readiness_ok_without_probes(client: AsyncClient) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["status"] == "healthy"


async def test_health_is_not_under_the_api_prefix(client: AsyncClient) -> None:
    """The whole point of mounting at the root: /api/health must not exist."""
    response = await client.get("/api/health")

    assert response.status_code == 404
    assert_failure(response.json(), code="NOT_FOUND")


async def test_readiness_reports_503_when_a_probe_fails(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_probe() -> DependencyCheck:
        return DependencyCheck(
            name="postgres", healthy=False, error="connection refused"
        )

    monkeypatch.setattr(health, "_PROBES", [failing_probe])

    response = await client.get("/health/ready")

    assert response.status_code == 503
    error = assert_failure(response.json(), code="SERVICE_UNAVAILABLE")
    assert error["details"] == [
        {
            "field": "postgres",
            "message": "connection refused",
            "type": "dependency_unavailable",
        }
    ]


async def test_health_reports_degraded_without_failing_the_request(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/health is a report: it stays 200 and puts the verdict in the payload."""

    async def healthy_probe() -> DependencyCheck:
        return DependencyCheck(name="cache", healthy=True, latency_ms=1.0)

    async def failing_probe() -> DependencyCheck:
        return DependencyCheck(name="postgres", healthy=False, error="timeout")

    monkeypatch.setattr(health, "_PROBES", [healthy_probe, failing_probe])

    response = await client.get("/health")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["status"] == "degraded"
    assert {check["name"] for check in data["checks"]} == {"cache", "postgres"}


async def test_probe_failure_does_not_break_health(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A probe that raises is reported as unhealthy, not propagated as a 500."""

    async def exploding_probe() -> DependencyCheck:
        raise RuntimeError("boom")

    monkeypatch.setattr(health, "_PROBES", [exploding_probe])

    response = await client.get("/health")

    assert response.status_code == 200
    data = assert_success(response.json())
    assert data["status"] == "unhealthy"
    assert data["checks"][0]["healthy"] is False
    assert "RuntimeError: boom" in data["checks"][0]["error"]
