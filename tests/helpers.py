from __future__ import annotations

from datetime import datetime, timezone

from agent.models import HeartbeatPayload, HostTelemetry


def heartbeat_payload() -> HeartbeatPayload:
    return HeartbeatPayload(
        node_id="econrg-lab",
        agent_version="0.1.0",
        sent_at=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
        host=HostTelemetry(
            hostname="econrg-lab",
            cpu_percent=14.2,
            memory_used_mb=6210,
            memory_total_mb=16384,
            disk_used_gb=72.1,
            disk_total_gb=200,
            uptime_seconds=924821,
            load_average=[0.4, 0.5, 0.6],
        ),
    )
