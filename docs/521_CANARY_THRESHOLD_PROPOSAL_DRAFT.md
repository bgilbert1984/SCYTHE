# §5.21 proposal (DRAFT): the canary threshold for antenna-on observation runs

Date: 2026-11-15. Status: PARTLY ACCEPTED. On 2026-11-15 the owner accepted (1) the visit gate "both +-256 kHz sides detected AND min-side excess >= 11.0 dB" and
(2) the tuning gate "every visit passes". Slices 1-2 (section 6) are implemented as pure functions in `rf_canary_gate.py`; the clipping
guard is implemented as written in section 4.3 and the owner did not separately address it. Still open: a second intrinsic offset,
observation-only vs tier-only, the tier revalidation interval, and slice 3 (wiring into the antenna-on mode with a new freeze).
Nothing here is frozen or in force; no declaration, code or test is changed by this file.
Follows `521_INTRINSIC_TIER_ANTENNA_ON_PROPOSAL_DRAFT.md` (P4: canary and clipping are the operative guards, no std limit).
Analysis scripts and the per-visit table: `scripts/s521_canary_threshold/` (`extract.py` -> `visits.json`, `rates.py`, `thr.py`).

## 1. What the canary is
The intrinsic tier (`521_CATALOGUE_TIERS.md`) names fixed baseband offsets that every valid terminated session recovered (today
+-256 kHz). In an antenna-on visit, the canary asks whether the sequencer's own detector still finds those offsets. If it does not,
the visit's ability to detect weak receiver spurs is in doubt and the visit should not be scored. The canary is a property of the
detector and the visit, not a claim about any physical cause.

## 2. Evidence (per visit; a visit = 8 windows at one LO setting; same detector `detect_candidates`)
"Both" = both +-256 kHz sides detected in the visit; "excess" = the smaller of the two sides' `excess_db`.

| set | visits | both detected | 95% Wilson CI | excess dB min / median / max |
|---|---|---|---|---|
| terminated RERUN1 (11-14) | 192 | 192 | 0.980-1.000 | 14.2 / 16.5 / 17.6 |
| antenna-on, quiet venue (520 128th St) | 32 | 32 | 0.893-1.000 | 14.5 / 16.4 / 17.6 |
| antenna-on, Hwy 99, gain 29.0, UHF | 32 | 32 | 0.893-1.000 | 11.6 / 13.6 / 15.7 |
| antenna-on, Phinney Ave N, gain 29.0, UHF (11-15, added after acceptance) | 32 | 32 | 0.893-1.000 | 13.4 / 14.8 / 16.0 |
| antenna-on, Hwy 99 repeat (11-15, 3 days later, same 8 tunings, gain 29.0) | 32 | 12 | 0.229-0.547 | 11.7 / 13.8 / 15.3 (12 detected) |
| strong bands, Hwy 99 (FM, 750, 881 MHz; 29.0/21.5/14.0 dB) | 36 | 5 | 0.061-0.287 | 10.1 / 11.5 / 12.4 |
| intermediate gains, Mukilteo (750, 93.3 MHz) | 32 | 21 | 0.483-0.796 | 10.8 / 15.7 / 17.1 |

Per row (4 visits each): 93.3 MHz at 29.0, 21.5 and 14.0 dB (Hwy 99): 0/4; 99 and 102 MHz at 29.0: 0/4; 881 MHz at 29.0: 0/4; 750 MHz at
29.0 / 21.5 / 14.0: 3/4, 1/4, 1/4; Mukilteo 93.3 MHz at 14.0: 0/4, at 1.5 and -1.0: 4/4; 750 MHz at 14.0 / 11.5 / 9.0 / 6.5 / 4.0: 3/4, 0/4,
2/4, 4/4, 4/4. Clipping was 0.000% in every Mukilteo row.
Zero false-fails in the 224 terminated and quiet visits (rule-of-three 95% upper bound 1.3% per visit).

Phinney row (6015 Phinney Ave N, Seattle, venue owner-stated; same 8 tunings and retune positions as the quiet and Hwy 99 sets, gain 29.0 dB,
E4000): 32/32 pass the merged visit gate (`rf_canary_gate.visit_gate`, both sides, >= 11.0 dB, clipping <= 0.001%). Sample std 0.82 / 0.89 / 1.64 LSB
(min / median / max), worst clipping 0.00005%, median 1,372 candidates per capture. Its std range is low, so it adds a good-venue
data point and tests nothing about saturation. It is not part of the 224/224 and 24/68 counts above, which `test_rf_canary_gate.py` still pins.
Per-visit table: `scripts/s521_canary_threshold/visits_phinney.json` (script `gate_eval_phinney.py`). Archive: `slope_phinney_20261115`,
tar.zst sha256 001ff931f8e0f86b4509e53a17e70646c89cdedf3957cc0df918758deffa4a9a, verified on dedirock.

Hwy 99 repeat (13601 Hwy 99, 2026-11-15, same 8 tunings, retunes and gain as the 11-14 set; venue owner-stated): 12/32 pass the merged visit gate
against 32/32 three days earlier. The 12 passes are the three upper tunings (444, 449, 457 MHz, 4/4 each); the five lower tunings (401-434 MHz) are 0/4,
all `CANARY_NOT_DETECTED` (neither side in 18 visits; the negative side only, at 10.4 and 11.5 dB, in 2). Clipping 0.0% in every visit. Sample std 1.7-2.3 LSB
in the failing tunings against about 1.0 earlier; candidates per capture 56-177 against about 390-620. Spectrum check (`spectrum_hwy99_repeat.py`, 4096-point
averaged PSD, d=0 captures, earlier set vs repeat): in the five failing tunings the median floor is 5.6-6.7 dB higher, total power 5.2-6.3 dB higher, the
strongest peak is only 10.0-12.2 dB above the median (the DC bin) and one bin is more than 10 dB above the median (earlier set: 5-9). No strong narrowband
carrier is present, so one does not explain the failure. The whole spectrum rose with a broadband floor, and the absolute level at +-256 kHz rose by about the same
amount (about -6 dB to about -1 dB). The 444 MHz tuning, which passed, has strong carriers near 445.04 MHz (38 dB over its median, 149 bins above 10 dB) and
std 3.3 LSB. The cause of the floor rise is not established from these files (a broadband emitter and a receiver-state difference both fit); a floor change of this size
in 400-435 MHz only is the kind of event the visit gate refuses. Per-visit table `visits_hwy99_repeat.json`, spectrum numbers `spectrum_hwy99_repeat.json`.
Archive `slope_hwy99_repeat_20261115`, tar.zst sha256 d5c98a2e860d7bf8761dfc03fea24220cb9d8cbf84103ca034a3dfbda1391a88, verified on dedirock.
Consequence for the recommendation: the visit gate's false-pass rate on degraded data is unchanged, but this is the first same-venue case of a good tuning set
turning bad within days, so a gate pass cannot be carried across sessions; each visit needs its own canary. It is not part of the pinned counts in `test_rf_canary_gate.py`.

## 3. Options
- **A. Either side detected.** Passes 51/68 degraded visits (75%); rejected: it barely gates.
- **B. Both sides detected (proposed floor).** Passes 26/68 degraded visits (38%), all 224 terminated/quiet and all 32 weak antenna-on.
- **C. Both sides and min-side excess >= X.** X = 11.0 dB: 224/224, 32/32, 24/68. X = 12.0: 31/32 weak antenna-on (a false-fail), 17/68.
  X = 13.0: 23/32 weak antenna-on, 15/68. Excess separates little: the weak antenna-on minimum (11.6 dB) overlaps the degraded range
  (10.1-12.4 dB, with Mukilteo 750 MHz rows up to 15.8), and antenna-on legitimately costs ~3 dB against terminated.

## 4. Recommendation
1. Visit gate: both +-256 kHz sides detected AND min-side excess >= 11.0 dB (0.6 dB below the weakest weak-band visit; costs
   no false-fails in 256 visits, removes 2 more degraded passes than B).
2. Tuning gate (the stronger control): a tuning is scored only if EVERY visit that contributes to it passes the visit gate. With 3
   visits per tuning and a per-visit degraded pass rate near 0.35-0.40, the all-pass rate is ~5-6% if visits fail independently
   (an assumption this data cannot test). At the 1.3% per-visit false-fail bound the cost is <= ~4% of good tunings.
3. Clipped-sample fraction > 0.001% still refuses the visit (separate guard, unchanged from the P4 proposal).
4. The threshold values are recorded in the frozen antenna-on declaration, not derived at run time, and the intrinsic tier hash is part of
   the chain digest, so a different tier or threshold is a different authorization.

## 5. Limits, stated plainly
- There is no ground truth for "this visit can detect weak spurs". The canary is a proxy tested by its own behaviour in good and
  degraded conditions; a passed visit is not proof of reliability (about 38% of degraded visits still pass the floor; ~35% at X = 11 dB).
- 288 good visits across three venues plus the terminated chain (the 32 Phinney visits are outside the pinned counts), and 68 degraded visits from two venues; the intermediate rows have 4 visits
  each, so per-row rates are coarse.
- Only +-256 kHz is in the tier. One canary offset means one detector path; a failure mode that kills only other offsets goes unseen.
- The per-tuning independence estimate is an assumption. The visits within a tuning were not captured as separate sessions here.
- The 881 MHz row produced only ~146 candidates per visit with 3% clipping and 0/4 canary passes; the detector behaviour there is not understood.
- The weak-band good set was captured at gain 29.0 only. Other gains in quiet bands are untested.

## 6. Slices if adopted
1. A canary function in the sequencer (visit gate) with replay-parity tests; 2. the tuning gate; 3. thresholds in a new antenna-on
authorization freeze together with the intrinsic tier hash. Each its own proposal, acceptance and PR.

## 7. Questions for the owner
- Accept both-sides + 11.0 dB as the visit gate, or prefer the plain "both detected" rule (B)?
- Tuning gate "every visit passes", or a looser rule (for example 2 of 3)?
- Should a second intrinsic offset be sought first (needs more terminated sessions at another gain or rate)?
