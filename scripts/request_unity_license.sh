#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly OUTPUT_DIR="${UNITY_LICENSE_REQUEST_DIR:-$REPO_ROOT/.unity-license}"

unity_binary="${UNITY_PATH:-}"
if [[ -z "$unity_binary" ]]; then
  unity_binary="$(command -v unity-editor || true)"
fi
if [[ -z "$unity_binary" || ! -x "$unity_binary" ]]; then
  echo "Unity Editor was not found. Run scripts/setup_unity_linux.sh or set UNITY_PATH." >&2
  exit 3
fi

mkdir -p "$OUTPUT_DIR"
log_file="$OUTPUT_DIR/request.log"

(
  cd "$OUTPUT_DIR"
  "$unity_binary" \
    -batchmode \
    -nographics \
    -quit \
    -createManualActivationFile \
    -logFile "$log_file"
)

activation_file="$(find "$OUTPUT_DIR" -maxdepth 1 -type f -name '*.alf' -print -quit)"
if [[ -z "$activation_file" ]]; then
  echo "Unity did not create an .alf file. Check $log_file" >&2
  exit 4
fi

echo "Activation request created: $activation_file"
echo "Upload it at https://license.unity3d.com/manual and download the resulting .ulf file."
echo "Then run: ./scripts/activate_unity_license.sh /path/to/UnityLicense.ulf"
