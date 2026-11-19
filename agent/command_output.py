"""Turn a control-helper exit into an operator-readable, secret-free message.

The helper's stderr is kept only as a bounded tail. saad-deploy prefixes its own
diagnostics with ``saad-deploy:``; the last such line is the most precise reason
and is shown first. Everything is redacted with the same rules as last-error.log.
"""

from __future__ import annotations

import asyncio
import re

from agent.collectors.saad_deploy import _sanitize_error_text

MAX_TAIL_BYTES = 16_384
MAX_TAIL_LINES = 12
MAX_MESSAGE_CHARS = 3_500
ENGINE_PREFIX = "saad-deploy:"

GITHUB_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# Exit codes owned by saad-deploy (lib.sh) and the fixed control helper.
EXIT_REASONS = {
    64: "The control helper rejected the request (invalid arguments).",
    65: "This app is not registered in /etc/saad-deploy on the server.",
    69: "The saad-deploy entrypoint is missing on the server (/opt/saad-deploy/bin).",
}


def lock_busy_message(app_id: str | None) -> str:
    target = app_id or "this app"
    return (
        f"Deployment lock busy for {target} (exit code 75). Another deploy or maintenance "
        "command is still running for this project. Wait for it to finish before retrying; "
        "if it persists, inspect the server's saad-deploy service and deployment lock."
    )


class StderrTail:
    """Drain a stream continuously so the child never blocks, keeping only the tail."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    async def drain(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while chunk := await stream.read(4_096):
            self._buffer += chunk
            if len(self._buffer) > MAX_TAIL_BYTES:
                del self._buffer[: len(self._buffer) - MAX_TAIL_BYTES]

    def lines(self) -> list[str]:
        text = ANSI.sub("", self._buffer.decode("utf-8", errors="replace")).replace("\r", "\n")
        return [line.strip() for line in text.split("\n") if line.strip()]


def redact(value: str) -> str:
    value = GITHUB_TOKEN.sub("[redacted]", value)
    value = BEARER.sub("Bearer [redacted]", value)
    return _sanitize_error_text(value) or ""


def engine_message(lines: list[str]) -> str | None:
    for line in reversed(lines):
        if line.startswith(ENGINE_PREFIX):
            return redact(line[len(ENGINE_PREFIX):].strip()) or None
    return None


def failure_message(exit_code: int | None, lines: list[str], *, timeout_minutes: int | None = None, app_id: str | None = None) -> str:
    reason = engine_message(lines)
    known = EXIT_REASONS.get(exit_code) if exit_code is not None else None
    if exit_code == 75:
        known = lock_busy_message(app_id)
    if timeout_minutes is not None:
        known = f"The operation did not finish within {timeout_minutes} minutes and was stopped."
        reason = None
    if exit_code == 1 and any("a password is required" in line or "sudo:" in line for line in lines):
        known = "sudo refused to run the control helper. Check the saad-node-agent sudoers rule."
    parts = [reason or known or f"Operation failed (exit code {exit_code})."]
    if known and reason and known != reason:
        parts.append(known)
    tail = [redact(line) for line in lines[-MAX_TAIL_LINES:]]
    if tail:
        parts.append("Last output:\n" + "\n".join(tail))
    return "\n\n".join(parts)[:MAX_MESSAGE_CHARS]


def success_summary(lines: list[str]) -> str:
    return (engine_message(lines) or "Operation completed.")[:1_900]
