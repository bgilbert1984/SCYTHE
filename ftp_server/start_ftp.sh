#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Default FTP_DIR to pcapng subdirectory
FTP_DIR="${FTP_DIR:-$(pwd)/pcapng}"
export FTP_DIR

# Default to anonymous access
FTP_ALLOW_ANONYMOUS="${FTP_ALLOW_ANONYMOUS:-true}"
export FTP_ALLOW_ANONYMOUS

echo "Starting FTP server (anonymous, blank password) on port ${FTP_PORT:-2121}" 
exec python ftp_server.py
