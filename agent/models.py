"""Typed wire models for telemetry sent to saad-dashboard."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class HostTelemetry(BaseModel):
    hostname: str
    cpu_percent: float
    memory_used_mb: float
    memory_total_mb: float
    disk_used_gb: float
    disk_total_gb: float
    uptime_seconds: int
    load_average: list[float]


class DeploymentTelemetry(BaseModel):
    status: str = "unknown"
    step: str | None = None
    current_sha: str | None = None
    previous_sha: str | None = None
    deployed_at: str | None = None
    last_error: str | None = None


class ContainerTelemetry(BaseModel):
    name: str
    service: str | None = None
    status: str
    health: str | None = None
    cpu_percent: float = 0.0
    memory_used_mb: float = 0.0
    memory_limit_mb: float | None = None
    restart_count: int = 0


class InstanceResources(BaseModel):
    cpu_percent: float = 0.0
    memory_used_mb: float = 0.0
    containers_total: int = 0
    containers_running: int = 0
    containers_healthy: int = 0


class InstanceTelemetry(BaseModel):
    app_id: str
    repository: str | None = None
    branch: str | None = None
    compose_project_name: str | None = None
    deployment: DeploymentTelemetry
    resources: InstanceResources
    containers: list[ContainerTelemetry] = Field(default_factory=list)


class HeartbeatPayload(BaseModel):
    """Versioned dashboard heartbeat payload."""

    model_config = ConfigDict(ser_json_timedelta="iso8601")

    protocol_version: int = 1
    node_id: str
    agent_version: str
    sent_at: datetime
    host: HostTelemetry
    instances: list[InstanceTelemetry] = Field(default_factory=list)
