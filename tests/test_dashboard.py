from __future__ import annotations

import asyncio
import unittest

import httpx

from agent.client.dashboard import post_heartbeat
from tests.helpers import heartbeat_payload


class DashboardClientTests(unittest.TestCase):
    def test_serializes_contract_and_authorization_header(self) -> None:
        received: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            received["url"] = str(request.url)
            received["authorization"] = request.headers.get("authorization")
            received["body"] = request.json() if hasattr(request, "json") else request.content
            return httpx.Response(200, json={"ok": True})

        async def run() -> bool:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await post_heartbeat(client, "https://dashboard.example/", "secret-token", heartbeat_payload())

        self.assertTrue(asyncio.run(run()))
        self.assertEqual(received["url"], "https://dashboard.example/api/v1/node-agent/heartbeat")
        self.assertEqual(received["authorization"], "Bearer secret-token")
        body = received["body"]
        self.assertIn(b'"protocol_version":1', body)  # type: ignore[arg-type]
        self.assertIn(b'"node_id":"econrg-lab"', body)  # type: ignore[arg-type]
        self.assertIn(b'"RECREATE"', body)  # type: ignore[arg-type]

    def test_dashboard_unavailable_returns_false_without_crashing(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("network unavailable", request=request)

        async def run() -> bool:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await post_heartbeat(
                    client,
                    "https://dashboard.example",
                    "secret-token",
                    heartbeat_payload(),
                    max_attempts=1,
                )

        self.assertFalse(asyncio.run(run()))
