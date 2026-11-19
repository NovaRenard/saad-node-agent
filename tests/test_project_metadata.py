import tempfile
import unittest
from pathlib import Path

from agent.collectors.saad_deploy import discover_instances
from scripts import deploy_metadata


CONTRACT = """GITHUB_REPOSITORY=owner/landing
APP_NAME="Landing page"
APP_CATEGORY=site
PUBLIC_URL=https://landing.example
STATE_DIR=/var/lib/saad-deploy/landing
"""


class ProjectMetadataTests(unittest.TestCase):
    def test_contract_without_app_id_uses_file_name_and_skips_overlays(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            (config_dir / "landing.env").write_text(CONTRACT, encoding="utf-8")
            (config_dir / "landing.blue.env").write_text("BACKEND_PORT=1\n", encoding="utf-8")
            instances = discover_instances(config_dir)
            self.assertEqual([instance.app_id for instance in instances], ["landing"])
            self.assertEqual(instances[0].display_name, "Landing page")
            self.assertEqual(instances[0].category, "site")
            self.assertEqual(instances[0].public_url, "https://landing.example")

            records = deploy_metadata.collect_instances(config_dir)
            self.assertEqual([record["app_id"] for record in records], ["landing"])
            self.assertEqual(records[0]["category"], "site")
            self.assertEqual(records[0]["display_name"], "Landing page")

    def test_unknown_category_and_unsafe_url_are_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            (config_dir / "x.env").write_text("APP_ID=x\nAPP_CATEGORY=spaceship\nPUBLIC_URL=javascript:alert(1)\n", encoding="utf-8")
            [instance] = discover_instances(config_dir)
            self.assertIsNone(instance.category)
            self.assertIsNone(instance.public_url)
