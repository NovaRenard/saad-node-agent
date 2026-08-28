"""Typed wire models for telemetry sent to saad-dashboard."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

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


class StrictEventModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentEventType(str, enum.Enum):
    NODE_SNAPSHOT = "node.snapshot"
    NODE_TELEMETRY = "node.telemetry"
    INSTANCE_CHANGED = "instance.changed"
    CONTAINER_CHANGED = "container.changed"
    DEPLOYMENT_CHANGED = "deployment.changed"
    COMMAND_ACK = "command.ack"
    COMMAND_RESULT = "command.result"
    LOGS_CHUNK = "logs.chunk"
    LOGS_ENDED = "logs.ended"
    ERROR = "error"


class DashboardEventType(str, enum.Enum):
    SNAPSHOT_REQUEST = "snapshot.request"
    COMMAND_EXECUTE = "command.execute"
    LOGS_START = "logs.start"
    LOGS_STOP = "logs.stop"
    ERROR = "error"


class CommandType(str, enum.Enum):
    DEPLOY = "DEPLOY"
    RESTART = "RESTART"
    ROLLBACK = "ROLLBACK"


class NodeSnapshotEventPayload(StrictEventModel):
    agent_version: str
    host: HostTelemetry
    instances: list[InstanceTelemetry] = Field(default_factory=list, max_length=100)


class InstanceResourceTelemetry(StrictEventModel):
    app_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    resources: InstanceResources


class NodeTelemetryEventPayload(StrictEventModel):
    host: HostTelemetry
    instances: list[InstanceResourceTelemetry] = Field(default_factory=list, max_length=100)


class InstanceChangedEventPayload(StrictEventModel):
    instance: InstanceTelemetry


class ContainerChangedEventPayload(StrictEventModel):
    app_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    container: ContainerTelemetry


class DeploymentChangedEventPayload(StrictEventModel):
    app_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    deployment: DeploymentTelemetry


class SnapshotRequestPayload(StrictEventModel):
    pass


class CommandExecutePayload(StrictEventModel):
    command_id: UUID
    command_type: CommandType
    app_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_-]*$")


class LogsStartPayload(StrictEventModel):
    request_id: UUID
    app_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    container: str = Field(min_length=1, max_length=255)
    tail_lines: int = Field(ge=1, le=1000)


class LogsStopPayload(StrictEventModel):
    request_id: UUID


class LogsChunkPayload(StrictEventModel):
    request_id: UUID
    text: str = Field(min_length=1, max_length=16_384)


class LogsEndedPayload(StrictEventModel):
    request_id: UUID
    reason: str = Field(min_length=1, max_length=80)


class ProtocolErrorPayload(StrictEventModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)


class ProtocolEnvelope(StrictEventModel):
    protocol_version: int = Field(default=2, frozen=True)
    message_id: UUID = Field(default_factory=uuid4)
    type: AgentEventType | DashboardEventType
    node_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    sent_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: UUID | None = None
    payload: dict[str, Any]


def make_agent_event(
    event_type: AgentEventType,
    node_id: str,
    payload: BaseModel,
    *,
    correlation_id: UUID | None = None,
) -> ProtocolEnvelope:
    return ProtocolEnvelope(
        type=event_type,
        node_id=node_id,
        correlation_id=correlation_id,
        payload=payload.model_dump(mode="json"),
    )


_DASHBOARD_PAYLOADS: dict[DashboardEventType, type[BaseModel]] = {
    DashboardEventType.SNAPSHOT_REQUEST: SnapshotRequestPayload,
    DashboardEventType.COMMAND_EXECUTE: CommandExecutePayload,
    DashboardEventType.LOGS_START: LogsStartPayload,
    DashboardEventType.LOGS_STOP: LogsStopPayload,
    DashboardEventType.ERROR: ProtocolErrorPayload,
}


def parse_dashboard_event(raw: object) -> tuple[ProtocolEnvelope, BaseModel]:
    """Validate dashboard frames before any operation is considered."""

    envelope = ProtocolEnvelope.model_validate(raw)
    if envelope.protocol_version != 2 or not isinstance(envelope.type, DashboardEventType):
        raise ValueError("unsupported dashboard protocol event")
    payload_model = _DASHBOARD_PAYLOADS[envelope.type]
    return envelope, payload_model.model_validate(envelope.payload)
