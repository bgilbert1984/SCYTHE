#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME=scythe-ftp.service
SRC="$DIR/$SERVICE_NAME"
DST="/etc/systemd/system/$SERVICE_NAME"

if [ ! -f "$SRC" ]; then
  echo "Service file not found: $SRC"
  exit 1
fi

echo "Installing $SERVICE_NAME to $DST (you will be prompted for sudo)..."
sudo cp "$SRC" "$DST"
sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"
echo "Service installed and started. Check status with: sudo systemctl status $SERVICE_NAME"

echo "If running inside GitHub Codespaces or an environment without systemd, this will fail. Instead, run the server manually using ./start_ftp.sh"
