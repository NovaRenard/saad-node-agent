from __future__ import annotations

import unittest

from agent.events import AgentEventEmitter
from agent.models import ContainerTelemetry, DeploymentTelemetry, InstanceResources, InstanceTelemetry
from tests.helpers import heartbeat_payload


class AgentEventEmitterTests(unittest.TestCase):
    def test_initial_snapshot_then_compact_telemetry_without_duplicate_changes(self) -> None:
        heartbeat = heartbeat_payload()
        emitter = AgentEventEmitter("econrg-lab")

        snapshot = emitter.snapshot(heartbeat)
        first_events = emitter.changes_and_telemetry(heartbeat)
        second_events = emitter.changes_and_telemetry(heartbeat)

        self.assertEqual(snapshot.type.value, "node.snapshot")
        self.assertEqual([event.type.value for event in first_events], ["node.telemetry"])
        self.assertEqual([event.type.value for event in second_events], ["node.telemetry"])

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

        self.assertEqual([event.type.value for event in changed], ["node.telemetry", "container.changed"])
        self.assertEqual([event.type.value for event in unchanged], ["node.telemetry"])
