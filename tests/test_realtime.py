from __future__ import annotations

import unittest

from agent.client.realtime import websocket_endpoint


class RealtimeTransportTests(unittest.TestCase):
    def test_derives_fixed_websocket_path_without_url_credentials(self) -> None:
        self.assertEqual(
            websocket_endpoint("https://dashboard.example/base/?ignored=yes"),
            "wss://dashboard.example/base/api/v2/node-agent/ws",
        )

    def test_http_dashboard_uses_unencrypted_websocket_only_for_local_development(self) -> None:
        self.assertEqual(websocket_endpoint("http://127.0.0.1:8000"), "ws://127.0.0.1:8000/api/v2/node-agent/ws")
