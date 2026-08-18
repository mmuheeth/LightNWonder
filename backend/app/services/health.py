"""Health reporting and the dependency-probe registry.

Register a probe at startup for anything the service cannot work without::

    from app.services.health import register_probe, probe

    @register_probe
    async def postgres() -> DependencyCheck:
        return await probe("postgres", lambda: db.execute(text("SELECT 1")))

Probes feed both ``GET /health`` and ``GET /health/ready``. Keep them cheap --
readiness is polled frequently.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from app.core.logging import get_logger
from app.schemas.health import DependencyCheck, HealthState

logger = get_logger("health")

HealthProbe = Callable[[], Awaitable[DependencyCheck]]

_PROBES: list[HealthProbe] = []
_STARTED_AT = time.monotonic()

# A probe that hangs must not hang the readiness endpoint.
PROBE_TIMEOUT_SECONDS = 3.0


def register_probe(probe: HealthProbe) -> HealthProbe:
    """Register a dependency probe. Usable as a decorator."""
    _PROBES.append(probe)
    return probe


def uptime_seconds() -> float:
    """Seconds elapsed since the process started."""
    return round(time.monotonic() - _STARTED_AT, 3)


async def probe(name: str, check: Callable[[], Awaitable[object]]) -> DependencyCheck:
    """Run ``check`` and package the outcome, timing it and trapping errors."""
    started = time.perf_counter()
    try:
        await check()
    except Exception as exc:  # noqa: BLE001 - a failed probe is a normal outcome
        return DependencyCheck(
            name=name,
            healthy=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            error=f"{type(exc).__name__}: {exc}",
        )
    return DependencyCheck(
        name=name,
        healthy=True,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )


async def run_probes() -> list[DependencyCheck]:
    """Run every registered probe concurrently."""
    if not _PROBES:
        return []

    async def guarded(fn: HealthProbe) -> DependencyCheck:
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                return await fn()
        except TimeoutError:
            return DependencyCheck(
                name=getattr(fn, "__name__", "unknown"),
                healthy=False,
                error=f"Probe timed out after {PROBE_TIMEOUT_SECONDS}s",
            )
        except Exception as exc:
            logger.exception("Health probe %r raised", getattr(fn, "__name__", fn))
            return DependencyCheck(
                name=getattr(fn, "__name__", "unknown"),
                healthy=False,
                error=f"{type(exc).__name__}: {exc}",
            )

    return list(await asyncio.gather(*(guarded(fn) for fn in _PROBES)))


def aggregate(checks: list[DependencyCheck]) -> HealthState:
    """Reduce individual probe results to one verdict."""
    if not checks:
        return HealthState.HEALTHY
    failed = sum(1 for check in checks if not check.healthy)
    if failed == 0:
        return HealthState.HEALTHY
    if failed == len(checks):
        return HealthState.UNHEALTHY
    return HealthState.DEGRADED
