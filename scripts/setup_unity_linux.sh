#!/usr/bin/env bash
set -euo pipefail

readonly UNITY_VERSION="${UNITY_VERSION:-6000.3.15f1}"
readonly UNITY_CHANGESET="${UNITY_CHANGESET:-c1aa84e375f6}"
readonly INSTALL_ROOT="${UNITY_INSTALL_ROOT:-/opt/unity}"
readonly EDITOR_DIR="$INSTALL_ROOT/$UNITY_VERSION"
readonly UNITY_URL="https://download.unity3d.com/download_unity/$UNITY_CHANGESET/LinuxEditorInstaller/Unity-$UNITY_VERSION.tar.xz"
readonly REQUIRED_KIB=$((12 * 1024 * 1024))

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
  echo "Unity Linux Editor requires Linux x86_64 for this setup." >&2
  exit 2
fi

if [[ -x "$EDITOR_DIR/Editor/Unity" || -x "$EDITOR_DIR/Unity" ]]; then
  echo "Unity $UNITY_VERSION is already installed at $EDITOR_DIR"
  exit 0
fi

available_kib="$(df -Pk "$(dirname "$INSTALL_ROOT")" 2>/dev/null | awk 'NR == 2 {print $4}')"
if [[ -n "$available_kib" && "$available_kib" -lt "$REQUIRED_KIB" ]]; then
  echo "At least 12 GiB free is required to install Unity; only $((available_kib / 1024 / 1024)) GiB is available." >&2
  exit 3
fi

sudo apt-get update
alsa_package="libasound2"
if apt-cache show libasound2t64 >/dev/null 2>&1; then
  alsa_package="libasound2t64"
fi

sudo apt-get install -y --no-install-recommends \
  ca-certificates curl tar xz-utils \
  "$alsa_package" libatk1.0-0 libc6 libcairo2 libcap2 libcups2 libdbus-1-3 \
  libexpat1 libfontconfig1 libfreetype6 libgcc-s1 libgl1 libglib2.0-0 libglu1-mesa \
  libgtk-3-0 libnspr4 libnss3 libpango-1.0-0 libstdc++6 libx11-6 \
  libxcomposite1 libxcursor1 libxdamage1 libxext6 libxfixes3 libxi6 \
  libxrandr2 libxrender1 libxtst6 zlib1g

sudo mkdir -p "$EDITOR_DIR"
echo "Installing Unity $UNITY_VERSION into $EDITOR_DIR (4.2 GiB download, streamed extraction)..."
if ! curl --fail --location --retry 3 --show-error "$UNITY_URL" | sudo tar -xJ -C "$EDITOR_DIR"; then
  echo "Unity extraction failed. Remove the incomplete directory before retrying: $EDITOR_DIR" >&2
  exit 4
fi

if [[ -x "$EDITOR_DIR/Editor/Unity" ]]; then
  unity_binary="$EDITOR_DIR/Editor/Unity"
elif [[ -x "$EDITOR_DIR/Unity" ]]; then
  unity_binary="$EDITOR_DIR/Unity"
else
  echo "Install completed, but no Unity executable was found under $EDITOR_DIR." >&2
  exit 5
fi

sudo ln -sfn "$unity_binary" /usr/local/bin/unity-editor
echo "Unity installed: $unity_binary"
echo "Next: activate your Unity license, then run ./build_unity_linux.sh"
