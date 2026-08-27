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
exec "$binary" "$@"
