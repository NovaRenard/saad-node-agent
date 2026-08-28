"""Docker telemetry parsed from the restricted snapshot helper's JSON output."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agent.models import ContainerTelemetry, InstanceResources

logger = logging.getLogger(__name__)
_MEMORY_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGTPE]?i?B|B)\s*$", re.IGNORECASE)
_MEMORY_UNITS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
    "PIB": 1024**5,
    "EIB": 1024**6,
}


def _percent(value: object) -> float:
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return 0.0


def _memory_mb(value: object) -> float | None:
    if value is None:
        return None
    match = _MEMORY_PATTERN.match(str(value))
    if not match:
        return None
    amount = float(match.group(1))
    bytes_count = amount * _MEMORY_UNITS[match.group(2).upper()]
    return round(bytes_count / (1024 * 1024), 2)


def _find_stat(container_id: str, stats: Iterable[dict[str, Any]]) -> dict[str, Any]:
    for stat in stats:
        stat_id = str(stat.get("ID") or stat.get("Container") or "")
        if stat_id and (container_id.startswith(stat_id) or stat_id.startswith(container_id)):
            return stat
    return {}


def _parse_memory_usage(stat: dict[str, Any]) -> tuple[float, float | None]:
    usage = str(stat.get("MemUsage") or stat.get("MemUsage / Limit") or "")
    used, separator, limit = usage.partition("/")
    used_mb = _memory_mb(used) or 0.0
    limit_mb = _memory_mb(limit) if separator else None
    return used_mb, limit_mb if limit_mb and limit_mb > 0 else None


def parse_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize the helper protocol into inspect records decorated with stats."""

    inspect_records = snapshot.get("inspect", [])
    stats = snapshot.get("stats", [])
    if not isinstance(inspect_records, list) or not isinstance(stats, list):
        raise ValueError("snapshot inspect and stats values must be lists")
    return [record for record in inspect_records if isinstance(record, dict)]


def containers_for_project(snapshot: dict[str, Any], compose_project_name: str | None) -> list[ContainerTelemetry]:
    """Select containers by official Compose labels, never by their names."""

    if not compose_project_name:
        return []
    records = parse_snapshot(snapshot)
    raw_stats = snapshot.get("stats", [])
    stats = [stat for stat in raw_stats if isinstance(stat, dict)] if isinstance(raw_stats, list) else []
    containers: list[ContainerTelemetry] = []
    for record in records:
        config = record.get("Config") if isinstance(record.get("Config"), dict) else {}
        labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
        if labels.get("com.docker.compose.project") != compose_project_name:
            continue
        container_id = str(record.get("Id") or "")
        stat = _find_stat(container_id, stats)
        memory_used_mb, memory_limit_mb = _parse_memory_usage(stat)
        host_config = record.get("HostConfig") if isinstance(record.get("HostConfig"), dict) else {}
        if memory_limit_mb is None:
            memory_limit_mb = _memory_mb(f"{host_config.get('Memory', 0)}B")
            if memory_limit_mb == 0:
                memory_limit_mb = None
        state = record.get("State") if isinstance(record.get("State"), dict) else {}
        health_data = state.get("Health") if isinstance(state.get("Health"), dict) else {}
        containers.append(
            ContainerTelemetry(
                name=str(record.get("Name") or stat.get("Name") or container_id).lstrip("/"),
                service=labels.get("com.docker.compose.service"),
                status=str(state.get("Status") or "unknown"),
                health=str(health_data.get("Status")) if health_data.get("Status") is not None else None,
                cpu_percent=_percent(stat.get("CPUPerc")),
                memory_used_mb=memory_used_mb,
                memory_limit_mb=memory_limit_mb,
                restart_count=int(record.get("RestartCount") or 0),
            )
        )
    return sorted(containers, key=lambda container: container.name)


def aggregate_resources(containers: list[ContainerTelemetry]) -> InstanceResources:
    return InstanceResources(
        cpu_percent=round(sum(container.cpu_percent for container in containers), 2),
        memory_used_mb=round(sum(container.memory_used_mb for container in containers), 2),
        containers_total=len(containers),
        containers_running=sum(container.status == "running" for container in containers),
        containers_healthy=sum(container.health == "healthy" for container in containers),
    )


def collect_docker_snapshot(helper_path: Path, *, use_sudo: bool = True, timeout: float = 20.0) -> dict[str, Any] | None:
    """Invoke only the fixed read-only helper and decode its JSON response."""

    command = [str(helper_path)]
    if use_sudo:
        command = ["sudo", "-n", *command]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        decoded = json.loads(result.stdout)
        if not isinstance(decoded, dict):
            raise ValueError("snapshot root must be an object")
        # Validate the protocol before retaining it for the rest of this heartbeat.
        parse_snapshot(decoded)
        return decoded
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Docker snapshot unavailable: %s", exc)
        return None
