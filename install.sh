#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/horcrux-monitoring"
CONFIG_DIR="/etc/horcrux-monitoring"

echo "==> Installing horcrux-monitoring"

# Venv + deps
echo "==> Creating venv and installing dependencies"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

# Config
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    echo "==> Creating config directory"
    mkdir -p "$CONFIG_DIR"
    cp "$INSTALL_DIR/config.example.yaml" "$CONFIG_DIR/config.yaml"
    echo "    Edit $CONFIG_DIR/config.yaml before starting"
else
    echo "==> Config already exists at $CONFIG_DIR/config.yaml, skipping"
fi

# systemd
echo "==> Installing systemd service"
cp "$INSTALL_DIR/horcrux-monitoring.service" /etc/systemd/system/
systemctl daemon-reload

echo ""
echo "Done. Next steps:"
echo "  1. Edit $CONFIG_DIR/config.yaml"
echo "  2. sudo systemctl enable --now horcrux-monitoring"
