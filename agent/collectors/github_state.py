"""Validated GitHub vs server version state written by saad-deploy (github.json)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from agent.models import GitCommitTelemetry, GitHubTelemetry

logger = logging.getLogger(__name__)

MAX_BYTES = 16 * 1024
SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")
GITHUB_URL = re.compile(r"^https://github\.com/[A-Za-z0-9_.\-/]{1,300}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
RUN_STATES = {"queued", "in_progress", "completed", "waiting", "requested", "pending"}
CONCLUSIONS = {"success", "failure", "cancelled", "skipped", "timed_out", "action_required", "neutral", "stale", "startup_failure"}
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = CONTROL.sub(" ", value).strip()
    return cleaned[:limit] or None


def _commit(value: object) -> GitCommitTelemetry | None:
    if not isinstance(value, dict):
        return None
    sha = value.get("sha")
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        return None
    url = value.get("url")
    status = value.get("status")
    conclusion = value.get("conclusion")
    return GitCommitTelemetry(
        sha=sha.lower(),
        message=_text(value.get("message"), 200),
        committed_at=_text(value.get("committed_at"), 40),
        url=url if isinstance(url, str) and GITHUB_URL.fullmatch(url) else None,
        status=status if status in RUN_STATES else None,
        conclusion=conclusion if conclusion in CONCLUSIONS else None,
    )


def parse_github_state(value: object) -> GitHubTelemetry | None:
    if not isinstance(value, dict):
        return None
    repository = value.get("repository")
    return GitHubTelemetry(
        repository=repository if isinstance(repository, str) and REPOSITORY.fullmatch(repository) else None,
        branch=_text(value.get("branch"), 120),
        checked_at=_text(value.get("checked_at"), 40),
        latest=_commit(value.get("latest")),
        deployable=_commit(value.get("deployable")),
        current=_commit(value.get("current")),
    )


def read_github_state(state_dir: Path | str | None) -> GitHubTelemetry | None:
    if not state_dir:
        return None
    path = Path(state_dir) / "github.json"
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Cannot read %s: %s", path, exc)
        return None
    if len(raw) > MAX_BYTES:
        return None
    try:
        return parse_github_state(json.loads(raw.decode("utf-8", errors="replace")))
    except json.JSONDecodeError:
        return None
