#!/usr/bin/env bash
set -Eeuo pipefail

readonly DISPLAY_NUMBER="${UNITY_DESKTOP_DISPLAY:-20}"
readonly NOVNC_PORT="${UNITY_DESKTOP_PORT:-6080}"
readonly VNC_PORT="$((5900 + DISPLAY_NUMBER))"
readonly SCREEN_GEOMETRY="${UNITY_DESKTOP_GEOMETRY:-1440x900x24}"
readonly DISPLAY=":$DISPLAY_NUMBER"

declare -a child_pids=()

cleanup() {
  local pid
  for pid in "${child_pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

export DISPLAY
export XDG_CURRENT_DESKTOP="Fluxbox"
export XDG_SESSION_DESKTOP="fluxbox"
export XDG_SESSION_TYPE="x11"
export BROWSER="microsoft-edge-codespaces"

# Codespaces exports this for VS Code helpers, but it makes Electron apps such
# as Unity Hub start as plain Node.js and exit immediately.
unset ELECTRON_RUN_AS_NODE

Xvfb "$DISPLAY" \
  -screen 0 "$SCREEN_GEOMETRY" \
  -nolisten tcp \
  -ac &
child_pids+=("$!")

for _ in $(seq 1 50); do
  [[ -S "/tmp/.X11-unix/X$DISPLAY_NUMBER" ]] && break
  sleep 0.1
done
if [[ ! -S "/tmp/.X11-unix/X$DISPLAY_NUMBER" ]]; then
  echo "Xvfb did not create display $DISPLAY." >&2
  exit 1
fi

dbus-update-activation-environment \
  DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_DESKTOP XDG_SESSION_TYPE \
  2>/dev/null || true

eval "$(gnome-keyring-daemon --start --components=secrets 2>/dev/null || true)"
export GNOME_KEYRING_CONTROL SSH_AUTH_SOCK

fluxbox &
child_pids+=("$!")

x11vnc \
  -display "$DISPLAY" \
  -rfbport "$VNC_PORT" \
  -localhost \
  -forever \
  -shared \
  -nopw \
  -noxdamage &
child_pids+=("$!")

websockify \
  --web=/usr/share/novnc \
  "127.0.0.1:$NOVNC_PORT" \
  "127.0.0.1:$VNC_PORT" &
websockify_pid="$!"
child_pids+=("$websockify_pid")

xdg-settings set default-web-browser microsoft-edge-codespaces.desktop 2>/dev/null || true
xdg-mime default microsoft-edge-codespaces.desktop x-scheme-handler/http 2>/dev/null || true
xdg-mime default microsoft-edge-codespaces.desktop x-scheme-handler/https 2>/dev/null || true
xdg-mime default unityhub.desktop x-scheme-handler/unityhub 2>/dev/null || true

sleep 1
unityhub &
child_pids+=("$!")

echo "Unity desktop started on display $DISPLAY and noVNC port $NOVNC_PORT."
wait "$websockify_pid"
