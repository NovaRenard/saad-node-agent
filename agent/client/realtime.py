"""Persistent authenticated WebSocket transport for current node telemetry."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import connect

from agent.config import Settings
from agent.models import HeartbeatPayload

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
                while not stop_event.is_set():
                    started_at = asyncio.get_running_loop().time()
                    payload = await asyncio.to_thread(collect_payload, settings)
                    await websocket.send(payload.model_dump_json())
                    # Stage 1 only expects acknowledgement frames. Stage 2
                    # adds a typed receive loop for dashboard-to-agent events.
                    try:
                        await asyncio.wait_for(websocket.recv(), timeout=0.5)
                    except TimeoutError:
                        pass
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
