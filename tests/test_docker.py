from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from agent.collectors.docker import aggregate_resources, collect_docker_snapshot, containers_for_project


SNAPSHOT = {
    "inspect": [
        {
            "Id": "a" * 64,
            "Name": "/atlant-backend-1",
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "atlant-crm",
                    "com.docker.compose.service": "backend",
                }
            },
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
            "RestartCount": 2,
            "HostConfig": {"Memory": 0},
        },
        {
            "Id": "b" * 64,
            "Name": "/atlant-worker-1",
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "atlant-crm",
                    "com.docker.compose.service": "worker",
                }
            },
            "State": {"Status": "exited"},
            "RestartCount": 0,
            "HostConfig": {"Memory": 536870912},
        },
        {
            "Id": "c" * 64,
            "Name": "/econrg-backend-1",
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "econrg-crm",
                    "com.docker.compose.service": "backend",
                }
            },
            "State": {"Status": "running", "Health": {"Status": "unhealthy"}},
            "RestartCount": 0,
            "HostConfig": {"Memory": 0},
        },
    ],
    "stats": [
        {"ID": "a" * 12, "CPUPerc": "1.50%", "MemUsage": "240MiB / 1GiB"},
        {"ID": "b" * 12, "CPUPerc": "0.50%", "MemUsage": "12MiB / 512MiB"},
        {"ID": "c" * 12, "CPUPerc": "4.00%", "MemUsage": "310MiB / 0B"},
    ],
}


class DockerCollectorTests(unittest.TestCase):
    def test_matches_compose_labels_and_aggregates_one_instance(self) -> None:
        containers = containers_for_project(SNAPSHOT, "atlant-crm")
        resources = aggregate_resources(containers)

        self.assertEqual([container.service for container in containers], ["backend", "worker"])
        self.assertEqual(resources.cpu_percent, 2.0)
        self.assertEqual(resources.memory_used_mb, 252.0)
        self.assertEqual(resources.containers_total, 2)
        self.assertEqual(resources.containers_running, 1)
        self.assertEqual(resources.containers_healthy, 1)
        self.assertEqual(containers[0].memory_limit_mb, 1024.0)

    def test_snapshot_uses_only_the_fixed_helper(self) -> None:
        completed = Mock(stdout='{"inspect": [], "stats": []}')
        with patch("agent.collectors.docker.subprocess.run", return_value=completed) as run:
            snapshot = collect_docker_snapshot("/fixed/snapshot.sh", use_sudo=False)

        self.assertEqual(snapshot, {"inspect": [], "stats": []})
        self.assertEqual(run.call_args.args[0], ["/fixed/snapshot.sh"])
        self.assertTrue(run.call_args.kwargs["check"])
