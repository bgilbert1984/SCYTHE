# §5.21 two-tier catalogue reading (offline; nothing frozen changes)

Date: 2026-11-14. Tool: `rf_catalogue_tiers.py` (revision `tiers-1`), tests: `test_rf_catalogue_tiers.py`.

## Why
Three valid terminated runs (RERUN2 2026-11-03, RERUN3 2026-11-11, RERUN1 2026-11-14) used the same schedule digest at different
venues and gains. Their ~11k near-DC entries do not reproduce beyond a shuffled-tuning null; only a fixed baseband offset
of +-256 kHz (fs/8 at 2.048 MS/s) is present in 63-64 of 64 tunings in all three. A per-venue refit of the load therefore
buys nothing reusable for the near-DC cloud, and the reproducible part needs no new run.

## Tiers
- **INTRINSIC_REPRODUCIBLE**: slope-0 fixed baseband offset with occupancy >= 0.90 of tunings in every session AND
  occupancy at least 0.50 above its local background (median of 20 neighbouring 24 Hz bins) in every session, across >= 3
  sessions with distinct labels and run ids, one schedule digest, one tuner family. The background test stops the dense
  near-DC cloud qualifying by density (its bins reach 0.6-0.9 occupancy by chance).
- **SESSION_SCOPED**: everything else. Not reused across sessions.

Result on the three runs: exactly two offsets, -255,996 and +255,996 Hz (24 Hz bins). Occupancy 0.98 / 1.00 / 1.00.

## Limits (stated, not hidden)
- Three sessions only; low power inside the dense cloud means a small reproducible subset could hide there.
- Inputs must be valid-run catalogues. Stream-liveness failure is invisible in `catalogue.json`; the tool has a
  density backstop (500 entries/tuning; the periodic-stream run had ~1,690) and requires reconciled, no refused visits.
  It refused the 2026-11-13 INVALID run on that backstop.
- The tool changes no frozen declaration, no sequencer behaviour and does not decide whether an antenna-on run may
  treat the intrinsic tier as already known; that needs its own proposal.
- A new chain (different gain family, sample rate, tuner) starts with no intrinsic tier until it has three sessions.
