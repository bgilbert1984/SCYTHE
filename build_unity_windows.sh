#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_PATH="${UNITY_PROJECT_PATH:-$REPO_ROOT/UnityProject}"
readonly BUILD_PATH="${UNITY_BUILD_PATH:-$PROJECT_PATH/Builds/Windows}"
readonly BUILD_NAME="${UNITY_BUILD_NAME:-SCYTHE_RF_Sim.exe}"
readonly LOG_FILE="${UNITY_LOG_FILE:-$BUILD_PATH/build.log}"

find_unity() {
  local candidate

  if [[ -n "${UNITY_PATH:-}" && -x "$UNITY_PATH" ]]; then
    printf '%s\n' "$UNITY_PATH"
    return
  fi

  if candidate="$(command -v unity-editor 2>/dev/null)" && [[ -x "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return
  fi

  for candidate in \
    /opt/unity/*/Editor/Unity \
    /opt/unity/*/Unity \
    "$HOME"/Unity/Hub/Editor/*/Editor/Unity \
    "$HOME"/Unity/Hub/Editor/*/Unity; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return
    fi
  done

  return 1
}

if [[ ! -f "$PROJECT_PATH/ProjectSettings/ProjectVersion.txt" ]]; then
  echo "Not a Unity project: $PROJECT_PATH" >&2
  exit 2
fi

if ! unity_binary="$(find_unity)"; then
  echo "Unity Editor not found. Run scripts/setup_unity_linux.sh or set UNITY_PATH." >&2
  exit 3
fi

unity_binary="$(readlink -f "$unity_binary")"

if [[ ! -d "$(dirname "$unity_binary")/Data/PlaybackEngines/WindowsStandaloneSupport" ]]; then
  echo "Windows Build Support (Mono) is not installed for this Unity Editor." >&2
  exit 4
fi

mkdir -p "$BUILD_PATH"
echo "=== Unity Windows Build ==="
echo "Project: $PROJECT_PATH"
echo "Output:  $BUILD_PATH/$BUILD_NAME"
echo "Editor:  $unity_binary"
echo "Log:     $LOG_FILE"

set +e
"$unity_binary" \
  -batchmode \
  -nographics \
  -quit \
  -projectPath "$PROJECT_PATH" \
  -buildTarget Win64 \
  -executeMethod Scythe.Editor.BuildCommand.BuildWindows \
  -buildPath "$BUILD_PATH/$BUILD_NAME" \
  -logFile "$LOG_FILE"
build_exit_code=$?
set -e

if [[ $build_exit_code -ne 0 || ! -f "$BUILD_PATH/$BUILD_NAME" ]]; then
  echo "Unity build failed (exit $build_exit_code). Last log lines:" >&2
  tail -n 80 "$LOG_FILE" >&2 || true
  exit "${build_exit_code:-1}"
fi

echo "Unity build succeeded: $BUILD_PATH/$BUILD_NAME"
