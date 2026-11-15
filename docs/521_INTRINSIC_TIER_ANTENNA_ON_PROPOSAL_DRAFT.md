# §5.21 proposal (DRAFT): intrinsic tier as pre-known, antenna-on observation runs

Date: 2026-11-14. Status: PROPOSAL. Nothing here is frozen or in force; no declaration, code or test is changed by this file.
It would need its own acceptance and a new freeze. It does not touch the prohibition on fabricated, inferred or result-selected
references (`521_CATALOGUE_AUTHORIZATION_DECISION.md`), the 100 ppm reference bound, bands, seed or schedule.

## 1. Problem
Every catalogue run at a new venue currently needs the antenna removed and the 50 ohm load refitted, then R1 verifies the load
(`521_TERMINATION_CONTROL.md`). The evidence we hold says that is poor value for the venue-independent part of the catalogue:
- RERUN1/2/3 (3 valid terminated sessions, same schedule digest, different venues and gains): the near-DC cloud (~11k entries) is not
  reproducible beyond a shuffled-tuning null; only a fixed baseband offset of +-256 kHz (fs/8) is. `rf_catalogue_tiers.py`
  (merged, #475) returns exactly -255,996 / +255,996 Hz at occupancy 0.98 / 1.00 / 1.00 (`521_CATALOGUE_TIERS.md`).
- Live antenna-on feasibility test (2026-11-14, `slope_feasibility_20261114`, archived and verified on dedirock; 8 tunings x 4 retune
  positions, gain 29.0 dB E4000): the sequencer's own detector finds +-256 kHz in 64/64 capture-side cells (terminated control 64/64);
  of the other candidates 27,021 stayed at fixed baseband offset, 189 moved with the retune (slope -1, real signals), 3,081 neither.

- Second live antenna-on capture at 13601 Hwy 99, Everett (2026-11-14, `slope_strong_20261114`, archived and verified on dedirock; same 8
  tunings, positions and gain): sample std 0.82 / 1.03 / 2.81 LSB (min / median / max), clipping at most 0.0004% of samples, +-256 kHz
  detected in 64/64 cells, and of the other candidates 15,594 fixed-offset, 1,376 moving (slope -1), 4,433 neither (~670 candidates per
  capture). Same chain, quiet venue vs this one: moving candidates rose 189 -> 1,376 while the +-256 kHz spur stayed 64/64.

- Strong-band antenna-on capture (2026-11-14, `slope_saturation_20261114`, 13601 Hwy 99; FM 93.3/99/102 MHz and cellular 750/881 MHz at
  gain 29.0 dB, plus 21.5 and 14.0 dB at 93.3 and 750 MHz; 4 retune positions each, 8 cells of +-256 kHz per row). +-256 kHz recovered /
  clipping (max over the 4 captures) / sample std:
  29.0 dB: 93.3 MHz 1/8, 45.4%, 102-103 LSB; 99.0 1/8, 16.1%, 65-82; 102.0 2/8, 37.2%, 96-97; 750 7/8, 2.2%, 27-37; 881 0/8, 3.1%, 17-39
  (only 298 candidates per capture);
  21.5 dB: 93.3 2/8, 0.79%, 58-62; 750 5/8, 0.015%, 13-22;  14.0 dB: 93.3 2/8, 0.000%, 25-26; 750 5/8, 0.000%, 5.5-10.
  The weak-band runs above recovered 64/64 at clipping <= 0.0005% and std <= 2.81 LSB.

## 2. What the code already does
`classify_track` decides internal vs external from the retune slope alone: slope 0 -> `CONSISTENT_WITH_INTERNAL_MIXING`, slope -1
with no declared-reference harmonic -> ingress finding (reference cap is 3 harmonics at 100 ppm, none inside 400-470 MHz).
Termination enters only as the declared claim, the chain digest and the `required_confidence` string. So slope separation does not
depend on the load; what the load buys is the claim that persistent entries are receiver-intrinsic with no ambient contribution.

## 3. Proposal
**P1. Intrinsic tier artifact.** Derive `intrinsic_tier.json` with `rf_catalogue_tiers.py` from >= 3 valid terminated sessions of
one chain (tuner family, sample rate, schedule digest). It is hash-pinned and bound to that chain. The 3 existing sessions give the
first artifact for the E4000/2.048 MS/s/schedule d35d706f chain; only +-256 kHz qualifies. All else stays `SESSION_SCOPED`.
**P2. Antenna-on observation run (new mode, new authorization).** A declaration with `termination.mode = ANTENNA_ON_OBSERVATION`
instead of a fitted load, plus the P1 artifact hash. The chain digest includes the mode and the tier hash, so an antenna-on run can
never satisfy a terminated authorization or vice versa. R1 does not apply (it would correctly say NOT_TERMINATED); a saturation and
input-level guard takes its place (P4).
**P3. Classification in that mode.**
- fixed offset on the tier list, slope 0 -> `INTRINSIC_KNOWN` (not newly catalogued);
- slope 0 elsewhere -> `FIXED_OFFSET_UNCONFIRMED`, session-scoped, never reused as terminated evidence;
- slope -1 -> ingress finding, as today;
- ambiguous slope -> unresolved, as today.
**P4. Guards (provisional, from the data above).** Per visit, refuse and do not score when ANY of:
- clipped-sample fraction (0 or 255) > 0.001% (weak-band maximum seen 0.0005%; 0.015% already lost +-256 kHz in 3 of 8 cells);
- sample std > 3.0 LSB (weak-band maximum 2.81 LSB; the lowest level that lost the spur was 5.5 LSB at 14.0 dB / 750 MHz with 0% clipping);
- **canary:** an intrinsic-tier offset (+-256 kHz) is not detected in that visit. Clipping alone is not a sufficient guard: strong FM at 14.0 dB
  clipped 0.000% yet recovered the spur in only 2/8 cells, so loss of the known spur is the direct evidence that scoring is unreliable.
Stream-liveness stays mandatory. These limits bracket a gap: nothing was captured between std 2.81 and 5.5 LSB, so 3.0 is a bound set at
the top of the observed good region, not a measured threshold, and needs an intermediate-gain run before freezing.
**P5. Revalidation.** A new chain (gain family, rate, tuner, hardware) starts with no tier and needs 3 terminated sessions. A
terminated session is also required after any hardware change (cable, XTR unit, case) and is recommended periodically; interval
is an owner decision.

## 4. Limits, stated plainly
- The live tests covered UHF 400-470 MHz at two venues (sample std 0.7-0.8 LSB quiet; 0.82-2.81 LSB at 13601 Hwy 99, clipping <=0.0004%).
  Neither came near saturation at gain 29.0 dB, so strong-signal bands (FM, cellular) and the saturation behaviour are still untested and
  P4's limits cannot be set from this data. The std seen (<=2.81 LSB) only bounds the level a UHF visit reaches at this gain.
- Only 189 moving candidates were seen, concentrated in a few tunings. That shows separation works there, not how it behaves when a
  real signal sits on a spur offset or changes during the visit.
- Antenna-on gave ~949 candidates per capture against ~2,366 per terminated visit; the cause is unexplained and should be understood
  before relying on candidate counts.
- Strong-band data: one venue, one session, 4 captures per row; 881 MHz yielded only ~298 candidates per capture at 3% clipping (the
  detector behaves differently there; not understood). The device also dropped off WSL twice during the attempt (usbipd Shared); the
  final 36 captures were all live and complete.
- Three terminated sessions only; low power in the dense near-DC cloud means a small reproducible subset could hide there.
- Antenna-on evidence is observation, not a terminated catalogue: it must not feed back into the intrinsic tier.

## 5. Not proposed
Removing R1 for terminated runs, relaxing the load/antenna precondition for the existing terminated authorization, changing the
reference bound, or any physical switch (an SPDT with built-in load remains an option if session-adjacent terminated windows prove
necessary).

## 6. Slices if adopted
1. Freeze the intrinsic tier artifact for the E4000 chain. 2. Mode + chain-digest change + `INTRINSIC_KNOWN`/`FIXED_OFFSET_UNCONFIRMED`
classes + tests (replay parity). 3. Saturation guard with limits set from a strong-signal antenna-on run. 4. New antenna-on authorization
freeze. Each its own proposal, acceptance and PR.

## 7. Questions for the owner
- Is observation-only antenna-on evidence wanted, or only the tier (no antenna-on catalogue run at all)?
- Which bands/venues should the saturation limits be set from (a strong-signal run is needed)?
- Revalidation interval for the terminated tier.
- Should the unexplained candidate-count gap be investigated first?
