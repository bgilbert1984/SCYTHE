#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /path/to/UnityLicense.ulf" >&2
  exit 2
fi

license_file="$(realpath "$1")"
if [[ ! -r "$license_file" ]]; then
  echo "License file is not readable: $license_file" >&2
  exit 2
fi

unity_binary="${UNITY_PATH:-}"
if [[ -z "$unity_binary" ]]; then
  unity_binary="$(command -v unity-editor || true)"
fi
if [[ -z "$unity_binary" || ! -x "$unity_binary" ]]; then
  echo "Unity Editor was not found. Run scripts/setup_unity_linux.sh or set UNITY_PATH." >&2
  exit 3
fi

log_file="${TMPDIR:-/tmp}/unity-license-activation.log"
"$unity_binary" \
  -batchmode \
  -nographics \
  -quit \
  -manualLicenseFile "$license_file" \
  -logFile "$log_file"

echo "Unity license imported. Log: $log_file"
