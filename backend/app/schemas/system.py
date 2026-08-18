"""Service-level metadata schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ServiceInfo(BaseModel):
    """Payload returned by ``GET /``."""

    service: str = Field(description="Service name.")
    version: str = Field(description="Deployed version.")
    environment: str = Field(description="Environment the process runs in.")
    docs_url: str | None = Field(
        default=None, description="Interactive docs path; null in production."
    )
    api_prefix: str = Field(description="Base path for API endpoints.")
