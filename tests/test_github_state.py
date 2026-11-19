import json
import tempfile
import unittest
from pathlib import Path

from agent.collectors.github_state import parse_github_state, read_github_state
from scripts import deploy_metadata

STATE = {
    "repository": "NovaRenard/atlant-crm",
    "branch": "main",
    "checked_at": "2026-11-19T16:00:00Z",
    "latest": {"sha": "CCCC1112", "message": "feat: x\u0007", "committed_at": "2026-11-19T16:00:00Z", "url": "https://github.com/NovaRenard/atlant-crm/actions/runs/3", "status": "in_progress", "conclusion": None},
    "deployable": {"sha": "aaaa3333", "message": "chore: release", "url": "javascript:alert(1)"},
    "current": {"sha": "not-a-sha"},
    "token": "must-not-pass",
}


class GitHubStateTests(unittest.TestCase):
    def test_fields_are_validated(self):
        state = parse_github_state(STATE)
        self.assertEqual(state.latest.sha, "cccc1112")
        self.assertEqual(state.latest.message, "feat: x")
        self.assertEqual(state.latest.status, "in_progress")
        self.assertIsNone(state.deployable.url)
        self.assertIsNone(state.current)
        self.assertNotIn("token", state.model_dump())

    def test_helper_and_direct_reader_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "github.json").write_text(json.dumps(STATE), encoding="utf-8")
            helper = deploy_metadata.collect_github_state(directory)
            self.assertNotIn("token", helper)
            self.assertEqual(parse_github_state(helper), read_github_state(directory))

    def test_missing_or_garbage_file_is_none(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(read_github_state(directory))
            Path(directory, "github.json").write_text("{nope", encoding="utf-8")
            self.assertIsNone(read_github_state(directory))
