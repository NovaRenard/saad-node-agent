"""Small resilient HTTP client for the dashboard heartbeat endpoint."""

from __future__ import annotations

import asyncio
import logging

import httpx

from agent.models import HeartbeatPayload

logger = logging.getLogger(__name__)


async def post_heartbeat(
    client: httpx.AsyncClient,
    dashboard_url: str,
    token: str,
    payload: HeartbeatPayload,
    *,
    max_attempts: int = 3,
) -> bool:
    """POST a heartbeat using bounded exponential backoff on transient failures."""

    endpoint = f"{dashboard_url.rstrip('/')}/api/v1/node-agent/heartbeat"
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.post(endpoint, headers=headers, json=payload.model_dump(mode="json"))
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or body.get("ok") is not True:
                raise ValueError("dashboard response did not contain {'ok': true}")
            return True
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if 400 <= status_code < 500:
                logger.error("Dashboard rejected heartbeat with HTTP %s; will retry next interval", status_code)
                return False
            error: Exception = exc
        except (httpx.HTTPError, ValueError) as exc:
            error = exc

        if attempt == max_attempts:
            logger.warning("Heartbeat failed after %s attempts: %s", attempt, error)
            return False
        delay = min(2 ** (attempt - 1), 8)
        logger.warning("Heartbeat attempt %s/%s failed: %s; retrying in %ss", attempt, max_attempts, error, delay)
        await asyncio.sleep(delay)
    return False
