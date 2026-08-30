from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent.collectors.host import collect_host_telemetry


class HostTelemetryV2Tests(unittest.TestCase):
    def test_collects_cumulative_host_telemetry_and_platform_metadata(self) -> None:
        with (
            patch("agent.collectors.host.socket.gethostname", return_value="econrg-lab"),
            patch("agent.collectors.host.psutil.cpu_percent", return_value=12.5),
            patch("agent.collectors.host.psutil.cpu_count", side_effect=lambda logical: 8 if logical else 4),
            patch("agent.collectors.host.psutil.virtual_memory", return_value=SimpleNamespace(used=512 * 1024**2, total=1024 * 1024**2, available=256 * 1024**2)),
            patch("agent.collectors.host.psutil.disk_usage", return_value=SimpleNamespace(used=10 * 1024**3, total=20 * 1024**3)),
            patch("agent.collectors.host.psutil.swap_memory", return_value=SimpleNamespace(used=128 * 1024**2, total=512 * 1024**2)),
            patch("agent.collectors.host.psutil.net_io_counters", return_value=SimpleNamespace(bytes_recv=101, bytes_sent=202)),
            patch("agent.collectors.host.psutil.disk_io_counters", return_value=SimpleNamespace(read_bytes=303, write_bytes=404)),
            patch("agent.collectors.host.psutil.boot_time", return_value=0),
            patch("agent.collectors.host.time.time", return_value=60),
            patch("agent.collectors.host.os.getloadavg", return_value=(0.1, 0.2, 0.3), create=True),
            patch("agent.collectors.host.platform.system", return_value="Linux"),
            patch("agent.collectors.host.platform.version", return_value="6.8.0"),
            patch("agent.collectors.host.platform.release", return_value="6.8.0-test"),
            patch("agent.collectors.host.collect_docker_version", return_value=(True, "27.0.1")),
        ):
            telemetry = collect_host_telemetry()

        self.assertEqual(telemetry.logical_cpu_count, 8)
        self.assertEqual(telemetry.physical_cpu_count, 4)
        self.assertEqual(telemetry.memory_available_mb, 256.0)
        self.assertEqual(telemetry.swap_used_mb, 128.0)
        self.assertEqual((telemetry.network_rx_bytes_total, telemetry.network_tx_bytes_total), (101, 202))
        self.assertEqual((telemetry.disk_read_bytes_total, telemetry.disk_write_bytes_total), (303, 404))
        self.assertEqual((telemetry.os_name, telemetry.kernel_version, telemetry.docker_version), ("Linux", "6.8.0-test", "27.0.1"))

    def test_docker_version_query_is_fixed_and_safe(self) -> None:
        from agent.collectors.host import collect_docker_version

        with patch("agent.collectors.host.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="27.0.1\n")) as run:
            available, version = collect_docker_version()

        self.assertTrue(available)
        self.assertEqual(version, "27.0.1")
        self.assertEqual(run.call_args.args[0], ("/usr/bin/docker", "version", "--format", "{{.Server.Version}}"))
        self.assertFalse(run.call_args.kwargs["shell"] if "shell" in run.call_args.kwargs else False)
