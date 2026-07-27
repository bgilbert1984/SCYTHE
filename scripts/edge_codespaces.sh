#!/usr/bin/env bash
set -Eeuo pipefail

readonly EDGE_PROFILE="${UNITY_EDGE_PROFILE:-${XDG_CONFIG_HOME:-$HOME/.config}/microsoft-edge-unity}"

mkdir -p "$EDGE_PROFILE"
unset ELECTRON_RUN_AS_NODE

exec /usr/bin/microsoft-edge-stable \
  --no-first-run \
  --password-store=basic \
  --disable-gpu \
  --disable-dev-shm-usage \
  --disable-features=msEdgeFirstRunExperience \
  --user-data-dir="$EDGE_PROFILE" \
  "$@"
