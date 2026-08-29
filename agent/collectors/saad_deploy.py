"""Discovery and state-file collection for the separate saad-deploy engine."""

from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agent.models import DeploymentTelemetry

logger = logging.getLogger(__name__)
MAX_ERROR_BYTES = 8 * 1024
SENSITIVE_ERROR_VALUE = re.compile(
    r"(?i)(?P<key>(?:[a-z0-9_-]*(?:token|secret|password|api[_-]?key)[a-z0-9_-]*|authorization|cookie|database_url))\s*[:=]\s*[^\s]+"
)
URL_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")


class InstanceConfig(BaseModel):
    """The read-only deployment metadata declared by one saad-deploy env file."""

    app_id: str
    repository: str | None = None
    branch: str | None = None
    app_dir: Path | None = None
    compose_file: Path | None = None
    compose_project_name: str | None = None
    state_dir: Path | None = None
    deployment: DeploymentTelemetry | None = None


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse the simple KEY=value format used by saad-deploy configuration.

    This intentionally does not source the file: deployment config remains data,
    never executable input to the agent.
    """

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not key or not key.replace("_", "A").isalnum():
            logger.warning("Ignoring malformed line %s in %s", line_number, path)
            continue
        value = raw_value.strip()
        if value[:1] in {"'", '"'}:
            try:
                parsed = shlex.split(value, comments=True, posix=True)
            except ValueError:
                logger.warning("Ignoring malformed quoted value for %s in %s", key, path)
                continue
            value = parsed[0] if parsed else ""
        else:
            # Preserve hashes which are part of a value, while allowing the
            # conventional whitespace-delimited inline comment.
            value = value.split(" #", maxsplit=1)[0].rstrip()
        values[key] = value
    return values


def _instance_from_values(
    values: dict[str, Any], *, deployment: DeploymentTelemetry | None = None
) -> InstanceConfig | None:
    """Build an instance from the deliberately small, non-secret metadata set."""

    app_id = values.get("APP_ID")
    if not isinstance(app_id, str) or not app_id.strip():
        return None

    def optional_string(key: str) -> str | None:
        value = values.get(key)
        return value.strip() or None if isinstance(value, str) else None

    return InstanceConfig(
        app_id=app_id.strip(),
        repository=optional_string("GITHUB_REPOSITORY"),
        branch=optional_string("DEPLOY_BRANCH"),
        app_dir=optional_string("APP_DIR"),
        compose_file=optional_string("COMPOSE_FILE"),
        compose_project_name=optional_string("COMPOSE_PROJECT_NAME"),
        state_dir=optional_string("STATE_DIR"),
        deployment=deployment,
    )


def discover_instances(config_dir: Path) -> list[InstanceConfig]:
    """Discover instances directly when deployment configs are readable."""

    instances: list[InstanceConfig] = []
    try:
        env_paths = sorted(config_dir.glob("*.env"))
    except OSError as exc:
        logger.error("Cannot list saad-deploy config directory %s: %s", config_dir, exc)
        return instances

    for env_path in env_paths:
        try:
            values = parse_env_file(env_path)
            instance = _instance_from_values(values)
            if instance is None:
                logger.warning("Skipping %s: APP_ID is missing", env_path)
                continue
            instances.append(instance)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            logger.exception("Skipping unreadable saad-deploy config %s: %s", env_path, exc)
    return instances


def discover_instances_from_helper(
    helper_path: Path,
    *,
    use_sudo: bool = True,
    timeout: float = 20.0,
) -> list[InstanceConfig]:
    """Read sanitized deployment metadata from the fixed privileged helper.

    The helper accepts no arguments and only emits fields the dashboard is
    permitted to receive. It lets deployment env files remain root-only.
    """

    command = [str(helper_path)]
    if use_sudo:
        command = ["sudo", "-n", *command]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
        payload = json.loads(result.stdout)
        if not isinstance(payload, list):
            raise ValueError("deployment metadata root must be a list")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Deployment metadata unavailable: %s", exc)
        return []

    instances: list[InstanceConfig] = []
    for record in payload:
        if not isinstance(record, dict):
            logger.warning("Ignoring malformed deployment metadata record")
            continue
        deployment = DeploymentTelemetry()
        raw_deployment = record.get("deployment")
        if isinstance(raw_deployment, dict):
            safe_deployment = {
                key: value
                for key, value in raw_deployment.items()
                if key in {
                    "status",
                    "step",
                    "current_sha",
                    "previous_sha",
                    "target_sha",
                    "deployed_at",
                    "started_at",
                    "finished_at",
                    "source",
                    "last_error",
                }
                and isinstance(value, str)
            }
            if isinstance(safe_deployment.get("last_error"), str):
                safe_deployment["last_error"] = _sanitize_error_text(safe_deployment["last_error"])
            try:
                deployment = DeploymentTelemetry(**safe_deployment)
            except ValueError:
                logger.warning("Ignoring malformed deployment state for %s", record.get("app_id", "unknown"))

        instance = _instance_from_values(
            {
                "APP_ID": record.get("app_id"),
                "GITHUB_REPOSITORY": record.get("repository"),
                "DEPLOY_BRANCH": record.get("branch"),
                "APP_DIR": record.get("app_dir"),
                "COMPOSE_FILE": record.get("compose_file"),
                "COMPOSE_PROJECT_NAME": record.get("compose_project_name"),
                "STATE_DIR": record.get("state_dir"),
            },
            deployment=deployment,
        )
        if instance is None:
            logger.warning("Ignoring deployment metadata without APP_ID")
            continue
        instances.append(instance)
    return instances


def _read_optional_text(path: Path, *, max_bytes: int | None = None) -> str | None:
    try:
        with path.open("rb") as state_file:
            content = state_file.read(max_bytes) if max_bytes else state_file.read()
        value = content.decode("utf-8", errors="replace").strip()
        return value or None
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Cannot read deployment state file %s: %s", path, exc)
        return None


def _safe_error_text(path: Path) -> str | None:
    value = _read_optional_text(path, max_bytes=MAX_ERROR_BYTES)
    return _sanitize_error_text(value)


def _sanitize_error_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = SENSITIVE_ERROR_VALUE.sub(lambda match: f"{match.group('key')}=[redacted]", value)
    return URL_CREDENTIALS.sub(r"\1[redacted]@", value) or None


def collect_deployment_state(instance: InstanceConfig) -> DeploymentTelemetry:
    """Read existing saad-deploy state files; missing or invalid files are normal."""

    if instance.state_dir is None:
        return DeploymentTelemetry()

    state_dir = instance.state_dir
    status = "unknown"
    step: str | None = None
    target_sha: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    source: str | None = None
    status_path = state_dir / "status.json"
    try:
        raw_status = _read_optional_text(status_path)
        if raw_status:
            parsed_status = json.loads(raw_status)
            if not isinstance(parsed_status, dict):
                raise ValueError("status.json root must be an object")
            raw_state = parsed_status.get("status")
            raw_step = parsed_status.get("step")
            status = str(raw_state) if raw_state is not None else status
            step = str(raw_step) if raw_step is not None else None
            for key in ("target_sha", "started_at", "finished_at", "source"):
                value = parsed_status.get(key)
                if isinstance(value, str) and value.strip():
                    if key == "target_sha":
                        target_sha = value.strip()
                    elif key == "started_at":
                        started_at = value.strip()
                    elif key == "finished_at":
                        finished_at = value.strip()
                    else:
                        source = value.strip()
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Ignoring invalid deployment status file %s: %s", status_path, exc)

    return DeploymentTelemetry(
        status=status,
        step=step,
        current_sha=_read_optional_text(state_dir / "current-sha"),
        previous_sha=_read_optional_text(state_dir / "previous-sha"),
        target_sha=target_sha,
        deployed_at=_read_optional_text(state_dir / "deployed-at"),
        started_at=started_at,
        finished_at=finished_at,
        last_error=_safe_error_text(state_dir / "last-error.log"),
        source=source,
    )
