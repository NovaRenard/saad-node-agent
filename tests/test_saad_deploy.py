from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from agent.collectors.saad_deploy import InstanceConfig, collect_deployment_state, discover_instances, parse_env_file


class SaadDeployCollectorTests(unittest.TestCase):
    def test_discovers_deploy_env_files_without_hardcoded_apps(self) -> None:
        content = """# a new app needs no code change
APP_ID=koshakan
GITHUB_REPOSITORY='NovaRenard/koshakan-crm'
DEPLOY_BRANCH=main
APP_DIR=/srv/koshakan
COMPOSE_FILE=/srv/koshakan/compose.yml
COMPOSE_PROJECT_NAME=koshakan-crm
STATE_DIR=/var/lib/saad-deploy/koshakan
"""
        koshakan_path = Path("/etc/saad-deploy/koshakan.env")
        broken_path = Path("/etc/saad-deploy/broken.env")
        with patch.object(Path, "read_text", return_value=content):
            parsed = parse_env_file(koshakan_path)
        self.assertEqual(parsed["GITHUB_REPOSITORY"], "NovaRenard/koshakan-crm")

        with (
            patch.object(Path, "glob", return_value=[koshakan_path, broken_path]),
            patch(
                "agent.collectors.saad_deploy.parse_env_file",
                side_effect=lambda path: parsed
                if path == koshakan_path
                else {"COMPOSE_PROJECT_NAME": "missing-app-id"},
            ),
        ):
            instances = discover_instances(Path("/etc/saad-deploy"))

        self.assertEqual([instance.app_id for instance in instances], ["koshakan"])
        instance = instances[0]
        self.assertEqual(instance.repository, "NovaRenard/koshakan-crm")
        self.assertEqual(instance.compose_project_name, "koshakan-crm")

    def test_missing_state_files_return_unknown_deployment(self) -> None:
        instance = InstanceConfig(app_id="atlant", state_dir=Path("/var/lib/saad-deploy/atlant"))
        with patch.object(Path, "open", side_effect=FileNotFoundError):
            deployment = collect_deployment_state(instance)

        self.assertEqual(deployment.status, "unknown")
        self.assertIsNone(deployment.current_sha)
        self.assertIsNone(deployment.last_error)

    def test_broken_status_json_does_not_hide_other_state(self) -> None:
        instance = InstanceConfig(app_id="econrg", state_dir=Path("/var/lib/saad-deploy/econrg"))
        with patch(
            "agent.collectors.saad_deploy._read_optional_text",
            side_effect=["{not-json", "current-sha-value", None, None, None],
        ):
            deployment = collect_deployment_state(instance)

        self.assertEqual(deployment.status, "unknown")
        self.assertEqual(deployment.current_sha, "current-sha-value")
