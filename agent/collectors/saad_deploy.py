"""Discovery and state-file collection for the separate saad-deploy engine."""

from __future__ import annotations

import json
import logging
import shlex
from pathlib import Path

from pydantic import BaseModel

from agent.models import DeploymentTelemetry

logger = logging.getLogger(__name__)
MAX_ERROR_BYTES = 8 * 1024


class InstanceConfig(BaseModel):
    """The read-only deployment metadata declared by one saad-deploy env file."""

    app_id: str
    repository: str | None = None
    branch: str | None = None
    app_dir: Path | None = None
    compose_file: Path | None = None
    compose_project_name: str | None = None
    state_dir: Path | None = None


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


def discover_instances(config_dir: Path) -> list[InstanceConfig]:
    """Discover each valid *.env declaration without letting one file stop others."""

    instances: list[InstanceConfig] = []
    try:
        env_paths = sorted(config_dir.glob("*.env"))
    except OSError as exc:
        logger.error("Cannot list saad-deploy config directory %s: %s", config_dir, exc)
        return instances

    for env_path in env_paths:
        try:
            values = parse_env_file(env_path)
            app_id = values.get("APP_ID", "").strip()
            if not app_id:
                logger.warning("Skipping %s: APP_ID is missing", env_path)
                continue
            instances.append(
                InstanceConfig(
                    app_id=app_id,
                    repository=values.get("GITHUB_REPOSITORY") or None,
                    branch=values.get("DEPLOY_BRANCH") or None,
                    app_dir=values.get("APP_DIR") or None,
                    compose_file=values.get("COMPOSE_FILE") or None,
                    compose_project_name=values.get("COMPOSE_PROJECT_NAME") or None,
                    state_dir=values.get("STATE_DIR") or None,
                )
            )
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            logger.exception("Skipping unreadable saad-deploy config %s: %s", env_path, exc)
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


def collect_deployment_state(instance: InstanceConfig) -> DeploymentTelemetry:
    """Read existing saad-deploy state files; missing or invalid files are normal."""

    if instance.state_dir is None:
        return DeploymentTelemetry()

    state_dir = instance.state_dir
    status = "unknown"
    step: str | None = None
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
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Ignoring invalid deployment status file %s: %s", status_path, exc)

    return DeploymentTelemetry(
        status=status,
        step=step,
        current_sha=_read_optional_text(state_dir / "current-sha"),
        previous_sha=_read_optional_text(state_dir / "previous-sha"),
        deployed_at=_read_optional_text(state_dir / "deployed-at"),
        last_error=_read_optional_text(state_dir / "last-error.log", max_bytes=MAX_ERROR_BYTES),
    )
