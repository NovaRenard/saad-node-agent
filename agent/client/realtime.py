"""Persistent authenticated WebSocket transport for current node telemetry."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import connect

from agent.config import Settings
from agent.events import AgentEventEmitter
from agent.models import (
    AgentEventType,
    DashboardEventType,
    HeartbeatPayload,
    ProtocolErrorPayload,
    LogsChunkPayload,
    LogsEndedPayload,
    LogsStartPayload,
    LogsStopPayload,
    make_agent_event,
    parse_dashboard_event,
)

logger = logging.getLogger(__name__)


def websocket_endpoint(dashboard_url: str) -> str:
    """Derive the fixed v2 endpoint without putting tokens in the URL."""

    parsed = urlsplit(dashboard_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("DASHBOARD_URL must use http or https")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    base_path = parsed.path.rstrip("/")
    return urlunsplit((scheme, parsed.netloc, f"{base_path}/api/v2/node-agent/ws", "", ""))


class RealtimeState:
    """Shared connection health observed by the HTTP fallback loop."""

    def __init__(self) -> None:
        self.connected = asyncio.Event()

    @property
    def is_healthy(self) -> bool:
        return self.connected.is_set()


async def run_realtime(
    settings: Settings,
    state: RealtimeState,
    stop_event: asyncio.Event,
    collect_payload: Callable[[Settings], HeartbeatPayload],
) -> None:
    """Reconnect with bounded exponential backoff and decorrelated jitter."""

    delay = settings.ws_reconnect_initial_seconds
    endpoint = websocket_endpoint(str(settings.dashboard_url))
    while not stop_event.is_set():
        try:
            async with connect(
                endpoint,
                additional_headers={"Authorization": f"Bearer {settings.node_token}"},
                ping_interval=settings.ws_ping_interval_seconds,
                ping_timeout=settings.ws_ping_timeout_seconds,
                close_timeout=5,
            ) as websocket:
                state.connected.set()
                delay = settings.ws_reconnect_initial_seconds
                logger.info("Realtime transport connected: node=%s", settings.node_id)
                emitter = AgentEventEmitter(settings.node_id)
                last_snapshot_at = 0.0
                snapshot_requested = True
                log_tasks: dict[object, asyncio.Task] = {}

                async def stream_logs(payload: LogsStartPayload, correlation_id) -> None:
                    process = await asyncio.create_subprocess_exec(
                        "sudo", "-n", str(settings.control_helper_path), "logs", payload.app_id, payload.container, str(payload.tail_lines),
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                    )
                    assert process.stdout is not None
                    try:
                        async for raw_line in process.stdout:
                            text = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                            if text:
                                await websocket.send(make_agent_event(AgentEventType.LOGS_CHUNK, settings.node_id, LogsChunkPayload(request_id=payload.request_id, text=text), correlation_id=correlation_id).model_dump_json())
                    finally:
                        if process.returncode is None:
                            process.terminate()
                        await process.wait()
                        await websocket.send(make_agent_event(AgentEventType.LOGS_ENDED, settings.node_id, LogsEndedPayload(request_id=payload.request_id, reason="stopped"), correlation_id=correlation_id).model_dump_json())
                while not stop_event.is_set():
                    started_at = asyncio.get_running_loop().time()
                    payload = await asyncio.to_thread(collect_payload, settings)
                    if snapshot_requested or started_at - last_snapshot_at >= settings.full_snapshot_interval_seconds:
                        events = [emitter.snapshot(payload)]
                        last_snapshot_at = started_at
                        snapshot_requested = False
                    else:
                        events = emitter.changes_and_telemetry(payload)
                    for event in events:
                        await websocket.send(event.model_dump_json())

                    # Drain a short control window so a snapshot request is
                    # served promptly without a busy receive loop.
                    try:
                        raw_message = await asyncio.wait_for(websocket.recv(), timeout=min(0.25, settings.telemetry_interval_seconds))
                    except TimeoutError:
                        raw_message = None
                    if raw_message is not None:
                        try:
                            envelope, _control = parse_dashboard_event(json.loads(raw_message))
                            if envelope.type == DashboardEventType.SNAPSHOT_REQUEST:
                                snapshot_requested = True
                            elif envelope.type == DashboardEventType.LOGS_START:
                                assert isinstance(_control, LogsStartPayload)
                                if len(log_tasks) >= 3:
                                    raise ValueError("log stream limit reached")
                                log_tasks[_control.request_id] = asyncio.create_task(stream_logs(_control, envelope.message_id))
                            elif envelope.type == DashboardEventType.LOGS_STOP:
                                assert isinstance(_control, LogsStopPayload)
                                task = log_tasks.pop(_control.request_id, None)
                                if task:
                                    task.cancel()
                            else:
                                logger.warning("Dashboard event %s is not enabled yet", envelope.type.value)
                                await websocket.send(
                                    make_agent_event(
                                        AgentEventType.ERROR,
                                        settings.node_id,
                                        ProtocolErrorPayload(code="unsupported_event", message=f"{envelope.type.value} is not enabled"),
                                        correlation_id=envelope.message_id,
                                    ).model_dump_json()
                                )
                        except (ValueError, json.JSONDecodeError):
                            logger.warning("Ignoring invalid or unsupported dashboard WebSocket event")
                    elapsed = asyncio.get_running_loop().time() - started_at
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=max(0.0, settings.telemetry_interval_seconds - elapsed))
                    except TimeoutError:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # Network and TLS failures must not stop telemetry collection.
            logger.warning("Realtime transport unavailable: %s", exc)
        finally:
            state.connected.clear()

        if stop_event.is_set():
            break
        jitter = random.uniform(0.8, 1.2)
        sleep_for = min(settings.ws_reconnect_max_seconds, delay) * jitter
        logger.info("Realtime reconnect for node=%s in %.1fs", settings.node_id, sleep_for)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_for)
        except TimeoutError:
            pass
        delay = min(settings.ws_reconnect_max_seconds, delay * 2)
