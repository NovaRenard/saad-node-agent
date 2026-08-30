# saad-node-agent

`saad-node-agent` is a small Python service installed on each Linux CRM node. It reports host health, discovered `saad-deploy` applications, deployment state, and per-container Docker telemetry to `saad-dashboard` over a persistent authenticated WebSocket.

It does not expose an HTTP server. Its only control capability is a typed, fixed allowlist of `DEPLOY`, `RESTART`, `RECREATE`, and `ROLLBACK`; it has no generic shell, Docker, filesystem, service, environment, or raw-SHA command interface.

## Relationship to `saad-deploy`

`saad-deploy` remains the deployment engine. This agent neither imports nor recreates its deployment logic. It reads the declarations in `/etc/saad-deploy/*.env` and the deployment-state files each app already owns:

- `status.json`
- `current-sha`
- `previous-sha`
- `deployed-at`
- `last-error.log`

Adding `/etc/saad-deploy/koshakan.env` automatically adds that instance to the next heartbeat; application names are not hard-coded.

## Architecture

```text
systemd (unprivileged saad-node-agent user)
  └─ Python agent
       ├─ psutil: host CPU/RAM/disk/swap/uptime/load plus cumulative network and disk-I/O counters
       ├─ read-only saad-deploy configs and state files
       ├─ sudo -n fixed snapshot.sh: Docker inspect/stats only
       ├─ sudo -n fixed deploy-metadata.py: secret-free CRM metadata/status
       ├─ persistent authenticated WebSocket → saad-dashboard
       └─ bounded HTTPS heartbeat only when WebSocket reconciliation is unavailable
```

The process discovers config files for each heartbeat so a newly added CRM does not require an agent restart. A missing state file, invalid `status.json`, damaged Compose project, unavailable Docker daemon, unavailable dashboard, or one bad instance is contained and logged; the rest of the heartbeat continues.

## Heartbeat protocol

The agent sends `POST {DASHBOARD_URL}/api/v1/node-agent/heartbeat` with:

```http
Authorization: Bearer <NODE_TOKEN>
Content-Type: application/json
```

Its versioned JSON payload contains `protocol_version: 1`, `node_id`, agent version and UTC timestamp; host telemetry; and instances with repository/branch, deployment state, per-container telemetry, plus CPU/RAM resource values. Containers are assigned by the official Docker Compose labels `com.docker.compose.project` and `com.docker.compose.service`, never by a name substring. A successful dashboard response is `{"ok": true}`.

Host telemetry v2 adds logical/physical CPU counts, available memory, swap,
cumulative network RX/TX and disk I/O byte counters, OS/kernel details, and
Docker availability/version. Counters are deliberately cumulative: the
dashboard derives rates and safely treats a reboot/reset as a gap. The agent
also reports these self-declared capabilities on each full snapshot and HTTP
heartbeat:

```text
DEPLOY RESTART ROLLBACK RECREATE LOGS HOST_METRICS_V2
```

The dashboard rollout must be upgraded first: its v2 fields are optional, so
it accepts both this expanded heartbeat and the original agent schema. HTTP
heartbeat remains the same fallback transport.

For transient connection errors and 5xx responses the client uses a bounded exponential retry (up to three attempts), then waits until the next heartbeat interval. Authentication and other 4xx errors are logged and retried only on the next normal interval.

## Install on `econrg-lab`

Requirements: Linux, systemd, Python 3.12, Docker CLI at `/usr/bin/docker`, and an existing `saad-deploy` configuration.

```bash
git clone <repository-url> /tmp/saad-node-agent
cd /tmp/saad-node-agent
sudo scripts/install.sh
sudoedit /etc/saad-node-agent/agent.env
sudo systemctl restart saad-node-agent
systemctl status saad-node-agent
```

The installer creates a dedicated system user, copies the agent to `/opt/saad-node-agent`, recreates its derived Python virtual environment, installs the systemd service, and enables it at boot. It creates `/etc/saad-node-agent/agent.env` only when absent, so updates never overwrite an existing production token or configuration.

`NODE_TOKEN` is deliberately a placeholder after the first install. Add the real token before starting the service. Do not commit this file.

## Configuration

The production config is `/etc/saad-node-agent/agent.env` (mode `0600`):

```env
NODE_ID=econrg-lab
NODE_NAME=econrg-lab
DASHBOARD_URL=https://app.saadsarbas.tech
NODE_TOKEN=replace-with-node-token
HEARTBEAT_INTERVAL_SECONDS=15
SAAD_DEPLOY_PATH=/opt/saad-deploy
SAAD_DEPLOY_CONFIG_DIR=/etc/saad-deploy
DEPLOY_METADATA_HELPER_PATH=/usr/local/lib/saad-node-agent/deploy-metadata.py
```

See [`.env.example`](.env.example) for optional transport/helper settings. For a manual test outside systemd, run:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
sudo .venv/bin/python -m agent.main --env-file /etc/saad-node-agent/agent.env
```

## Operations and logs

```bash
systemctl status saad-node-agent
journalctl -u saad-node-agent -f
journalctl -u saad-node-agent --since '1 hour ago'
```

### Fixed remote operations

The dashboard can request only these typed operations for a registered app ID:

- **Deploy latest** runs the existing fixed `deploy-sha.sh --query-ci` workflow.
- **Restart** restarts registered application containers without recreating them.
- **Apply config / Recreate** runs only `/opt/saad-deploy/bin/recreate.sh <app-id>`.
  It uses the current SHA, force-recreates only configured application services,
  and does not run migrations, build images, or touch infrastructure services.
- **Rollback** runs the fixed rollback entrypoint.

The helper rejects unknown commands, malformed IDs, duplicate lifecycle command
IDs, extra arguments, and all shell/Docker/Compose input. RECREATE therefore
does not expand the agent into a generic remote executor.

## Security model

The Python service runs as the unprivileged `saad-node-agent` user and does not have direct access to `/var/run/docker.sock` or the root-owned `saad-deploy` env files. systemd reads the root-owned `0600` config file and supplies its values as process environment; the agent does not need to reopen the secret file. It can run fixed `sudo -n` helpers only: read-only Docker snapshot, deployment metadata, and a strict control helper.

The Docker helper has a fixed `PATH`, accepts no user input, and only runs these Docker read operations: `docker ps --all --quiet`, `docker inspect`, and `docker stats --all --no-stream`. It returns their data as JSON. The deployment helper safely parses (never sources) `/etc/saad-deploy/*.env` and emits only allowlisted deployment metadata plus bounded, redacted `last-error.log` text. The control helper validates app and container identifiers, verifies Compose project ownership, and executes only fixed `saad-deploy` entrypoints (or fixed `docker logs`); it never calls a shell or accepts arbitrary arguments.

The systemd unit additionally limits filesystem visibility, address families, and file permissions. Its bounded capabilities are only sufficient for `sudo` to start the two fixed helpers and read deployment state; the Python agent itself remains unprivileged. Deployment config and state are read only; no local database is created.

## Connect another server

1. Install Python 3.12, Docker CLI, systemd, and configure its existing `saad-deploy` application env files.
2. Clone this repository and run `sudo scripts/install.sh`.
3. Give the new node its unique `NODE_ID`/`NODE_NAME`, dashboard URL, and node token in `/etc/saad-node-agent/agent.env`.
4. Start the service and inspect its journal. The agent discovers all `*.env` files in `SAAD_DEPLOY_CONFIG_DIR` automatically.

## Development verification

```bash
python -m unittest discover -s tests -v
```
