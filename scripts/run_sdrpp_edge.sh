#!/usr/bin/env bash
set -euo pipefail

install_dir=${SCYTHE_SDRPP_INSTALL_DIR:-"$HOME/.local/share/scythe/sdrpp-edge"}
binary="$install_dir/bin/sdrpp"

if [[ ! -x "$binary" ]]; then
    echo "SDR++ edge binary not found at $binary" >&2
    echo "Run scripts/build_sdrpp_edge_alma10.sh first." >&2
    exit 2
fi

export LD_LIBRARY_PATH="$install_dir/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# Default to --autostart so the NESDR begins after the first waterfall layout.
# Set SCYTHE_SDRPP_AUTOSTART=0 for a manual Play click.
args=("$@")
if [[ "${SCYTHE_SDRPP_AUTOSTART:-1}" != "0" ]]; then
    has_autostart=0
    for arg in "${args[@]}"; do
        if [[ "$arg" == "--autostart" ]]; then
            has_autostart=1
        fi
    done
    if ((has_autostart == 0)); then
        args+=(--autostart)
    fi
fi

exec "$binary" "${args[@]}"
