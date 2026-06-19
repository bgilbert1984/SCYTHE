#!/usr/bin/env bash
set -euo pipefail

dest_dir="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
source_dir="${SCYTHE_SOURCE_DIR:-${1:-/home/spectrcyde/NerfEngine}}"

if [ ! -d "$source_dir" ]; then
  printf 'source directory does not exist: %s\n' "$source_dir" >&2
  exit 1
fi

if [ ! -d "$dest_dir/.git" ]; then
  printf 'destination is not a git repository: %s\n' "$dest_dir" >&2
  exit 1
fi

files=(
  ".env.example"
  "command-ops-visualization.html"
  "rf_scythe_home.html"
  "rf_scythe_api_server.py"
  "scythe_orchestrator.py"
  "pcap_ingest.py"
  "writebus.py"
  "test_pcap_ingest_writebus.py"
  "test_writebus.py"
  "assets/n2yo.py"
  "assets/js/scythe_transport.js"
  "assets/js/shared_auth.js"
  "registries/__init__.py"
  "registries/detection_registry.py"
  "registries/pcap_registry.py"
  "registries/recon_registry.py"
  "styles.css"
  "network-visualization.css"
  "missile-operations.css"
  "urh-integration.css"
  "unified-render-scheduler.js"
)

source_targets=()
for rel in "${files[@]}"; do
  src="$source_dir/$rel"
  if [ ! -f "$src" ]; then
    printf 'skip missing source: %s\n' "$rel" >&2
    continue
  fi
  source_targets+=("$src")
done

if [ "${#source_targets[@]}" -eq 0 ]; then
  printf 'no source files found\n' >&2
  exit 1
fi

"$dest_dir/tools/preflight_secret_scan.sh" "${source_targets[@]}"

copied=()
for rel in "${files[@]}"; do
  src="$source_dir/$rel"
  dst="$dest_dir/$rel"
  if [ ! -f "$src" ]; then
    continue
  fi
  mkdir -p "$(dirname "$dst")"
  cp -p "$src" "$dst"
  copied+=("$rel")
done

if [ "${#copied[@]}" -eq 0 ]; then
  printf 'no files copied\n' >&2
  exit 1
fi

"$dest_dir/tools/preflight_secret_scan.sh" "${copied[@]}"

printf 'synced %d files from %s\n' "${#copied[@]}" "$source_dir"
printf 'review with: git status --short\n'
