"""Host-level telemetry collector."""

from __future__ import annotations

import os
import platform
import socket
import subprocess
import time

import psutil

from agent.models import HostTelemetry


BYTES_PER_MIB = 1024 * 1024
BYTES_PER_GIB = 1024 * 1024 * 1024
DOCKER_VERSION_COMMAND = ("/usr/bin/docker", "version", "--format", "{{.Server.Version}}")


def collect_docker_version() -> tuple[bool, str | None]:
    """Read the local Docker version through one fixed, argument-free command."""

    try:
        result = subprocess.run(
            DOCKER_VERSION_COMMAND,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, None
    version = result.stdout.strip()
    return (result.returncode == 0 and bool(version), version or None)


def collect_host_telemetry() -> HostTelemetry:
    """Collect a non-invasive point-in-time view of the local host."""

    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    swap = psutil.swap_memory()
    network = psutil.net_io_counters()
    disk_io = psutil.disk_io_counters()
    docker_available, docker_version = collect_docker_version()
    try:
        load_average = [round(value, 2) for value in os.getloadavg()]
    except (AttributeError, OSError):
        # The production target is Linux, but retaining this fallback keeps
        # local development and tests portable.
        load_average = []

    return HostTelemetry(
        hostname=socket.gethostname(),
        cpu_percent=round(psutil.cpu_percent(interval=0.1), 2),
        logical_cpu_count=psutil.cpu_count(logical=True),
        physical_cpu_count=psutil.cpu_count(logical=False),
        memory_used_mb=round(memory.used / BYTES_PER_MIB, 2),
        memory_total_mb=round(memory.total / BYTES_PER_MIB, 2),
        memory_available_mb=round(memory.available / BYTES_PER_MIB, 2),
        disk_used_gb=round(disk.used / BYTES_PER_GIB, 2),
        disk_total_gb=round(disk.total / BYTES_PER_GIB, 2),
        swap_used_mb=round(swap.used / BYTES_PER_MIB, 2),
        swap_total_mb=round(swap.total / BYTES_PER_MIB, 2),
        network_rx_bytes_total=network.bytes_recv if network else None,
        network_tx_bytes_total=network.bytes_sent if network else None,
        disk_read_bytes_total=disk_io.read_bytes if disk_io else None,
        disk_write_bytes_total=disk_io.write_bytes if disk_io else None,
        os_name=platform.system() or None,
        os_version=platform.version() or None,
        kernel_version=platform.release() or None,
        docker_available=docker_available,
        docker_version=docker_version,
        uptime_seconds=max(0, int(time.time() - psutil.boot_time())),
        load_average=load_average,
    )
