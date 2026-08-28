#!/usr/bin/env python3
"""Fixed privileged operations for saad-node-agent; never accepts shell input."""
from __future__ import annotations
import json, os, re, shlex, subprocess, sys
from pathlib import Path

CONFIG_DIR = Path('/etc/saad-deploy')
DOCKER = '/usr/bin/docker'
APP_ID = re.compile(r'^[a-z0-9][a-z0-9_-]{0,119}$')
CONTAINER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$')

def env_values(path: Path) -> dict[str, str]:
    values = {}
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'): continue
        if line.startswith('export '): line = line[7:].lstrip()
        key, sep, value = line.partition('=')
        if not sep or not key.replace('_','A').isalnum(): continue
        try: values[key] = shlex.split(value, comments=True)[0] if value[:1] in {'"', "'"} else value.split(' #', 1)[0].strip()
        except ValueError: continue
    return values

def allowed_container(app_id: str, container: str) -> bool:
    values = env_values(CONFIG_DIR / f'{app_id}.env')
    if values.get('APP_ID') != app_id or not values.get('COMPOSE_PROJECT_NAME'): return False
    result = subprocess.run([DOCKER, 'inspect', container], capture_output=True, text=True, check=False)
    if result.returncode: return False
    try: record = json.loads(result.stdout)[0]
    except (json.JSONDecodeError, IndexError, TypeError): return False
    labels = record.get('Config', {}).get('Labels', {})
    return isinstance(labels, dict) and labels.get('com.docker.compose.project') == values['COMPOSE_PROJECT_NAME'] and bool(labels.get('com.docker.compose.service'))

def main() -> int:
    if len(sys.argv) != 5 or sys.argv[1] != 'logs': return 64
    _, _, app_id, container, raw_tail = sys.argv
    if not APP_ID.fullmatch(app_id) or not CONTAINER.fullmatch(container): return 64
    try: tail = int(raw_tail)
    except ValueError: return 64
    if not 1 <= tail <= 1000 or not allowed_container(app_id, container): return 65
    os.execv(DOCKER, [DOCKER, 'logs', '--tail', str(tail), '--follow', container])
    return 127

if __name__ == '__main__': raise SystemExit(main())
