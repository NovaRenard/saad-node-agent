#!/usr/bin/env bash
# Install/update the telemetry-only agent on a Linux node. Run as root from a clone.
set -euo pipefail

APP_NAME=saad-node-agent
AGENT_USER=saad-node-agent
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR=/opt/saad-node-agent
CONFIG_DIR=/etc/saad-node-agent
HELPER_DIR=/usr/local/lib/saad-node-agent
SYSTEMD_UNIT=/etc/systemd/system/saad-node-agent.service
SUDOERS_FILE=/etc/sudoers.d/saad-node-agent
PYTHON_BIN="${PYTHON_BIN:-python3.12}"

if [ "${EUID}" -ne 0 ]; then
  echo "Run this installer as root." >&2
  exit 1
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.12 is required (missing: $PYTHON_BIN)." >&2
  exit 1
fi

if ! id "$AGENT_USER" >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin "$AGENT_USER"
fi

install -d -o root -g root -m 0755 "$INSTALL_DIR" "$CONFIG_DIR" "$HELPER_DIR"
# Configuration lives outside the source tree and is never copied or overwritten here.
cp -a "$SOURCE_DIR/." "$INSTALL_DIR/"
find "$INSTALL_DIR" -type d -exec chmod 0755 {} +
find "$INSTALL_DIR" -type f -exec chmod 0644 {} +
chown -R root:root "$INSTALL_DIR"

# The source-permission normalization above also touches a prior virtualenv on
# updates. Recreate that derived directory so its interpreter and entry points
# are executable again; the root-owned configuration directory is unaffected.
"$PYTHON_BIN" -m venv --clear "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --disable-pip-version-check --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --disable-pip-version-check -r "$INSTALL_DIR/requirements.txt"

install -o root -g "$AGENT_USER" -m 0750 "$SOURCE_DIR/scripts/snapshot.sh" "$HELPER_DIR/snapshot.sh"
install -o root -g "$AGENT_USER" -m 0750 "$SOURCE_DIR/scripts/deploy_metadata.py" "$HELPER_DIR/deploy-metadata.py"
install -o root -g "$AGENT_USER" -m 0750 "$SOURCE_DIR/scripts/control_helper.py" "$HELPER_DIR/control-helper.py"
install -o root -g root -m 0644 "$SOURCE_DIR/systemd/saad-node-agent.service" "$SYSTEMD_UNIT"

if [ ! -f "$CONFIG_DIR/agent.env" ]; then
  install -o root -g root -m 0600 "$SOURCE_DIR/.env.example" "$CONFIG_DIR/agent.env"
  echo "Created $CONFIG_DIR/agent.env; fill NODE_TOKEN before starting the service." >&2
else
  chmod 0600 "$CONFIG_DIR/agent.env"
  chown root:root "$CONFIG_DIR/agent.env"
fi

cat >"$SUDOERS_FILE" <<'EOF'
# Permit only fixed, argument-free, read-only telemetry helpers.
saad-node-agent ALL=(root) NOPASSWD: /usr/local/lib/saad-node-agent/snapshot.sh "", /usr/local/lib/saad-node-agent/deploy-metadata.py "", /usr/local/lib/saad-node-agent/control-helper.py logs *
EOF
chmod 0440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE"

systemctl daemon-reload
systemctl enable "$APP_NAME.service"
echo "Installed $APP_NAME. Edit $CONFIG_DIR/agent.env, then run: systemctl restart $APP_NAME"
