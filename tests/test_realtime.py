from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import unittest
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from agent.client.realtime import CommandExecutionRegistry, RealtimeState, run_realtime, websocket_endpoint
from agent.config import Settings
from agent.models import AgentEventType, CommandAckPayload, CommandType, HeartbeatPayload, HostTelemetry, make_agent_event, parse_dashboard_event


class RealtimeTransportTests(unittest.TestCase):
    def test_derives_fixed_websocket_path_without_url_credentials(self) -> None:
        self.assertEqual(
            websocket_endpoint("https://dashboard.example/base/?ignored=yes"),
            "wss://dashboard.example/base/api/v2/node-agent/ws",
        )

    def test_http_dashboard_uses_unencrypted_websocket_only_for_local_development(self) -> None:
        self.assertEqual(websocket_endpoint("http://127.0.0.1:8000"), "ws://127.0.0.1:8000/api/v2/node-agent/ws")

    def test_duplicate_command_id_is_claimed_once_and_completed_frames_are_replayed(self) -> None:
        command_id = uuid4()
        registry = CommandExecutionRegistry(max_completed=1)
        self.assertTrue(registry.claim(command_id))
        self.assertFalse(registry.claim(command_id))
        event = make_agent_event(
            AgentEventType.COMMAND_ACK,
            "econrg-lab",
            CommandAckPayload(command_id=command_id, status="ACKNOWLEDGED"),
        )
        registry.complete(command_id, [event])
        self.assertFalse(registry.claim(command_id))
        self.assertEqual(registry.replay(command_id), (event,))

    def test_command_execute_accepts_only_typed_allowlisted_payloads(self) -> None:
        command_id = uuid4()
        valid = {
            "protocol_version": 2,
            "message_id": str(uuid4()),
            "type": "command.execute",
            "node_id": "econrg-lab",
            "sent_at": "2026-08-29T12:00:00+00:00",
            "correlation_id": None,
            "payload": {"command_id": str(command_id), "command_type": "RECREATE", "app_id": "atlant"},
        }
        _envelope, payload = parse_dashboard_event(valid)
        self.assertEqual(payload.command_type.value, "RECREATE")
        valid["payload"] = {"command_id": str(command_id), "command_type": "SHELL", "app_id": "atlant"}
        with self.assertRaises(ValueError):
            parse_dashboard_event(valid)


class RealtimeCommandFlowTests(unittest.IsolatedAsyncioTestCase):
    def settings(self) -> Settings:
        return Settings(
            node_id="econrg-lab",
            node_name="econrg-lab",
            dashboard_url="https://dashboard.example",
            node_token="test-token",
            telemetry_interval_seconds=0.5,
            full_snapshot_interval_seconds=5,
            control_helper_path=Path("/fixed/control-helper.py"),
            command_timeout_seconds=5,
            ws_reconnect_initial_seconds=0.1,
            ws_reconnect_max_seconds=1,
        )

    def heartbeat(self) -> HeartbeatPayload:
        return HeartbeatPayload(
            node_id="econrg-lab",
            agent_version="test",
            sent_at=datetime.now(timezone.utc),
            host=HostTelemetry(
                hostname="econrg-lab",
                cpu_percent=1,
                memory_used_mb=1,
                memory_total_mb=2,
                disk_used_gb=1,
                disk_total_gb=2,
                uptime_seconds=1,
                load_average=[0, 0, 0],
            ),
        )

    async def test_command_emits_ack_running_and_success_from_the_fixed_helper(self) -> None:
        stop_event = asyncio.Event()
        command_id = uuid4()
        control_frame = json.dumps(
            {
                "protocol_version": 2,
                "message_id": str(uuid4()),
                "type": "command.execute",
                "node_id": "econrg-lab",
                "sent_at": "2026-08-29T12:00:00+00:00",
                "correlation_id": None,
                "payload": {"command_id": str(command_id), "command_type": CommandType.RECREATE.value, "app_id": "atlant"},
            }
        )

        class Process:
            returncode = 0

            async def wait(self) -> int:
                return 0

            def kill(self) -> None:
                self.returncode = -9

        class Socket:
            def __init__(self) -> None:
                self.messages: list[dict] = []
                self.incoming = asyncio.Queue()
                self.incoming.put_nowait(control_frame)

            async def send(self, raw: str) -> None:
                message = json.loads(raw)
                self.messages.append(message)
                if message["type"] == "command.result" and message["payload"]["status"] == "SUCCESS":
                    stop_event.set()

            async def recv(self) -> str:
                return await self.incoming.get()

        class Connection:
            def __init__(self, socket: Socket) -> None:
                self.socket = socket

            async def __aenter__(self) -> Socket:
                return self.socket

            async def __aexit__(self, *_args) -> None:
                return None

        socket = Socket()
        settings = self.settings()
        heartbeat = self.heartbeat()
        with (
            patch("agent.client.realtime.connect", return_value=Connection(socket)),
            patch("agent.client.realtime.asyncio.create_subprocess_exec", AsyncMock(return_value=Process())) as spawn,
        ):
            await asyncio.wait_for(run_realtime(settings, RealtimeState(), stop_event, lambda _settings: heartbeat), timeout=5)

        lifecycle = [(message["type"], message["payload"].get("status")) for message in socket.messages if message["type"].startswith("command.")]
        self.assertEqual(lifecycle, [("command.ack", "ACKNOWLEDGED"), ("command.result", "RUNNING"), ("command.result", "SUCCESS")])
        self.assertEqual(spawn.call_args.args[:5], ("sudo", "-n", str(settings.control_helper_path), "recreate", "atlant"))

    async def test_live_logs_are_streamed_only_from_the_fixed_helper(self) -> None:
        stop_event = asyncio.Event()
        request_id = uuid4()
        control_frame = json.dumps(
            {
                "protocol_version": 2,
                "message_id": str(uuid4()),
                "type": "logs.start",
                "node_id": "econrg-lab",
                "sent_at": "2026-08-29T12:00:00+00:00",
                "correlation_id": None,
                "payload": {"request_id": str(request_id), "app_id": "atlant", "container": "atlant-web-1", "tail_lines": 50},
            }
        )

        class Stream:
            def __init__(self) -> None:
                self._lines = iter((b"first line\n", b"second line\n"))

            def __aiter__(self):
                return self

            async def __anext__(self) -> bytes:
                try:
                    return next(self._lines)
                except StopIteration:
                    raise StopAsyncIteration from None

        class Process:
            returncode = 0
            stdout = Stream()

            async def wait(self) -> int:
                return 0

            def terminate(self) -> None:
                self.returncode = -15

        class Socket:
            def __init__(self) -> None:
                self.messages: list[dict] = []
                self.incoming = asyncio.Queue()
                self.incoming.put_nowait(control_frame)

            async def send(self, raw: str) -> None:
                message = json.loads(raw)
                self.messages.append(message)
                if message["type"] == "logs.ended":
                    stop_event.set()

            async def recv(self) -> str:
                return await self.incoming.get()

        class Connection:
            def __init__(self, socket: Socket) -> None:
                self.socket = socket

            async def __aenter__(self) -> Socket:
                return self.socket

            async def __aexit__(self, *_args) -> None:
                return None

        socket = Socket()
        settings = self.settings()
        with (
            patch("agent.client.realtime.connect", return_value=Connection(socket)),
            patch("agent.client.realtime.asyncio.create_subprocess_exec", AsyncMock(return_value=Process())) as spawn,
        ):
            await asyncio.wait_for(run_realtime(settings, RealtimeState(), stop_event, lambda _settings: self.heartbeat()), timeout=5)

        chunks = [message["payload"]["text"] for message in socket.messages if message["type"] == "logs.chunk"]
        ended = [message for message in socket.messages if message["type"] == "logs.ended"]
        self.assertEqual(chunks, ["first line", "second line"])
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0]["payload"], {"request_id": str(request_id), "reason": "stopped"})
        self.assertEqual(
            spawn.call_args.args[:7],
            ("sudo", "-n", str(settings.control_helper_path), "logs", "atlant", "atlant-web-1", "50"),
        )

    async def test_reconnects_after_a_transport_failure_and_resends_snapshot(self) -> None:
        stop_event = asyncio.Event()

        class Socket:
            def __init__(self) -> None:
                self.messages: list[dict] = []

            async def send(self, raw: str) -> None:
                self.messages.append(json.loads(raw))
                if self.messages[-1]["type"] == "node.snapshot":
                    stop_event.set()

            async def recv(self) -> str:
                return await asyncio.Future()

        class Connection:
            def __init__(self, socket: Socket) -> None:
                self.socket = socket

            async def __aenter__(self) -> Socket:
                return self.socket

            async def __aexit__(self, *_args) -> None:
                return None

        socket = Socket()
        with (
            patch("agent.client.realtime.connect", side_effect=[OSError("temporary disconnect"), Connection(socket)]) as connect,
            patch("agent.client.realtime.random.uniform", return_value=0),
        ):
            await asyncio.wait_for(run_realtime(self.settings(), RealtimeState(), stop_event, lambda _settings: self.heartbeat()), timeout=5)

        self.assertEqual(connect.call_count, 2)
        self.assertEqual([message["type"] for message in socket.messages], ["node.snapshot"])
