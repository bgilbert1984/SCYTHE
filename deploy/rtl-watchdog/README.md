# rtl_tcp functional-health guard (ss-guard)

Protects the §5.21 catalogue experiment's IQ source on scythe-1
(`scythe-rtl-tcp.service`, NESDR SMArt v5 SN 14530058) against the
false-green failure mode: systemd reports the unit `active` while `rtl_tcp`
is wedged on a dead USB handle and no I/Q flows. A squandered epoch is the
cost of missing it; a disturbed healthy experiment is the cost of a sloppy
guard. This guard is built to avoid both.

## What it checks

1. **Hands off when busy.** A local `rtl_sdr` holding the dongle, or an
   established TCP client on :1234 (a capture streaming), means the guard
   exits without touching anything.
2. **Listener present** on :1234 — distinguishes DOWN (nothing listening)
   from WEDGE (listener up, stream dead). If the service is mid-restart on
   its own (systemd `Restart=always` after a USB re-attach), the guard
   grants a 10 s grace window instead of piling on.
3. **RTL0 session probe** (read-only): connect, read the 12-byte dongle
   info, verify the `RTL0` magic, read a bounded chunk of I/Q and require
   bytes to actually flow. `systemctl active` plus a listening socket cannot
   catch the wedge; RTL0 plus flowing bytes can.
4. **The probe emits no tuner commands.** A healthy experiment is never
   retuned or otherwise mutated by a health check. The test suite asserts
   the guard sends zero bytes (test 1).

Network state is read from `/proc/net/tcp` and `/proc/net/tcp6`
(`ss(1)` is not installed on scythe-1; an `ss`-based check silently no-ops
there — see PROVENANCE.md).

A re-check for a newly-connected client between the hands-off check and the
probe closes the race where a just-started capture could look like a dead
server.

## On failure

- Restarts **only** `scythe-rtl-tcp.service`.
- Rate-limited: max 3 restarts/hour, then `ALERT` + stand down (exit 2).
- Logs reason + ISO-8601 timestamps to
  `~/.local/state/scythe-watchdog/watchdog.log`.

## What it will not do

- Retune a healthy receiver.
- Alter catalogue declarations.
- Silently continue an interrupted catalogue visit.
- Convert a failed or interrupted visit into accepted evidence.

Any guard recovery during a live catalogue visit invalidates that visit;
normal catalogue reacquisition/refusal rules govern (owned by the catalogue
runner, not this guard).

## Layout

| Repo path | Deployed path (scythe-1) |
|---|---|
| `deploy/rtl-watchdog/scythe-rtl-watchdog.sh` | `~/bin/scythe-rtl-watchdog.sh` |
| `deploy/systemd/user/scythe-rtl-watchdog.service` | `~/.config/systemd/user/scythe-rtl-watchdog.service` |
| `deploy/systemd/user/scythe-rtl-watchdog.service.d/override.conf` | `~/.config/systemd/user/scythe-rtl-watchdog.service.d/override.conf` |
| `deploy/systemd/user/scythe-rtl-watchdog.timer` | `~/.config/systemd/user/scythe-rtl-watchdog.timer` |

The timer fires every 60 s (`OnUnitActiveSec=60s`). Exit codes 0/1/2 are all
declared success so the timer never enters a failed state over them.

## Tests

`python3 tests/test_ssguard.py` — drives the exact deployed script against
fake `rtl_tcp` servers and a fake `systemctl`:

- healthy → no action, exit 0, zero bytes sent
- listener absent → restart → recovery, exit 1
- RTL0 magic but no I/Q (the wedge) → restart → recovery, exit 1
- 3 restarts/hour exhausted → ALERT, no restart, exit 2

Requires 127.0.0.1:1234 free; the suite skips otherwise (e.g. on scythe-1
itself, where the real `rtl_tcp` lives — the tests must never touch it).
