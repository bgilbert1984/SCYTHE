# §5.21 NESDR SMArt v5 authorization: RETIRED (DRAFT PROPOSAL until merged and accepted)

Date: 2026-11-14 (drafted in a Claude Code session at the owner's request)
Receiver: NESDR SMArt v5, SN 14530058. Permanently dead (owner-stated 2026-10-11).

## What changes
- `check_declaration_authorized` refuses any declaration whose `receiver.sensor_id` is 14530058 with `RECEIVER_RETIRED`, before any
  schedule generation or tuner contact. `run_epoch` uses this default.
- Replay and reanalysis of evidence already collected pass `historical=True` (`reanalyze_from_spool`, `rf_spur_replay_processor`).
  The v5 authorization keeps validating its own spooled runs; it cannot authorize a new one.

## What does not change
- `docs/521_AUTHORIZED_RUN_DECLARATION.json` stays **byte-identical** (sha256 pinned in `test_rf_521_v5_retirement.py`). It is not deleted;
  deletion is a later, separate slice (deprecate before delete).
- The per-receiver `sensor_id` binding stays: it is what refuses a run on the wrong receiver.
- The 259 v5 catalogue entries stay v5-labelled; they cannot satisfy anything for the XTR (different receiver, tuner and reference).

## Deliberately NOT in this slice
- The older XTR authorization (`521_AUTHORIZED_RUN_DECLARATION_XTR.json`, 29.7 dB, no tuner field) remains in the accepted set. On the real E4000 it
  already refuses at first contact (tuner-family mismatch, PR #469). Retiring it belongs with registering the E4000 freeze, so the accepted set
  is never empty by accident and the owner decides when the replacement takes effect.
