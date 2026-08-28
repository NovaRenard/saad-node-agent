"""Process entry point and heartbeat orchestration."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import ValidationError

from agent import __version__
from agent.client.dashboard import post_heartbeat
from agent.collectors.docker import aggregate_resources, collect_docker_snapshot, containers_for_project
from agent.collectors.host import collect_host_telemetry
from agent.collectors.saad_deploy import collect_deployment_state, discover_instances
from agent.config import Settings, load_settings
from agent.models import DeploymentTelemetry, HeartbeatPayload, InstanceResources, InstanceTelemetry

logger = logging.getLogger(__name__)


def collect_payload(settings: Settings) -> HeartbeatPayload:
    """Collect one best-effort heartbeat. A bad instance never discards its peers."""

    host = collect_host_telemetry()
    snapshot = collect_docker_snapshot(
        settings.snapshot_helper_path,
        use_sudo=settings.snapshot_use_sudo,
        timeout=settings.http_timeout_seconds + 10,
    )
    instances: list[InstanceTelemetry] = []
    for instance in discover_instances(settings.saad_deploy_config_dir):
        try:
            deployment = collect_deployment_state(instance)
        except Exception:  # State data is untrusted and must not break telemetry.
            logger.exception("Could not collect deployment state for %s", instance.app_id)
            deployment = DeploymentTelemetry()

        try:
            containers = containers_for_project(snapshot, instance.compose_project_name) if snapshot else []
            resources = aggregate_resources(containers)
        except Exception:  # An individual unexpected Docker record remains isolated.
            logger.exception("Could not collect Docker telemetry for %s", instance.app_id)
            containers = []
            resources = InstanceResources()

        instances.append(
            InstanceTelemetry(
                app_id=instance.app_id,
                repository=instance.repository,
                branch=instance.branch,
                compose_project_name=instance.compose_project_name,
                deployment=deployment,
                resources=resources,
                containers=containers,
            )
        )

    return HeartbeatPayload(
        node_id=settings.node_id,
        agent_version=__version__,
        sent_at=datetime.now(timezone.utc),
        host=host,
        instances=instances,
    )


async def run_forever(settings: Settings, stop_event: asyncio.Event | None = None) -> None:
    """Run heartbeats until the service receives SIGTERM or SIGINT."""

    stop_event = stop_event or asyncio.Event()
    timeout = httpx.Timeout(settings.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        while not stop_event.is_set():
            started_at = time.monotonic()
            try:
                payload = await asyncio.to_thread(collect_payload, settings)
                delivered = await post_heartbeat(
                    client,
                    str(settings.dashboard_url),
                    settings.node_token,
                    payload,
                    max_attempts=settings.heartbeat_max_attempts,
                )
                if delivered:
                    logger.info("Heartbeat sent: node=%s instances=%s", settings.node_id, len(payload.instances))
            except asyncio.CancelledError:
                raise
            except Exception:
                # Keep the systemd process alive for recoverable operating
                # failures; unexpected details are retained in journald.
                logger.exception("Unhandled error while preparing heartbeat")

            remaining = max(0.0, settings.heartbeat_interval_seconds - (time.monotonic() - started_at))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=remaining)
            except TimeoutError:
                pass


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(signum, stop_event.set)
        except (NotImplementedError, RuntimeError):
            # add_signal_handler is unavailable on Windows, which is only a
            # development platform for this Linux-targeted agent.
            signal.signal(signum, lambda _signal, _frame: stop_event.set())


async def _run(settings: Settings) -> None:
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)
    await run_forever(settings, stop_event)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the saad telemetry-only node agent")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Path to an env file (default: /etc/saad-node-agent/agent.env)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings(args.env_file)
    except ValidationError as exc:
        logger.error("Invalid agent configuration: %s", exc)
        return 2
    asyncio.run(_run(settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
