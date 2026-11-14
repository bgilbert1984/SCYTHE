# §5.21 R1: measured termination control

Status: implemented 2026-11-14 (`rf_termination_control.py`, sequencer hook, tests). Limits are VENUE-SCOPED (see §5).

## 1. What it is for
The catalogue only separates receiver-intrinsic products from real signals if the input is terminated. Until now "terminated" rested on the operator's
statement. This control MEASURES it, on the same chain the run uses (rtl_tcp, E4000, gain 29.0), and leaves a hashed artifact the sequencer verifies before
any catalogue tuner contact. It tells a connected antenna from a terminated input. It does not identify the load and cannot detect a partial or loose termination.

## 2. What it measures
At each reference centre it reads two windows (liveness-checked) and records (a) the sample standard deviation in LSB (I and Q together), (b) the averaged-spectrum
peak-to-median ratio in dB (4096-point Hann segments, DC +-50 kHz and the outer 20% of the band excluded). Verdict per centre: `TERMINATED` if both are inside the limits,
`NOT_TERMINATED` if not, `UNCALIBRATED` if the centre has no limits, `STREAM_NOT_LIVE` if the window is a replayed/constant stream. The run verdict is `TERMINATED` only if every centre is.

## 3. Centres and limits (`TERMINATED_LIMITS`)
| Centre | max std (LSB) | max peak/median (dB) | Terminated seen | Antenna on seen |
|---|---|---|---|---|
| 93.3, 99.0, 102.0 MHz (FM) | 1.5 | 15 | std 0.73-0.83, p/m 4.7-9.7 | std 48-104, p/m 25-34 |
| 750 MHz | 4.0 | 20 | std 1.18-2.25 | std 27-38 |
| 881 MHz | 4.0 | 20 | std 1.15-1.32 | std 23-29 |

Dropped after measurement: 445.9 and 462.5 MHz (antenna-on std was sometimes 0.7, indistinguishable from terminated), 632 MHz (the terminated input itself leaks ambient LTE,
std 1.8-4.9, fluctuating), 90/96/105 MHz (not needed once 93.3/99/102 separate by >30x), 1940 MHz (kept out for simplicity).

## 4. Evidence
- Calibration: 8 repeats terminated (load fitted, 132 128th St SW) and 8 repeats antenna-on (520 128th St SW Ste# A3), 12 candidate centres, gain 29.0, E4000 SN 71874552.
- Applied to the calibration data: 8/8 terminated pass, 8/8 antenna-on fail (circular: the same data set the limits).
- Held out: fresh antenna-on reads through the real tool, 3/3 `NOT_TERMINATED`; fresh terminated reads at the second venue, 4/4 `TERMINATED` with the first limits, then 3/3 after the 750/881 MHz
  std limit was widened 3.0 -> 4.0 (the second venue's terminated std at 750 MHz reached 2.25, against 1.37 at the first).
- End to end: the sequencer hook accepts a real terminated artifact, refuses one measured at another gain (`TERMINATION_CONTROL_CHAIN_MISMATCH`) and refuses the antenna-on artifact (`..._NOT_TERMINATED`).

## 5. Limits of this control
- Venue-scoped: ambient LTE leaks into the unit even when terminated and FM stations change by place. The terminated side must be re-validated at a new venue (three runs, ~2 minutes) before the limits are relied on.
  The antenna-on side needs repeating only if the ambient environment is very different. Two venues is a small sample.
- Detects antenna versus terminated only. A partial, open or loose termination is not tested (no such state was measured).
- It checks the input, not which load is fitted; the load part number stays "NOT verified".
- The artifact must be less than one hour old and is bound to receiver, tuner, gain and the current limits. Changing the limits invalidates older artifacts.

## 6. How to use
1. Power down, fit the load, re-attach (`usbipd attach --wsl --busid <id>`), check the serial, start rtl_tcp (operator step).
2. `python3 rf_termination_control.py --sensor-id 71874552 --family E4000 --gain 29.0 --out CONTROL.json` (exit 0 only on `TERMINATED`; it prints the file's SHA-256).
3. Put `"termination_control": {"path": "CONTROL.json", "sha256": "<that hash>"}` in the run declaration. `run_epoch` verifies it before any tuner command, copies it to
   `termination_control.json` in the evidence directory, and the README states "verified". Without the field the run still works and the README says "NOT PROVIDED; the termination rests on the operator's statement only".
Refusal codes: `TERMINATION_CONTROL_HASH_MISMATCH`, `_ALTERED`, `_REVISION`, `_NOT_TERMINATED`, `_CHAIN_MISMATCH`, `_LIMITS`, `_STALE`.
Making the control mandatory for the E4000 freeze would be a new freeze and is the owner's decision; it is not done here.
