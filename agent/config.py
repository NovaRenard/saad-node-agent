"""Configuration loaded from the process environment or an env file."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_ENV_FILE = Path("/etc/saad-node-agent/agent.env")
DEFAULT_SNAPSHOT_HELPER = Path("/usr/local/lib/saad-node-agent/snapshot.sh")


class Settings(BaseSettings):
    """Runtime configuration for a single managed node."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    node_id: str = Field(min_length=1)
    node_name: str = Field(min_length=1)
    dashboard_url: HttpUrl
    node_token: str = Field(min_length=1, repr=False)
    heartbeat_interval_seconds: int = Field(default=15, ge=5, le=3600)
    telemetry_interval_seconds: float = Field(default=2.0, ge=0.5, le=3600)
    full_snapshot_interval_seconds: int = Field(default=30, ge=5, le=3600)
    ws_ping_interval_seconds: int = Field(default=15, ge=5, le=300)
    ws_ping_timeout_seconds: int = Field(default=15, ge=5, le=300)
    ws_reconnect_initial_seconds: float = Field(default=1.0, ge=0.1, le=60)
    ws_reconnect_max_seconds: float = Field(default=30.0, ge=1, le=600)
    saad_deploy_path: Path = Path("/opt/saad-deploy")
    saad_deploy_config_dir: Path = Path("/etc/saad-deploy")
    snapshot_helper_path: Path = DEFAULT_SNAPSHOT_HELPER
    snapshot_use_sudo: bool = True
    http_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    heartbeat_max_attempts: int = Field(default=3, ge=1, le=5)


def load_settings(env_file: Path | None = None) -> Settings:
    """Load settings from the environment and, when readable, an env file.

    systemd reads the production file itself and injects its values into the
    service. The unprivileged agent deliberately does not need file-read access
    to that root-owned secret after startup.
    """

    candidate = env_file
    if candidate is None and DEFAULT_ENV_FILE.is_file() and os.access(DEFAULT_ENV_FILE, os.R_OK):
        candidate = DEFAULT_ENV_FILE
    return Settings(_env_file=candidate, _env_file_encoding="utf-8")
