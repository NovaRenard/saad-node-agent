"""Persistent authenticated WebSocket transport for current node telemetry."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections import OrderedDict, deque
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import connect

from agent.config import Settings
from agent.events import AgentEventEmitter
from agent.models import (
    AgentEventType,
    CommandAckPayload,
    CommandExecutePayload,
    CommandResultPayload,
    DashboardEventType,
    HeartbeatPayload,
    ProtocolErrorPayload,
    LogsChunkPayload,
    LogsEndedPayload,
    LogsStartPayload,
    LogsStopPayload,
    ProtocolEnvelope,
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


class CommandExecutionRegistry:
    """Bounded process-local idempotency state for dashboard command frames."""

    def __init__(self, max_completed: int = 256) -> None:
        self._inflight: set[UUID] = set()
        self._completed: OrderedDict[UUID, tuple[ProtocolEnvelope, ...]] = OrderedDict()
        self._max_completed = max_completed

    def claim(self, command_id: UUID) -> bool:
        if command_id in self._inflight or command_id in self._completed:
            return False
        self._inflight.add(command_id)
        return True

    def replay(self, command_id: UUID) -> tuple[ProtocolEnvelope, ...] | None:
        return self._completed.get(command_id)

    def complete(self, command_id: UUID, events: list[ProtocolEnvelope]) -> None:
        self._inflight.discard(command_id)
        self._completed[command_id] = tuple(events)
        self._completed.move_to_end(command_id)
        while len(self._completed) > self._max_completed:
            self._completed.popitem(last=False)


async def run_realtime(
    settings: Settings,
    state: RealtimeState,
    stop_event: asyncio.Event,
    collect_payload: Callable[[Settings], HeartbeatPayload],
) -> None:
    """Reconnect with bounded exponential backoff and decorrelated jitter."""

    delay = settings.ws_reconnect_initial_seconds
    endpoint = websocket_endpoint(str(settings.dashboard_url))
    command_registry = CommandExecutionRegistry()
    command_tasks: dict[UUID, asyncio.Task[None]] = {}
    # Keep the head until it has crossed the current socket.  A queue that
    # removes and re-adds a frame on send failure can reorder ACK/RUNNING/final
    # lifecycle messages across a reconnect.
    command_outbox: deque[ProtocolEnvelope] = deque()

    async def execute_command(payload: CommandExecutePayload, correlation_id: UUID) -> None:
        """Run the fixed helper independently of any individual socket."""

        started_at = datetime.now(timezone.utc)
        emitted: list[ProtocolEnvelope] = []
        process: asyncio.subprocess.Process | None = None

        async def emit(event_type: AgentEventType, command_payload) -> None:
            message = make_agent_event(event_type, settings.node_id, command_payload, correlation_id=correlation_id)
            emitted.append(message)
            command_outbox.append(message)

        try:
            await emit(AgentEventType.COMMAND_ACK, CommandAckPayload(command_id=payload.command_id, status="ACKNOWLEDGED"))
            await emit(AgentEventType.COMMAND_RESULT, CommandResultPayload(command_id=payload.command_id, status="RUNNING", started_at=started_at))
            helper_operation = {
                "DEPLOY": "deploy",
                "RESTART": "restart",
                "ROLLBACK": "rollback",
            }[payload.command_type.value]
            process = await asyncio.create_subprocess_exec(
                "sudo", "-n", str(settings.control_helper_path), helper_operation, payload.app_id,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                exit_code = await asyncio.wait_for(process.wait(), timeout=settings.command_timeout_seconds)
            except TimeoutError:
                process.kill()
                await process.wait()
                await emit(AgentEventType.COMMAND_RESULT, CommandResultPayload(command_id=payload.command_id, status="FAILED", started_at=started_at, finished_at=datetime.now(timezone.utc), error="Operation timed out."))
            else:
                if exit_code == 0:
                    await emit(AgentEventType.COMMAND_RESULT, CommandResultPayload(command_id=payload.command_id, status="SUCCESS", started_at=started_at, finished_at=datetime.now(timezone.utc), result_summary="Operation completed."))
                else:
                    await emit(AgentEventType.COMMAND_RESULT, CommandResultPayload(command_id=payload.command_id, status="FAILED", started_at=started_at, finished_at=datetime.now(timezone.utc), error=f"Operation failed (exit code {exit_code})."))
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            await emit(AgentEventType.COMMAND_RESULT, CommandResultPayload(command_id=payload.command_id, status="FAILED", started_at=started_at, finished_at=datetime.now(timezone.utc), error="Agent stopped before the operation completed."))
        except OSError:
            await emit(AgentEventType.COMMAND_RESULT, CommandResultPayload(command_id=payload.command_id, status="FAILED", started_at=started_at, finished_at=datetime.now(timezone.utc), error="Unable to start the fixed control helper."))
        finally:
            command_registry.complete(payload.command_id, emitted)

    while not stop_event.is_set():
        log_tasks: dict[UUID, asyncio.Task[None]] = {}
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

                async def send_event(event) -> None:
                    await websocket.send(event.model_dump_json())

                async def flush_command_outbox() -> None:
                    while command_outbox:
                        message = command_outbox[0]
                        # The frame stays at the head if this send breaks the
                        # socket, so the next connection preserves lifecycle
                        # order instead of delivering a result before its ACK.
                        await send_event(message)
                        command_outbox.popleft()

                async def stream_logs(payload: LogsStartPayload, correlation_id) -> None:
                    reason = "stopped"
                    process: asyncio.subprocess.Process | None = None
                    try:
                        process = await asyncio.create_subprocess_exec(
                            "sudo", "-n", str(settings.control_helper_path), "logs", payload.app_id, payload.container, str(payload.tail_lines),
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                        )
                        assert process.stdout is not None
                        async for raw_line in process.stdout:
                            text = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                            if text:
                                await send_event(make_agent_event(AgentEventType.LOGS_CHUNK, settings.node_id, LogsChunkPayload(request_id=payload.request_id, text=text), correlation_id=correlation_id))
                        await process.wait()
                        if process.returncode not in {0, None}:
                            reason = "failed"
                    except asyncio.CancelledError:
                        reason = "stopped"
                        raise
                    except OSError:
                        reason = "failed"
                    finally:
                        if process is not None:
                            if process.returncode is None:
                                process.terminate()
                            await process.wait()
                        await send_event(make_agent_event(AgentEventType.LOGS_ENDED, settings.node_id, LogsEndedPayload(request_id=payload.request_id, reason=reason), correlation_id=correlation_id))

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
                    await flush_command_outbox()

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
                                if _control.request_id not in log_tasks and len(log_tasks) >= 3:
                                    await send_event(make_agent_event(AgentEventType.LOGS_ENDED, settings.node_id, LogsEndedPayload(request_id=_control.request_id, reason="limit_reached"), correlation_id=envelope.message_id))
                                elif _control.request_id not in log_tasks:
                                    task = asyncio.create_task(stream_logs(_control, envelope.message_id))
                                    log_tasks[_control.request_id] = task
                                    task.add_done_callback(lambda _task, request_id=_control.request_id: log_tasks.pop(request_id, None))
                            elif envelope.type == DashboardEventType.LOGS_STOP:
                                assert isinstance(_control, LogsStopPayload)
                                task = log_tasks.pop(_control.request_id, None)
                                if task:
                                    task.cancel()
                            elif envelope.type == DashboardEventType.COMMAND_EXECUTE:
                                assert isinstance(_control, CommandExecutePayload)
                                replay = command_registry.replay(_control.command_id)
                                if replay is not None:
                                    for message in replay:
                                        await send_event(message)
                                elif command_registry.claim(_control.command_id):
                                    task = asyncio.create_task(execute_command(_control, envelope.message_id))
                                    command_tasks[_control.command_id] = task
                                    task.add_done_callback(lambda _task, command_id=_control.command_id: command_tasks.pop(command_id, None))
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
            active_log_tasks = tuple(log_tasks.values())
            for task in active_log_tasks:
                task.cancel()
            if active_log_tasks:
                await asyncio.gather(*active_log_tasks, return_exceptions=True)
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

    active_command_tasks = tuple(command_tasks.values())
    for task in active_command_tasks:
        task.cancel()
    if active_command_tasks:
        await asyncio.gather(*active_command_tasks, return_exceptions=True)
