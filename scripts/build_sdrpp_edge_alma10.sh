#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_dir=${SCYTHE_SDRPP_SOURCE_DIR:-"$repo_root/assets/SDRPlusPlus-master"}
build_dir=${SCYTHE_SDRPP_BUILD_DIR:-"$HOME/.cache/scythe/sdrpp-edge-build"}
install_dir=${SCYTHE_SDRPP_INSTALL_DIR:-"$HOME/.local/share/scythe/sdrpp-edge"}

required_commands=(cmake g++ make pkg-config)
missing=()
for command_name in "${required_commands[@]}"; do
    command -v "$command_name" >/dev/null 2>&1 || missing+=("$command_name")
done
if ((${#missing[@]})); then
    echo "Missing build commands: ${missing[*]}" >&2
    echo "Install the AlmaLinux dependencies documented in docs/SDRPP_EDGE_BRIDGE.md." >&2
    exit 2
fi

if [[ ! -f "$source_dir/CMakeLists.txt" ]]; then
    echo "SDR++ source tree not found at $source_dir" >&2
    exit 2
fi

mkdir -p "$build_dir" "$install_dir"

# Disable every optional SDR++ module first, then enable only the modules in
# SCYTHE's edge signal chain. This avoids unrelated radio SDK dependencies.
cmake_args=()
while IFS= read -r option_name; do
    cmake_args+=("-D${option_name}=OFF")
done < <(sed -nE 's/^option\((OPT_BUILD_[A-Z0-9_]+).*/\1/p' "$source_dir/CMakeLists.txt")

cmake_args+=(
    -DOPT_BUILD_RTL_SDR_SOURCE=ON
    -DOPT_BUILD_NETWORK_SINK=ON
    -DOPT_BUILD_RADIO=ON
    -DOPT_BUILD_FREQUENCY_MANAGER=ON
    -DOPT_BUILD_IQ_EXPORTER=ON
    -DOPT_BUILD_RIGCTL_SERVER=ON
    -DOPT_BACKEND_GLFW=ON
    -DCMAKE_BUILD_TYPE=Release
    "-DCMAKE_INSTALL_PREFIX=$install_dir"
)

cmake -S "$source_dir" -B "$build_dir" "${cmake_args[@]}"
cmake --build "$build_dir" --parallel "$(getconf _NPROCESSORS_ONLN)"
cmake --install "$build_dir"

echo "SDR++ edge build installed at $install_dir"
echo "Launch it with: $repo_root/scripts/run_sdrpp_edge.sh"
