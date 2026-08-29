#!/usr/bin/env python3
"""Fixed privileged control operations for saad-node-agent.

The dashboard can select an operation only from this allowlist. Every command
is delegated to a fixed saad-deploy entrypoint or a fixed Docker invocation;
this helper never evaluates user input as a shell command.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


CONFIG_DIR = Path("/etc/saad-deploy")
DEPLOY_BIN_DIR = Path("/opt/saad-deploy/bin")
DOCKER = "/usr/bin/docker"
APP_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,119}$")
CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
MAX_TAIL_LINES = 1_000


def env_values(path: Path) -> dict[str, str]:
    """Parse root-owned declarations as data rather than sourcing them."""

    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or not key.replace("_", "A").isalnum():
            continue
        try:
            values[key] = (
                shlex.split(value, comments=True, posix=True)[0]
                if value[:1] in {'"', "'"}
                else value.split(" #", 1)[0].strip()
            )
        except ValueError:
            continue
    return values


def registered_app(app_id: str) -> dict[str, str] | None:
    if not APP_ID.fullmatch(app_id):
        return None
    config_path = CONFIG_DIR / f"{app_id}.env"
    try:
        if not config_path.is_file():
            return None
        values = env_values(config_path)
    except (OSError, UnicodeDecodeError):
        return None
    if values.get("APP_ID") != app_id or not values.get("COMPOSE_PROJECT_NAME"):
        return None
    return values


def allowed_container(app_id: str, container: str) -> bool:
    values = registered_app(app_id)
    if values is None or not CONTAINER.fullmatch(container):
        return False
    result = subprocess.run([DOCKER, "inspect", container], capture_output=True, text=True, check=False)
    if result.returncode:
        return False
    try:
        record = json.loads(result.stdout)[0]
    except (json.JSONDecodeError, IndexError, TypeError):
        return False
    labels = record.get("Config", {}).get("Labels", {})
    return (
        isinstance(labels, dict)
        and labels.get("com.docker.compose.project") == values["COMPOSE_PROJECT_NAME"]
        and bool(labels.get("com.docker.compose.service"))
    )


def _exec_fixed(entrypoint: str, app_id: str, *extra: str) -> int:
    if registered_app(app_id) is None:
        return 65
    command = DEPLOY_BIN_DIR / entrypoint
    if not command.is_file():
        return 69
    os.execv(str(command), [str(command), app_id, *extra])
    return 127


def _logs(app_id: str, container: str, raw_tail: str) -> int:
    if not APP_ID.fullmatch(app_id) or not CONTAINER.fullmatch(container):
        return 64
    try:
        tail = int(raw_tail)
    except ValueError:
        return 64
    if not 1 <= tail <= MAX_TAIL_LINES or not allowed_container(app_id, container):
        return 65
    os.execv(DOCKER, [DOCKER, "logs", "--tail", str(tail), "--follow", container])
    return 127


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        return 64
    operation = args[0]
    if operation == "logs" and len(args) == 4:
        return _logs(args[1], args[2], args[3])
    if operation == "restart" and len(args) == 2:
        return _exec_fixed("restart.sh", args[1])
    if operation == "deploy" and len(args) == 2:
        return _exec_fixed("deploy-sha.sh", args[1], "--query-ci", "--source", "manual")
    if operation == "rollback" and len(args) == 2:
        return _exec_fixed("rollback.sh", args[1])
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
