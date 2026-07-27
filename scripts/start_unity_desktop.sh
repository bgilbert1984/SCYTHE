#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly STATE_DIR="$REPO_ROOT/.unity-desktop"
readonly SESSION_SCRIPT="$REPO_ROOT/scripts/unity_desktop_session.sh"
readonly PID_FILE="$STATE_DIR/supervisor.pid"
readonly LOG_FILE="$STATE_DIR/desktop.log"
readonly NOVNC_PORT="${UNITY_DESKTOP_PORT:-6080}"

mkdir -p "$STATE_DIR"

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(<"$PID_FILE")"
  if [[ "$existing_pid" =~ ^[0-9]+$ ]] &&
     kill -0 "$existing_pid" 2>/dev/null &&
     tr '\0' ' ' <"/proc/$existing_pid/cmdline" | grep -q 'unity_desktop_session.sh'; then
    echo "Unity desktop is already running (PID $existing_pid)."
  else
    rm -f "$PID_FILE"
  fi
fi

if [[ ! -f "$PID_FILE" ]]; then
  nohup setsid dbus-run-session -- "$SESSION_SCRIPT" >"$LOG_FILE" 2>&1 &
  supervisor_pid="$!"
  printf '%s\n' "$supervisor_pid" >"$PID_FILE"

  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$NOVNC_PORT/vnc.html" >/dev/null 2>&1; then
      break
    fi
    if ! kill -0 "$supervisor_pid" 2>/dev/null; then
      echo "Unity desktop failed to start. Log:" >&2
      tail -n 100 "$LOG_FILE" >&2 || true
      exit 1
    fi
    sleep 0.25
  done

  if ! curl -fsS "http://127.0.0.1:$NOVNC_PORT/vnc.html" >/dev/null 2>&1; then
    echo "noVNC did not become ready. Check $LOG_FILE" >&2
    exit 1
  fi
fi

if [[ -n "${CODESPACE_NAME:-}" &&
      -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" &&
      -x "$(command -v gh 2>/dev/null || true)" ]]; then
  if ! gh codespace ports -c "$CODESPACE_NAME" 2>/dev/null |
       awk -v port="$NOVNC_PORT" '$1 == port { found = 1 } END { exit !found }'; then
    local_tunnel_port="$((NOVNC_PORT + 10000))"
    timeout 5s gh codespace ports forward \
      "$NOVNC_PORT:$local_tunnel_port" \
      -c "$CODESPACE_NAME" >/dev/null 2>&1 || true
  fi
  gh codespace ports visibility \
    "$NOVNC_PORT:private" \
    -c "$CODESPACE_NAME" >/dev/null 2>&1 || true
fi

echo "Local noVNC: http://127.0.0.1:$NOVNC_PORT/vnc.html?autoconnect=1&resize=scale"
if [[ -n "${CODESPACE_NAME:-}" && -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]]; then
  echo "Codespaces URL: https://${CODESPACE_NAME}-${NOVNC_PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}/vnc.html?autoconnect=1&resize=scale"
fi
echo "Keep port $NOVNC_PORT private. Log: $LOG_FILE"
