"""Health-check payloads."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class HealthState(StrEnum):
    """Aggregate health verdict."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class DependencyCheck(BaseModel):
    """Result of probing one downstream dependency."""

    name: str = Field(description="Dependency name, e.g. 'postgres'.")
    healthy: bool = Field(description="Whether the dependency answered correctly.")
    latency_ms: float | None = Field(
        default=None, description="Round-trip time of the probe."
    )
    error: str | None = Field(
        default=None, description="Failure reason when unhealthy."
    )


class HealthStatus(BaseModel):
    """Payload returned by ``GET /health``."""

    status: HealthState = Field(description="Aggregate verdict.")
    service: str = Field(description="Service name.")
    version: str = Field(description="Deployed version.")
    environment: str = Field(description="Environment the process runs in.")
    uptime_seconds: float = Field(description="Seconds since process start.")
    checks: list[DependencyCheck] = Field(
        default_factory=list, description="Per-dependency probe results."
    )


class LivenessStatus(BaseModel):
    """Payload returned by ``GET /health/live``."""

    status: HealthState = Field(description="Always 'healthy' if the process responds.")
    uptime_seconds: float = Field(description="Seconds since process start.")


class ReadinessStatus(BaseModel):
    """Payload returned by ``GET /health/ready``."""

    status: HealthState = Field(description="Aggregate verdict over dependencies.")
    checks: list[DependencyCheck] = Field(
        default_factory=list, description="Per-dependency probe results."
    )
