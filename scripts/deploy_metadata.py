#!/usr/bin/env python3
"""Emit a secret-free view of root-owned saad-deploy configuration.

This helper intentionally accepts no arguments. It is executed by sudo only
through a fixed sudoers rule and writes JSON to stdout for saad-node-agent.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any


CONFIG_DIR = Path("/etc/saad-deploy")
MAX_STATE_BYTES = 8 * 1024
MAX_ERROR_BYTES = 8 * 1024
SENSITIVE_ERROR_VALUE = re.compile(
    r"(?i)(?P<key>(?:[a-z0-9_-]*(?:token|secret|password|api[_-]?key)[a-z0-9_-]*|authorization|cookie|database_url))\s*[:=]\s*[^\s]+"
)
URL_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")
METADATA_KEYS = {
    "repository": "GITHUB_REPOSITORY",
    "branch": "DEPLOY_BRANCH",
    "app_dir": "APP_DIR",
    "compose_file": "COMPOSE_FILE",
    "compose_project_name": "COMPOSE_PROJECT_NAME",
    "state_dir": "STATE_DIR",
}


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=value data without sourcing an untrusted shell file."""

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not key or not key.replace("_", "A").isalnum():
            continue
        value = raw_value.strip()
        if value[:1] in {"'", '"'}:
            try:
                parsed = shlex.split(value, comments=True, posix=True)
            except ValueError:
                continue
            value = parsed[0] if parsed else ""
        else:
            value = value.split(" #", maxsplit=1)[0].rstrip()
        values[key] = value
    return values


def read_optional_text(path: Path, *, max_bytes: int = MAX_STATE_BYTES) -> str | None:
    try:
        with path.open("rb") as state_file:
            value = state_file.read(max_bytes).decode("utf-8", errors="replace").strip()
        return value or None
    except OSError:
        return None


def safe_error_text(path: Path) -> str | None:
    value = read_optional_text(path, max_bytes=MAX_ERROR_BYTES)
    if value is None:
        return None
    value = SENSITIVE_ERROR_VALUE.sub(lambda match: f"{match.group('key')}=[redacted]", value)
    value = URL_CREDENTIALS.sub(r"\1[redacted]@", value)
    return value or None


def collect_deployment_state(state_dir: str | None) -> dict[str, str | None]:
    """Return deployment fields that are useful and safe to send upstream."""

    deployment: dict[str, str | None] = {
        "status": "unknown",
        "step": None,
        "current_sha": None,
        "previous_sha": None,
        "target_sha": None,
        "deployed_at": None,
        "started_at": None,
        "finished_at": None,
        "source": None,
        "last_error": None,
    }
    if not state_dir:
        return deployment

    directory = Path(state_dir)
    raw_status = read_optional_text(directory / "status.json")
    if raw_status:
        try:
            parsed_status = json.loads(raw_status)
        except json.JSONDecodeError:
            parsed_status = None
        if isinstance(parsed_status, dict):
            status = parsed_status.get("status")
            step = parsed_status.get("step")
            if status is not None:
                deployment["status"] = str(status)
            if step is not None:
                deployment["step"] = str(step)
            for key in ("target_sha", "started_at", "finished_at", "source"):
                value = parsed_status.get(key)
                if isinstance(value, str) and value.strip():
                    deployment[key] = value.strip()

    for metadata_key, filename in {
        "current_sha": "current-sha",
        "previous_sha": "previous-sha",
        "deployed_at": "deployed-at",
    }.items():
        deployment[metadata_key] = read_optional_text(directory / filename)
    # This is intentionally the only error file exposed by the root-owned
    # helper. saad-deploy writes a fixed failure summary here, and the bounded
    # read prevents an unexpected file size from turning telemetry into a log
    # transport. Deployment env files and arbitrary paths are never exposed.
    deployment["last_error"] = safe_error_text(directory / "last-error.log")
    return deployment


def collect_instances(config_dir: Path = CONFIG_DIR) -> list[dict[str, Any]]:
    """Collect only an explicit allowlist from every saad-deploy declaration."""

    try:
        config_paths = sorted(config_dir.glob("*.env"))
    except OSError:
        return []

    instances: list[dict[str, Any]] = []
    for config_path in config_paths:
        try:
            values = parse_env_file(config_path)
        except (OSError, UnicodeDecodeError):
            continue
        app_id = values.get("APP_ID", "").strip()
        if not app_id:
            continue
        instance = {"app_id": app_id}
        for output_key, source_key in METADATA_KEYS.items():
            value = values.get(source_key, "").strip()
            instance[output_key] = value or None
        instance["deployment"] = collect_deployment_state(instance["state_dir"])
        instances.append(instance)
    return instances


def main() -> int:
    if len(sys.argv) != 1:
        print("deploy metadata helper accepts no arguments", file=sys.stderr)
        return 64
    print(json.dumps(collect_instances(), separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
