# §5.21 XTR ppm calibration — plan skeleton (DRAFT, not frozen)

`docs/521_XTR_PPM_CALIBRATION_PLAN_DRAFT.json` (schema `scythe.ppm_calibration_plan.draft`) is a template that encodes in one place what
`rf_ppm_calibration.check_plan` requires for an `lte_cp_pss` plan. It is **not** a frozen plan: it has no hash, `check_plan` refuses it
as it stands (blank carriers), and nothing in it authorizes a run. Every blank is `null`. Freezing is a separate act after the survey.

## Order of work (procedure section 4)
1. Antenna on the XTR (receive only). Survey first; **stop if fewer than 3 usable carriers**.
2. For each usable cell record in a carrier row: `name`, `operator`, `channel` (EARFCN), `band`, `nominal_hz` and `nominal_source`
   (from F_DL = F_DL_low + 0.1 (N_DL - N_Offs-DL), TS 36.101 5.7.3, showing the numbers), `search_hz`, `duplex` + `duplex_source`
   (Table 5.5-1), `cp` = "normal" + `cp_source`. `feature_offset_hz` stays 0 for LTE. At least 3 carriers across the span (about
   700-800 MHz, 1.8-1.9 GHz, one between). Drop rows you do not use; keep >= `min_carriers`.
3. Fill the remaining `null`s (below), run `rf_ppm_calibration` freeze, and only then plan sessions.

## Left blank on purpose (decide at freeze)
- `gain_db`. Catalogue runs and both frozen declarations use 29.7 dB, which is a step in the sequencer's R820T2 gain table (29.0 is not
  in that table). Whether the calibration should use the same gain is the owner's choice; it must be a table member.
- `precision_ppm`: the rounding of the proposed `reference_ppm` (procedure section 7).
- `validation.seed`.
- Carrier rows (all fields).

## Stated, with caveats
- `estimator`: `pss_definition_confirmed` is **false** until the second reader of A1-A3/A7 is recorded and Check B passes. While false,
  `combine_sessions` blocks any proposal (the guard only requires the bool).
- `reference_accuracy`: 0.05 ppm holds for **Wide Area** BS only (TS 36.104 6.5.1). If any cell is Medium Range/Local Area use 0.1 ppm
  (Home 0.25). `confirmed` is false until a human second reader checks the table; an unconfirmed figure blocks any proposal.
- `thresholds`: the procedure's defaults, all stated (chi-square [0.3, 3], |a| <= 5 u(a) and <= 500 Hz, consistency_k 3, max expanded
  uncertainty 5 ppm). `se_floor_hz` 5.0 is the **test fixture's value**, not justified for real data: the estimator's observed
  synthetic error is 15-30 Hz (about 0.02-0.04 ppm at 700-1900 MHz), so revisit it against the synthetic validation at freeze.
  `validation` values (trials 5, 10 dB, bias limit 0.05 ppm) are also the fixture's.
- `plan_note`: the section 4.1 wording. The freeze hashes it with the plan.

## Gates: keep the defaults, disclose the rate (owner decision 2026-11-06)
Over 8 seed offsets of the synthetic end-to-end test, 17 of 24 sessions were accepted (about 29% false reject per session) and the
decision gate permitted an amendment 3 of 8 times. That is a simulator measurement, not a real-session rate, so the gates are not retuned
on it. A false reject costs a re-runnable session; a false accept could bake a biased ppm into a new frozen authorization. Sequence:
freeze with the defaults, disclose the figure in the evidence README, run real sessions, measure the actual reject rate, retune only
with real-session evidence as a separate decision. Illustrative planning only: at 29% per session, 3 sessions all pass about 36% of the
time; 5 sessions give at least 3 accepted about 85% of the time. Budget sessions accordingly.

## Not covered
Per-session termination-removal statement (procedure section 5; `rf_ppm_calibration_capture.py` requires it and records it verbatim) is a
per-session owner act, not granted here. Nothing here concerns Stage 1 or any driven source. Receive only.
