"""Host-level telemetry collector."""

from __future__ import annotations

import os
import socket
import time

import psutil

from agent.models import HostTelemetry


BYTES_PER_MIB = 1024 * 1024
BYTES_PER_GIB = 1024 * 1024 * 1024


def collect_host_telemetry() -> HostTelemetry:
    """Collect a non-invasive point-in-time view of the local host."""

    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    try:
        load_average = [round(value, 2) for value in os.getloadavg()]
    except (AttributeError, OSError):
        # The production target is Linux, but retaining this fallback keeps
        # local development and tests portable.
        load_average = []

    return HostTelemetry(
        hostname=socket.gethostname(),
        cpu_percent=round(psutil.cpu_percent(interval=0.1), 2),
        memory_used_mb=round(memory.used / BYTES_PER_MIB, 2),
        memory_total_mb=round(memory.total / BYTES_PER_MIB, 2),
        disk_used_gb=round(disk.used / BYTES_PER_GIB, 2),
        disk_total_gb=round(disk.total / BYTES_PER_GIB, 2),
        uptime_seconds=max(0, int(time.time() - psutil.boot_time())),
        load_average=load_average,
    )
