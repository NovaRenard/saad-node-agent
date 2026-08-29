from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent.collectors.saad_deploy import (
    InstanceConfig,
    collect_deployment_state,
    discover_instances,
    discover_instances_from_helper,
    parse_env_file,
)


HELPER_PATH = Path(__file__).parents[1] / "scripts" / "deploy_metadata.py"


def load_metadata_helper():
    spec = importlib.util.spec_from_file_location("deploy_metadata_helper", HELPER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def test_helper_metadata_exposes_allowlisted_fields_only(self) -> None:
        helper = load_metadata_helper()
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_dir = Path(temporary_directory) / "configs"
            state_dir = Path(temporary_directory) / "state"
            config_dir.mkdir()
            state_dir.mkdir()
            (config_dir / "koshakan.env").write_text(
                "\n".join(
                    [
                        "APP_ID=koshakan",
                        "GITHUB_REPOSITORY=NovaRenard/koshakan-crm",
                        "COMPOSE_PROJECT_NAME=koshakan-crm",
                        f"STATE_DIR={state_dir}",
                        "DATABASE_URL=postgresql://must-not-leak",
                        "GITHUB_TOKEN=must-not-leak",
                    ]
                ),
                encoding="utf-8",
            )
            (state_dir / "status.json").write_text(
                '{"status":"healthy","step":"complete","target_sha":"abc123","started_at":"2026-08-29T12:00:00Z","finished_at":"2026-08-29T12:01:00Z"}',
                encoding="utf-8",
            )
            (state_dir / "current-sha").write_text("abc123", encoding="utf-8")
            (state_dir / "last-error.log").write_text("token=must-not-leak", encoding="utf-8")

            records = helper.collect_instances(config_dir)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["app_id"], "koshakan")
        self.assertEqual(record["deployment"]["status"], "healthy")
        self.assertEqual(record["deployment"]["current_sha"], "abc123")
        self.assertEqual(record["deployment"]["target_sha"], "abc123")
        self.assertEqual(record["deployment"]["started_at"], "2026-08-29T12:00:00Z")
        self.assertEqual(record["deployment"]["finished_at"], "2026-08-29T12:01:00Z")
        serialized = json.dumps(record)
        self.assertNotIn("DATABASE_URL", serialized)
        self.assertNotIn("GITHUB_TOKEN", serialized)
        self.assertNotIn("last_error", serialized)
        self.assertNotIn("must-not-leak", serialized)

    def test_agent_uses_the_fixed_metadata_helper_and_rejects_extra_fields(self) -> None:
        completed = Mock(
            stdout=json.dumps(
                [
                    {
                        "app_id": "koshakan",
                        "repository": "NovaRenard/koshakan-crm",
                        "compose_project_name": "koshakan-crm",
                        "deployment": {"status": "healthy", "current_sha": "abc123", "last_error": "must-not-leak"},
                        "DATABASE_URL": "must-not-leak",
                    }
                ]
            )
        )
        with patch("agent.collectors.saad_deploy.subprocess.run", return_value=completed) as run:
            instances = discover_instances_from_helper("/fixed/deploy-metadata.py", use_sudo=False)

        self.assertEqual(run.call_args.args[0], ["/fixed/deploy-metadata.py"])
        self.assertEqual([instance.app_id for instance in instances], ["koshakan"])
        self.assertEqual(instances[0].deployment.status, "healthy")
        self.assertIsNone(instances[0].deployment.last_error)

    def test_installer_recreates_the_derived_virtualenv_on_updates(self) -> None:
        installer = (Path(__file__).parents[1] / "scripts" / "install.sh").read_text(encoding="utf-8")

        self.assertIn('"$PYTHON_BIN" -m venv --clear "$INSTALL_DIR/.venv"', installer)

    def test_service_allows_only_the_capabilities_needed_by_fixed_sudo_helpers(self) -> None:
        service_unit = (Path(__file__).parents[1] / "systemd" / "saad-node-agent.service").read_text(encoding="utf-8")

        self.assertIn(
            "CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_AUDIT_WRITE CAP_DAC_READ_SEARCH",
            service_unit,
        )

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
