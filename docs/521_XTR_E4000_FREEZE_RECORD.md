# §5.21 XTR E4000 freeze: record (2026-11-14)

Registered at the owner's instruction (2026-11-14). Receiver: Nooelec SMArt XTR, SN 71874552, Elonics E4000.

## What was frozen
`docs/521_AUTHORIZED_RUN_DECLARATION_XTR_E4000.json` (sha256 pinned in `test_rf_521_e4000_gain.py`), accepted by the sequencer for NEW runs.
Relative to the 2026-11-03 XTR authorization it changes exactly two chain fields: `gain_db` 29.7 -> 29.0 and `receiver.tuner` -> `E4000`
(pinned by test). Seed, bands, reference (28.8 MHz at 100 ppm), termination record and schedule are unchanged. New run id
`521-UHF-XTR-E4K-EPOCH1`.

Why: 29.7 dB is an R820T2 step; the E4000 (`rtl_test -t`, 2026-11-14) has 14 steps (-1.0 ... 42.0 dB), so `rtl_tcp` would clamp it and the
chain hash would name a gain the receiver is not at. With PR #469 the old file already refused at first tuner contact (`TUNER_FAMILY_MISMATCH`),
leaving the XTR with no working authorization.

## What became history-only
- `docs/521_AUTHORIZED_RUN_DECLARATION_XTR.json` (2026-11-03): byte-identical (sha256 pinned), no longer accepted for new runs. It still validates
  replay/reanalysis (`historical=True`) of the RERUN2 spool, which was collected under it.
- `docs/521_AUTHORIZED_RUN_DECLARATION.json` (v5): already history-only since #470.
- A new-run declaration that still carries the old chain is refused `DECLARATION_NOT_AUTHORIZED` with the field differences (gain, tuner) in the message.

## What this does NOT do
- It is not a launch authorization. Acceptance by the sequencer is one pre-contact check; the launch gates still apply.
- `docs/521_FIRST_WINDOW_LAUNCH_PACKAGE.md` is stale: it names the retired v5 (SN 14530058) as the authorized receiver and would NO-GO the XTR's serial
  readback. It needs updating to SN 71874552 / 29.0 dB / `521-UHF-XTR-E4K-EPOCH1` before any run. Not changed here.
- R1 (measured termination control) is not in the freeze. It would amend the freeze as a new frozen record, never an edit of this file.
- The R820T2 gain-table discrepancy (sequencer 23.1/25.0/27.8 dB vs bridge 22.9/25.4/28.0) is unchanged and pinned by test as pre-existing.
