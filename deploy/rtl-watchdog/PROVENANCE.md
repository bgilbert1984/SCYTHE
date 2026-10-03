# Provenance: ss-guard watchdog

## Deployment record

- **Deployed:** 2026-10-02 ~19:35 PDT on scythe-1
  (`~/bin/scythe-rtl-watchdog.sh`), replacing the earlier liveness
  watchdog (backed up on-host at
  `~/bin/scythe-rtl-watchdog.sh.bak-20261002`).
- **Deployed script SHA-256:** recorded below; the repo copy is
  byte-identical (verified at packaging time).
- **Invocation:** user systemd timer `scythe-rtl-watchdog.timer`, every
  60 s (`OnBootSec=2min`, `OnUnitActiveSec=60s`); oneshot service, exit
  codes 0/1/2 declared success via override.
- **Authorization:** operator GO 2026-10-02 ("ss-guard watchdog hardening:
  GO"), to land before the §5.21 three-day catalogue experiment.

## Live verification (2026-10-02, scythe-1)

All three paths exercised against the real service before codification:

- healthy stream → exit 0, silent, no restart;
- service stopped → `NO_LISTENER` → restart → `RECOVERED`, exit 1;
- 3 restarts/hour seeded → `ALERT`, no restart, exit 2.

## Known incident: the missing-`ss` false alert

The first ss-guard revision (deployed 19:35 PDT) checked listener/client
state with `ss(1)`. `ss` is **not installed** on scythe-1 (minimal
AlmaLinux WSL2); the `ss ... | grep` pipelines silently yielded no rows,
so `listener_present` was always false. The guard reported `NO_LISTENER`
on a healthy service, restarted it once, and logged `ALERT` when the
(healthy) re-probe "failed" the same way — 2026-10-02 19:34:41 and
19:34:49 -0700 in `~/.local/state/scythe-watchdog/watchdog.log`.

The pre-hardening script's `ss`-based established-client hands-off had been
silently dead for the same reason: it could never observe a streaming
client.

Fix: network state is read from `/proc/net/tcp` and `/proc/net/tcp6`
directly (no iproute2 dependency). The false `ALERT` is annotated as such
in the on-host watchdog log. Second revision deployed ~19:36 PDT; this
repo codifies the second revision byte-for-byte.

## Authority

- Decision: §5.21 authorization record
  (`docs/521_CATALOGUE_AUTHORIZATION_DECISION.md`),
  "rtl_tcp reliability hardening: AUTHORIZED".
- The guard may not retune, alter declarations, or paper over interrupted
  visits; any recovery during a live visit invalidates that visit.

## Script identity

```
SHA-256(scythe-rtl-watchdog.sh) = 597b88557e6a5f19c259cacd7cbca30ad1e9ca3f6e8e312cebe555c324d8f5e0
```

(Computed from the deployed file on scythe-1 at packaging time; re-verify
with `sha256sum ~/bin/scythe-rtl-watchdog.sh` on scythe-1.)
