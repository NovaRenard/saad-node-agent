from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


HELPER_PATH = Path(__file__).parents[1] / "scripts" / "control_helper.py"


def load_control_helper():
    spec = importlib.util.spec_from_file_location("control_helper", HELPER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ControlHelperTests(unittest.TestCase):
    def test_rejects_arbitrary_operations_and_malformed_app_ids(self) -> None:
        helper = load_control_helper()
        self.assertEqual(helper.main(["shell", "id"]), 64)
        self.assertEqual(helper.main(["deploy", "../../etc"]), 65)
        self.assertEqual(helper.main(["logs", "atlant", "bad;name", "10"]), 64)

    def test_logs_require_a_container_owned_by_the_registered_compose_project(self) -> None:
        helper = load_control_helper()
        result = Mock(returncode=0, stdout=json.dumps([{"Config": {"Labels": {"com.docker.compose.project": "other", "com.docker.compose.service": "web"}}}]))
        with (
            patch.object(helper, "registered_app", return_value={"COMPOSE_PROJECT_NAME": "atlant"}),
            patch.object(helper.subprocess, "run", return_value=result),
        ):
            self.assertFalse(helper.allowed_container("atlant", "atlant-web-1"))

    def test_fixed_operations_never_construct_shell_commands(self) -> None:
        helper = load_control_helper()
        with (
            patch.object(helper, "registered_app", return_value={"COMPOSE_PROJECT_NAME": "atlant"}),
            patch.object(helper.Path, "is_file", return_value=True),
            patch.object(helper.os, "execv", side_effect=RuntimeError("executed")) as execv,
        ):
            with self.assertRaisesRegex(RuntimeError, "executed"):
                helper.main(["restart", "atlant"])
        self.assertEqual(execv.call_args.args[1], [str(helper.DEPLOY_BIN_DIR / "restart.sh"), "atlant"])

    def test_recreate_is_a_fixed_entrypoint_with_no_extra_arguments(self) -> None:
        helper = load_control_helper()
        with (
            patch.object(helper, "registered_app", return_value={"COMPOSE_PROJECT_NAME": "atlant"}),
            patch.object(helper.Path, "is_file", return_value=True),
            patch.object(helper.os, "execv", side_effect=RuntimeError("executed")) as execv,
        ):
            with self.assertRaisesRegex(RuntimeError, "executed"):
                helper.main(["recreate", "atlant"])
        self.assertEqual(execv.call_args.args[1], [str(helper.DEPLOY_BIN_DIR / "recreate.sh"), "atlant"])
        self.assertEqual(helper.main(["recreate", "atlant", "web"]), 64)
