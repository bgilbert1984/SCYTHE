#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"

if [ "$#" -gt 0 ]; then
  targets=("$@")
else
  mapfile -t targets < <(git ls-files)
fi

if [ "${#targets[@]}" -eq 0 ]; then
  exit 0
fi

patterns=(
  "API_KEY[[:space:]]*=[[:space:]]*['\"][A-Za-z0-9_-]{16,}['\"]"
  "const[[:space:]]+STADIA_API_KEY[[:space:]]*=[[:space:]]*['\"][^'\"]{16,}['\"]"
  "Cesium\\.Ion\\.defaultAccessToken[[:space:]]*=[[:space:]]*['\"][^'\"]{16,}['\"]"
)

failed=0
for pattern in "${patterns[@]}"; do
  if matches="$(grep -InE --binary-files=without-match -- "$pattern" "${targets[@]}" 2>/dev/null || true)" && [ -n "$matches" ]; then
    printf '%s\n' "$matches"
    failed=1
  fi
done

if [ "$failed" -ne 0 ]; then
  printf '\npreflight_secret_scan: hardcoded token assignment detected. Use .env or runtime window variables instead.\n' >&2
  exit 1
fi

printf 'preflight_secret_scan: ok\n'
