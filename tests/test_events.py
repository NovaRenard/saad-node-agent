from __future__ import annotations

import unittest

from agent.events import AgentEventEmitter
from agent.models import AGENT_CAPABILITIES, ContainerTelemetry, DeploymentTelemetry, InstanceResources, InstanceTelemetry
from tests.helpers import heartbeat_payload


class AgentEventEmitterTests(unittest.TestCase):
    def test_initial_snapshot_then_compact_telemetry_without_duplicate_changes(self) -> None:
        heartbeat = heartbeat_payload()
        emitter = AgentEventEmitter("econrg-lab")

        snapshot = emitter.snapshot(heartbeat)
        first_events = emitter.changes_and_telemetry(heartbeat)
        second_events = emitter.changes_and_telemetry(heartbeat)

        self.assertEqual(snapshot.type.value, "node.snapshot")
        self.assertEqual(snapshot.payload["capabilities"], list(AGENT_CAPABILITIES))
        self.assertEqual([event.type.value for event in first_events], ["telemetry"])
        self.assertEqual([event.type.value for event in second_events], ["telemetry"])

    def test_container_change_is_emitted_once_when_its_state_changes(self) -> None:
        heartbeat = heartbeat_payload()
        heartbeat.instances = [
            InstanceTelemetry(
                app_id="atlant",
                deployment=DeploymentTelemetry(status="healthy"),
                resources=InstanceResources(cpu_percent=1, memory_used_mb=100),
                containers=[ContainerTelemetry(name="atlant-backend-1", service="backend", status="running", health="healthy")],
            )
        ]
        emitter = AgentEventEmitter("econrg-lab")
        emitter.snapshot(heartbeat)
        heartbeat.instances[0].containers[0].health = "unhealthy"

        changed = emitter.changes_and_telemetry(heartbeat)
        unchanged = emitter.changes_and_telemetry(heartbeat)

        self.assertEqual([event.type.value for event in changed], ["telemetry", "container.changed"])
        self.assertEqual([event.type.value for event in unchanged], ["telemetry"])

    def test_container_resource_changes_stay_in_compact_telemetry(self) -> None:
        heartbeat = heartbeat_payload()
        heartbeat.instances = [
            InstanceTelemetry(
                app_id="atlant",
                deployment=DeploymentTelemetry(status="healthy"),
                resources=InstanceResources(cpu_percent=1, memory_used_mb=100),
                containers=[
                    ContainerTelemetry(
                        name="atlant-backend-1",
                        service="backend",
                        status="running",
                        cpu_percent=1,
                        memory_used_mb=100,
                    )
                ],
            )
        ]
        emitter = AgentEventEmitter("econrg-lab")
        emitter.snapshot(heartbeat)
        heartbeat.instances[0].containers[0].cpu_percent = 72.5
        heartbeat.instances[0].containers[0].memory_used_mb = 456

        events = emitter.changes_and_telemetry(heartbeat)

        self.assertEqual([event.type.value for event in events], ["telemetry"])
        self.assertEqual(
            events[0].payload["instances"][0]["containers"],
            [{"name": "atlant-backend-1", "cpu_percent": 72.5, "memory_used_mb": 456.0}],
        )
