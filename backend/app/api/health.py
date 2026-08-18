"""Health endpoints.

Mounted at the application root -- *not* under the ``/api`` prefix -- because
orchestrators, load balancers and uptime monitors should never have to follow a
change to the API's base path.

- ``GET /health``       full report, for humans and dashboards
- ``GET /health/live``  liveness: is the process up? never touches dependencies
- ``GET /health/ready`` readiness: can it serve traffic? 503 when it cannot

``/health`` and ``/health/live`` always answer 200: they are *reports*, and
``success: true`` means "a report was produced", with the verdict in
``data.status``. Only ``/health/ready`` fails the request, because that is the
signal orchestrators act on -- and it fails through the normal exception path so
the error envelope looks like every other error in the service.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.exceptions.base import ServiceUnavailableError
from app.schemas.health import (
    DependencyCheck,
    HealthState,
    HealthStatus,
    LivenessStatus,
    ReadinessStatus,
)
from app.schemas.response import ApiResponse, ErrorDetail
from app.services import health

router = APIRouter(tags=["health"])

HEALTH_PATHS = ("/health", "/health/live", "/health/ready")


@router.get(
    "/health",
    response_model=ApiResponse[HealthStatus],
    summary="Service health report",
)
async def get_health() -> ApiResponse[HealthStatus]:
    """Report service metadata plus the state of every dependency."""
    checks = await health.run_probes()
    state = health.aggregate(checks)

    return ApiResponse[HealthStatus].ok(
        data=HealthStatus(
            status=state,
            service=settings.APP_NAME,
            version=settings.APP_VERSION,
            environment=settings.ENVIRONMENT,
            uptime_seconds=health.uptime_seconds(),
            checks=checks,
        ),
        message=f"Service is {state.value}",
    )


@router.get(
    "/health/live",
    response_model=ApiResponse[LivenessStatus],
    summary="Liveness probe",
)
async def get_liveness() -> ApiResponse[LivenessStatus]:
    """Answer as long as the event loop is responsive.

    Intentionally dependency-free: a database outage must not cause the
    orchestrator to restart otherwise-healthy pods.
    """
    return ApiResponse[LivenessStatus].ok(
        data=LivenessStatus(
            status=HealthState.HEALTHY,
            uptime_seconds=health.uptime_seconds(),
        ),
        message="Service is live",
    )


@router.get(
    "/health/ready",
    response_model=ApiResponse[ReadinessStatus],
    summary="Readiness probe",
    responses={503: {"description": "One or more dependencies are unavailable"}},
)
async def get_readiness() -> ApiResponse[ReadinessStatus]:
    """Report whether the service can currently serve traffic.

    Raises:
        ServiceUnavailableError: when any dependency probe fails, yielding a 503
            with the failing dependencies listed in ``error.details``.
    """
    checks = await health.run_probes()
    state = health.aggregate(checks)

    if state is not HealthState.HEALTHY:
        raise ServiceUnavailableError(
            f"Service is {state.value}; not ready to serve traffic",
            details=_failure_details(checks),
        )

    return ApiResponse[ReadinessStatus].ok(
        data=ReadinessStatus(status=state, checks=checks),
        message="Service is ready",
    )


def _failure_details(checks: list[DependencyCheck]) -> list[ErrorDetail]:
    return [
        ErrorDetail(
            field=check.name,
            message=check.error or "Dependency reported unhealthy",
            type="dependency_unavailable",
        )
        for check in checks
        if not check.healthy
    ]
