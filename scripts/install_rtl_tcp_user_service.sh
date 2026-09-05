#!/usr/bin/env bash
# Install the rtl_tcp boot-capture user service.
#
# Linux side only. This script deliberately does NOT touch the Windows host:
# under WSL the USB device must be attached with usbipd before rtl_tcp can open
# it, and that is a Windows-side decision. See docs/RTL_TCP_BOOT_CAPTURE.md.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$REPO/deploy/systemd/user"
UNIT_DST="$HOME/.config/systemd/user"
ENV_EXAMPLE="$REPO/config/rf-capture.env.example"
ENV_DST="$HOME/.config/scythe/rf-capture.env"

command -v rtl_tcp >/dev/null || { echo "rtl_tcp not found on PATH; install rtl-sdr first." >&2; exit 1; }

mkdir -p "$UNIT_DST/scythe-orchestrator.service.d" "$(dirname "$ENV_DST")"

# The environment file is operator configuration and carries the receiver
# serial, so an existing one is never overwritten.
if [ -e "$ENV_DST" ]; then
  echo "keeping existing $ENV_DST"
else
  cp "$ENV_EXAMPLE" "$ENV_DST"
  echo "installed $ENV_DST from the example"
  echo "  EDIT IT: set SCYTHE_RTL_DEVICE_SERIAL to this receiver's serial."
  echo "  Find it with: rtl_test -t 2>&1 | grep -i 'SN:'"
fi

install -m 0644 "$UNIT_SRC/scythe-rtl-tcp.service" "$UNIT_DST/scythe-rtl-tcp.service"
install -m 0644 "$UNIT_SRC/scythe-orchestrator.service.d/rf-capture.conf" \
                "$UNIT_DST/scythe-orchestrator.service.d/rf-capture.conf"
echo "installed unit and orchestrator drop-in into $UNIT_DST"

# User services only start at boot if lingering is enabled; without it they
# start at first login and die at last logout.
if ! loginctl show-user "$USER" 2>/dev/null | grep -q '^Linger=yes'; then
  echo
  echo "WARNING: lingering is off, so this unit will NOT start at boot."
  echo "  Enable it with: sudo loginctl enable-linger $USER"
fi

systemctl --user daemon-reload
systemctl --user enable --now scythe-rtl-tcp.service
systemctl --user status scythe-rtl-tcp.service --no-pager || true

cat <<'NOTE'

Installed. Two things this script did not and cannot do:

  1. Attach the USB device. Under WSL the receiver does not exist in Linux
     until it is attached from Windows. The unit retries every 5s forever, so
     it will come up on its own within ~5s of the device appearing.

  2. Confirm the sample rate. rtl_tcp never acknowledges the rate it applied,
     so the bridge publishes it as SHARED_LAUNCH_CONFIGURATION with
     runtime_attestation UNAVAILABLE. That is accurate, not a gap to paper over.
NOTE
