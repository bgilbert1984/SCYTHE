#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PID_FILE="$REPO_ROOT/.unity-desktop/supervisor.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "Unity desktop is not running."
  exit 0
fi

supervisor_pid="$(<"$PID_FILE")"
if [[ ! "$supervisor_pid" =~ ^[0-9]+$ ]] ||
   ! kill -0 "$supervisor_pid" 2>/dev/null ||
   ! tr '\0' ' ' <"/proc/$supervisor_pid/cmdline" | grep -q 'unity_desktop_session.sh'; then
  rm -f "$PID_FILE"
  echo "Removed a stale Unity desktop PID file."
  exit 0
fi

process_group="$(ps -o pgid= -p "$supervisor_pid" | tr -d ' ')"
if [[ "$process_group" == "$supervisor_pid" ]]; then
  kill -TERM -- "-$process_group"
else
  kill -TERM "$supervisor_pid"
fi

for _ in $(seq 1 40); do
  kill -0 "$supervisor_pid" 2>/dev/null || break
  sleep 0.25
done

rm -f "$PID_FILE"
echo "Unity desktop stopped."
