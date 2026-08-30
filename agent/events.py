"""Build compact v2 telemetry and structural-change events from snapshots."""

from __future__ import annotations

from agent.models import (
    AgentEventType,
    ContainerChangedEventPayload,
    ContainerResourceTelemetry,
    DeploymentChangedEventPayload,
    HeartbeatPayload,
    InstanceChangedEventPayload,
    InstanceResourceTelemetry,
    NodeSnapshotEventPayload,
    NodeTelemetryEventPayload,
    ProtocolEnvelope,
    make_agent_event,
)


class AgentEventEmitter:
    """Keeps only the last observed structural state in process memory."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self._instances: dict[str, dict] = {}

    def snapshot(self, heartbeat: HeartbeatPayload) -> ProtocolEnvelope:
        self._remember(heartbeat)
        return make_agent_event(
            AgentEventType.NODE_SNAPSHOT,
            self.node_id,
            NodeSnapshotEventPayload(
                agent_version=heartbeat.agent_version,
                capabilities=heartbeat.capabilities,
                host=heartbeat.host,
                instances=heartbeat.instances,
            ),
        )

    def changes_and_telemetry(self, heartbeat: HeartbeatPayload) -> list[ProtocolEnvelope]:
        events: list[ProtocolEnvelope] = [
            make_agent_event(
                AgentEventType.TELEMETRY,
                self.node_id,
                NodeTelemetryEventPayload(
                    host=heartbeat.host,
                    instances=[
                        InstanceResourceTelemetry(
                            app_id=instance.app_id,
                            resources=instance.resources,
                            containers=[
                                ContainerResourceTelemetry(
                                    name=container.name,
                                    cpu_percent=container.cpu_percent,
                                    memory_used_mb=container.memory_used_mb,
                                )
                                for container in instance.containers
                            ],
                        )
                        for instance in heartbeat.instances
                    ],
                ),
            )
        ]
        for instance in heartbeat.instances:
            current = self._instance_signature(instance)
            previous = self._instances.get(instance.app_id)
            if previous is None or previous["instance"] != current["instance"]:
                events.append(make_agent_event(AgentEventType.INSTANCE_CHANGED, self.node_id, InstanceChangedEventPayload(instance=instance)))
            if previous is None or previous["deployment"] != current["deployment"]:
                events.append(
                    make_agent_event(
                        AgentEventType.DEPLOYMENT_CHANGED,
                        self.node_id,
                        DeploymentChangedEventPayload(app_id=instance.app_id, deployment=instance.deployment),
                    )
                )
            previous_containers = previous["containers"] if previous else {}
            for name, container in current["containers"].items():
                if previous_containers.get(name) != container:
                    events.append(
                        make_agent_event(
                            AgentEventType.CONTAINER_CHANGED,
                            self.node_id,
                            ContainerChangedEventPayload(app_id=instance.app_id, container=current["containers_by_name"][name]),
                        )
                    )
            self._instances[instance.app_id] = current
        return events

    def _remember(self, heartbeat: HeartbeatPayload) -> None:
        self._instances = {instance.app_id: self._instance_signature(instance) for instance in heartbeat.instances}

    @staticmethod
    def _instance_signature(instance) -> dict:
        containers_by_name = {container.name: container for container in instance.containers}
        # Attach the retained objects for event construction without sending the
        # signature wrapper over the wire.
        return {
            "instance": {
                "repository": instance.repository,
                "branch": instance.branch,
                "compose_project_name": instance.compose_project_name,
            },
            "deployment": instance.deployment.model_dump(mode="json"),
            # CPU and memory are telemetry, not structural state. Comparing the
            # whole model here caused every sampled container to emit a
            # container.changed event on each collection cycle.
            "containers": {
                name: AgentEventEmitter._container_signature(container)
                for name, container in containers_by_name.items()
            },
            "containers_by_name": containers_by_name,
        }

    @staticmethod
    def _container_signature(container) -> dict:
        return {
            "service": container.service,
            "status": container.status,
            "health": container.health,
            "memory_limit_mb": container.memory_limit_mb,
            "restart_count": container.restart_count,
        }
