from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from agent.config import DEFAULT_ENV_FILE, load_settings


class SettingsLoadingTests(unittest.TestCase):
    def test_protected_default_file_is_left_to_systemd_environment(self) -> None:
        with (
            patch("agent.config.Path.is_file", return_value=True),
            patch("agent.config.os.access", return_value=False),
            patch("agent.config.Settings", return_value=object()) as settings,
        ):
            load_settings()

        self.assertIsNone(settings.call_args.kwargs["_env_file"])

    def test_explicit_env_file_is_always_honored(self) -> None:
        requested_file = Path("/tmp/agent.env")
        with patch("agent.config.Settings", return_value=object()) as settings:
            load_settings(requested_file)

        self.assertEqual(settings.call_args.kwargs["_env_file"], requested_file)
