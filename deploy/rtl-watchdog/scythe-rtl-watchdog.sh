#!/bin/bash
# scythe-rtl-watchdog: functional-health guard for scythe-rtl-tcp.service.
# Runs from a user systemd timer every 60s.
#
# DESIGN (ss-guard hardening, authorized 2026-10-02):
#   Tests FUNCTION, not process existence. A systemd unit can report "active"
#   while rtl_tcp is wedged on a dead USB handle - that false-green state can
#   squander a catalogue epoch, so the guard verifies the stream endpoint:
#     1. listener present on :1234
#     2. short RTL0 session probe: connect, read the 12-byte dongle info,
#        verify the "RTL0" magic, read a bounded chunk of I/Q and require
#        bytes to actually flow.
#   The probe is READ-ONLY: it never sends rtl_tcp commands. Any command
#   write could retune the receiver, which this guard must never do to a
#   healthy experiment.
#
#   Network state is read from /proc/net/tcp[/6], NOT from ss: iproute2's ss
#   is not installed on this host, and ss-based checks silently no-op when
#   the binary is absent (a plain `ss ... | grep` pipeline yields no rows,
#   which reads as "no listener / no clients" - exactly backwards).
#
# HANDS-OFF conditions (exit 0, touch nothing):
#   - a local rtl_sdr holding the dongle -> BUSY
#   - an established TCP client on :1234  -> IN_USE (a capture is streaming;
#     never steal it, never disturb it)
#
# On demonstrated functional failure the guard restarts ONLY
# scythe-rtl-tcp.service, rate-limited to 3 restarts/hour, logging reason and
# timestamps. It never retunes, never alters catalogue declarations, never
# papers over an interrupted visit: any recovery during a live catalogue
# visit invalidates that visit under normal catalogue reacquisition/refusal
# rules (the catalogue runner, not this guard, owns that decision).
#
# Exit codes: 0 healthy-or-intentionally-idle, 1 recovered-after-restart,
#             2 needs human. (scythe-rtl-watchdog.service declares 0,1,2 as
#             success so the timer never enters a failed state over them.)
set -u
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/scythe-watchdog"
RESTART_LOG="$STATE_DIR/restarts.log"
PORT=1234
MAX_RESTARTS_PER_HOUR=3
mkdir -p "$STATE_DIR"
log() { echo "$(date -Is) $1" >> "$STATE_DIR/watchdog.log"; logger -t scythe-rtl-watchdog "$1" 2>/dev/null; }

service_state() { systemctl --user is-active scythe-rtl-tcp.service 2>/dev/null; }

# refresh_net sets SSGUARD_LISTEN (0/1) and SSGUARD_ESTAB (count) from
# /proc/net/tcp[/6]. States: 0A=LISTEN, 01=ESTABLISHED. Ports are hex.
refresh_net() {
  eval "$(python3 - "$PORT" <<'PYEOF'
import sys
port = int(sys.argv[1])
def rows(path):
    try:
        lines = open(path).read().strip().split("\n")[1:]
    except FileNotFoundError:
        return
    for l in lines:
        f = l.split()
        if len(f) < 4:
            continue
        try:
            lp = int(f[1].rsplit(":", 1)[1], 16)
            rp = int(f[2].rsplit(":", 1)[1], 16)
        except ValueError:
            continue
        yield lp, rp, f[3]
listen = False
estab = 0
for path in ("/proc/net/tcp", "/proc/net/tcp6"):
    for lp, rp, st in rows(path):
        if lp == port and st == "0A":
            listen = True
        if st == "01" and (lp == port or rp == port):
            estab += 1
print("SSGUARD_LISTEN=%d SSGUARD_ESTAB=%d" % (listen, estab))
PYEOF
)"
}
listener_present() { [ "${SSGUARD_LISTEN:-0}" = "1" ]; }
client_streaming() { [ "${SSGUARD_ESTAB:-0}" -gt 0 ]; }

# ---- hands-off checks -------------------------------------------------------
if pgrep -x rtl_sdr >/dev/null 2>&1; then
  log "BUSY: local rtl_sdr holds the dongle (pid $(pgrep -x rtl_sdr | tr '\n' ' '))- not touching"
  exit 0
fi
refresh_net
if client_streaming; then
  exit 0  # a client is streaming; never steal it
fi

# ---- functional probe -------------------------------------------------------
# Prints one of: OK | NO_RTL0 | NODATA | NOINFO | FAIL:<ExceptionName>
probe() {
  python3 - "$1" <<'PYEOF'
import socket, sys

def main(to):
    try:
        s = socket.create_connection(("127.0.0.1", 1234), timeout=to)
    except Exception as e:
        print("FAIL:" + type(e).__name__)
        return
    s.settimeout(to)
    try:
        info = b""
        while len(info) < 12:
            c = s.recv(12 - len(info))
            if not c:
                print("NOINFO")
                return
            info += c
        if info[:4] != b"RTL0":
            print("NO_RTL0")
            return
        n = 0
        while n < 262144:
            c = s.recv(262144 - n)
            if not c:
                break
            n += len(c)
        print("OK" if n > 0 else "NODATA")
    except Exception as e:
        print("FAIL:" + type(e).__name__)
    finally:
        s.close()

main(float(sys.argv[1]))
PYEOF
}

SERVICE_STATE=$(service_state)

# Listener check first: distinguishes DOWN (nothing listening) from WEDGE
# (listener up, stream dead). If the service is mid-restart on its own
# (systemd Restart=always after a USB re-attach), give it a grace window
# instead of piling on.
refresh_net
if ! listener_present; then
  if [ "$SERVICE_STATE" = "activating" ]; then
    sleep 10
    SERVICE_STATE=$(service_state)
    refresh_net
  fi
  if ! listener_present; then
    RESULT="NO_LISTENER"
  else
    RESULT=$(probe 6)
  fi
else
  RESULT=$(probe 6)
fi

case "$RESULT" in
  OK) exit 0 ;;  # healthy; stay quiet
  NO_LISTENER|NO_RTL0|NODATA|NOINFO|FAIL:*) ;;  # genuine failure -> restart path
  *)
    log "PROBE INCONCLUSIVE (output: '$RESULT', service $SERVICE_STATE) - not restarting, needs human"
    exit 2 ;;
esac

# Close the race: a capture may have connected between the hands-off check
# and the probe (a queued connect would make our recv time out and look like
# a dead server). Re-verify before touching anything.
refresh_net
if client_streaming; then
  log "CLIENT APPEARED DURING PROBE (was: $RESULT) - a capture is streaming, standing down"
  exit 0
fi

NOW=$(date +%s)
RECENT=$(awk -v c=$((NOW - 3600)) '$1 > c' "$RESTART_LOG" 2>/dev/null | wc -l)
if [ "$RECENT" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
  log "ALERT: functional failure ($RESULT, service $SERVICE_STATE) - restart cooldown ($RECENT/hr), needs human"
  exit 2
fi
log "UNHEALTHY ($RESULT, service $SERVICE_STATE) - restarting scythe-rtl-tcp.service"
echo "$NOW" >> "$RESTART_LOG"
systemctl --user restart scythe-rtl-tcp.service
sleep 8
SERVICE_STATE2=$(service_state)
refresh_net
if ! listener_present; then
  RESULT2="NO_LISTENER"
else
  RESULT2=$(probe 6)
fi
case "$RESULT2" in
  OK) log "RECOVERED after restart (service $SERVICE_STATE2)"; exit 1 ;;
  *)  log "ALERT: restart did not recover stream ($RESULT2, service $SERVICE_STATE2) - needs human"; exit 2 ;;
esac
