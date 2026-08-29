from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agent.collectors.saad_deploy import InstanceConfig
from agent.config import Settings
from agent.main import collect_payload, run_forever
from agent.models import DeploymentTelemetry, HostTelemetry


class MainCollectorTests(unittest.TestCase):
    def test_one_broken_instance_does_not_prevent_other_instances(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings = Settings(
                node_id="econrg-lab",
                node_name="econrg-lab",
                dashboard_url="https://dashboard.example",
                node_token="test-token",
                saad_deploy_config_dir=Path(temporary_directory),
                snapshot_use_sudo=False,
            )
            instances = [InstanceConfig(app_id="broken"), InstanceConfig(app_id="healthy")]
            host = HostTelemetry(
                hostname="econrg-lab",
                cpu_percent=1,
                memory_used_mb=1,
                memory_total_mb=2,
                disk_used_gb=1,
                disk_total_gb=2,
                uptime_seconds=1,
                load_average=[0, 0, 0],
            )
            with (
                patch("agent.main.collect_host_telemetry", return_value=host),
                patch("agent.main.collect_docker_snapshot", return_value=None),
                patch("agent.main.discover_instances_from_helper", return_value=instances),
                patch(
                    "agent.main.collect_deployment_state",
                    side_effect=[RuntimeError("bad state"), DeploymentTelemetry(status="healthy")],
                ),
            ):
                payload = collect_payload(settings)

        self.assertEqual([instance.app_id for instance in payload.instances], ["broken", "healthy"])
        self.assertEqual(payload.instances[0].deployment.status, "unknown")
        self.assertEqual(payload.instances[1].deployment.status, "healthy")


class TransportPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_healthy_realtime_connection_suppresses_http_fallback_heartbeats(self) -> None:
        settings = Settings(
            node_id="econrg-lab",
            node_name="econrg-lab",
            dashboard_url="https://dashboard.example",
            node_token="test-token",
            full_snapshot_interval_seconds=5,
        )
        stop_event = asyncio.Event()

        async def healthy_realtime(_settings, state, stop, _collector) -> None:
            state.connected.set()
            await stop.wait()

        with (
            patch("agent.main.run_realtime", healthy_realtime),
            patch("agent.main.post_heartbeat", AsyncMock()) as post_heartbeat,
        ):
            task = asyncio.create_task(run_forever(settings, stop_event))
            await asyncio.sleep(0.05)
            stop_event.set()
            await task

        post_heartbeat.assert_not_awaited()
