# §5.21 proposal (DRAFT): the canary threshold for antenna-on observation runs

Date: 2026-11-15. Status: PARTLY ACCEPTED. On 2026-11-15 the owner accepted (1) the visit gate "both +-256 kHz sides detected AND min-side excess >= 11.0 dB" and
(2) the tuning gate "every visit passes". Slices 1-2 (section 6) are implemented as pure functions in `rf_canary_gate.py`; the clipping
guard is implemented as written in section 4.3 and the owner did not separately address it. Still open: a second intrinsic offset,
observation-only vs tier-only, the tier revalidation interval, and slice 3 (wiring into the antenna-on mode with a new freeze).
On 2026-11-16 the owner also decided: keep the hard per-visit clipping limit; proceed with slice 3 scoped to UHF 400-470 MHz at 29.0 dB, observation-only, staged for review (the freeze is a separate acceptance;
see `521_ANTENNA_ON_SLICE3_STAGING.md`); exclude 750 MHz, 881 MHz and FM; do not seek a second intrinsic offset first (+-512 kHz recorded as a watch candidate); revalidate the tier every 12 months plus
the hardware-change and canary-failing-in-quiet triggers.
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
| Hwy 99, 50 ohm load fitted (11-15, after the repeat; floor-rise check) | 32 | 32 | 0.893-1.000 | 15.3 / 16.7 / 17.5 |
| 50 ohm load, strong-band plan control (11-15; FM, 750, 881 MHz; 29.0/21.5/14.0 dB) | 36 | 35 | 0.858-0.995 | 11.1 / 16.4 / 17.5 (35 detected) |
| 50 ohm load, 8 tunings at 21.5 and 34.0 dB (11-15) | 64 | 64 | 0.943-1.000 | 15.8 / 16.9 / 17.8 |
| antenna-on, Hwy 99 repeat 3 (11-16, same 8 tunings, gain 29.0; 31/32 pass the gate) | 32 | 32 | 0.893-1.000 | 12.7 / 14.4 / 15.9 |
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

Hwy 99 antenna-off check (13601 Hwy 99, 2026-11-15, 50 ohm load fitted as stated by the owner and not verified here, same 8 tunings, retunes and gain 29.0 dB,
run directly after the repeat): 32/32 pass the visit gate, weaker-side excess 15.3 / 16.7 / 17.5 dB (the terminated RERUN1 range is 14.2-17.6), sample std 0.72-0.80 LSB,
no clipping, median 1,972 candidates per capture. Median floor of the five tunings that failed in the antenna-on repeat (401, 407, 416, 425, 435 MHz): about -10 to -11 dB with the
load fitted, against -1.9 to 0.1 dB in the repeat and -6.6 to -7.9 dB in the 11-14 antenna-on set. Venue-scoped cause: the failure follows the antenna side. With the load fitted the same
receiver at the same gain gives a flat low floor and clean +-256 kHz spurs, so the broadband rise in 401-435 MHz (about 10 dB above the terminated floor in the repeat, about 3.5 dB on 11-14)
came in through the antenna at that venue and time; its source is not identified, and the earlier spectrum shows no narrowband carrier. This run does not rule out a transient receiver state
during the repeat; it shows the receiver is healthy now. Consequence for slice 3: an antenna-on venue can fail the gate while the receiver is fine, and a failed visit says nothing against the
receiver, which is the intended reading of the canary. Per-visit table `visits_hwy99_antoff.json`, spectrum numbers `spectrum_hwy99_antoff.json` (covers the 11-14, repeat and load-fitted sets).
Archive `slope_hwy99_antoff_20261115`, tar.zst sha256 b5bef5031c331bc3016e3db300542a74df841fe727801fd96ce0ace96daebad8, verified on dedirock.

Hwy 99 repeat 3 (13601 Hwy 99, 2026-11-16, antenna on as stated by the owner, same 8 tunings, retunes and gain 29.0 dB as the 11-14 and 11-15 sets): 31/32 pass the merged visit gate;
both +-256 kHz sides were detected in 32/32 visits (weaker-side excess 12.7 / 14.4 / 15.9 dB), sample std 0.83-1.48 LSB, median 743 candidates per capture. The 401-435 MHz floor
rise of the 11-15 repeat is gone: the median floor of those five tunings is -7.9 to -9.2 dB, against -1.9 to 0.1 dB in the repeat, -6.6 to -7.9 dB on 11-14 and -10.2 to -11.2 dB with the load
fitted, and the strong carrier near 445.04 MHz at 444 MHz is no longer there. So the floor rise was transient, which fits the antenna-side finding above. Per-visit table `visits_hwy99_repeat3.json`,
spectrum numbers `spectrum_hwy99_repeat3.json` (covers the 11-14, 11-15 repeat, repeat 3 and load-fitted sets). Archive `slope_hwy99_repeat2_20261116` (the directory name keeps the label used on
the day), tar.zst sha256 65f5574a22f58cc1c30aed6699efd09bd308d90032340c645cad10e7f3a906b9, verified on dedirock.
The one failure is a clipping refusal, not a canary failure: `t056_d+0.u8` (457 MHz) clipped 84 of 8,388,608 samples (0.0010014%) against the 0.001% limit, with its canary at 14.7 / 14.3 dB. The limit trips
at 84 samples, and 83 would have passed. The limit is the one guard of the visit gate that the owner has not separately addressed (section 4.3), and it is a hard per-visit threshold, so a healthy visit can be
refused by one sample count at the edge. The gate is left as implemented; the options (keep, or a margin, or a rate over the whole tuning) are the owner's call and need a separate proposal.
In the other 11-15 captures (Phinney, the Hwy 99 repeat, the antenna-off check and the load-fitted sets) clipping stayed at or below 0.00005% in every visit, so this is the only clipping refusal in those sets.

Load-fitted controls (2026-11-15, 50 ohm load fitted as stated by the owner and not verified here; feasibility only, not catalogue runs; per-visit table
`visits_load_fitted.json`, script `analyse_load_fitted.py`; archives `slope_load_strong_20261115` tar.zst sha256 db25e27b5640bcddc89dd36f8590eab171a5fa8cbba2f5cd161c15821a7fb95a and
`slope_load_gains_20261115` tar.zst sha256 80e1b03ac628b169869940b24ad3226b874309c0a14d21b88f99ed4c799cfe9d, both verified on dedirock).
- Strong-band control: the 11-14 strong-band plan (93.3, 99, 102, 750, 881 MHz at 29.0 dB; 93.3 and 750 MHz at 21.5 and 14.0 dB) with the load fitted passes 35/36, against
  5/36 with the antenna on. FM rows (93.3, 99, 102 MHz at 29.0 dB): 4/4 each, excess 16.2-17.5 dB. 750 MHz at 21.5 and 14.0 dB: 4/4, 14.3-17.0 dB. So the FM-band, gain-29 failures
  in the 11-14 set came in through the antenna. The 881 MHz row passes 4/4 but with a median of 1,011 candidates (146-298 antenna-on). Residue that is not antenna-side: with the load
  fitted the 750 MHz row (29.0 dB) and the 881 MHz row still show sample std 1.2-1.5 LSB (about 0.75 elsewhere) and weaker-side excess 11.1-13.5 dB (about 16-17 elsewhere), and one 750 MHz
  visit fails (`CANARY_NOT_DETECTED`, std 1.78). The cause is not identified (pickup around the load and a receiver response at those frequencies both fit). Clipping 0.0% in every visit.
  Consequence: the visit gate's 11.0 dB floor sits only 0.1 dB below the weakest load-fitted visit in this row (11.1 dB); at 750 and 881 MHz it is close to false-failing a terminated visit, which
  the 224 terminated visits of RERUN1 (all at about 14-18 dB) did not show because they did not include those bands.
- Gains 21.5 and 34.0 dB, same 8 tunings and retunes: 64/64 pass, excess 16.2-17.8 dB (21.5) and 15.8-17.6 dB (34.0), std about 0.73, no clipping. So the canary holds at the other gains an antenna-on
  venue is most likely to need. Fixed offsets (present in at least 90% of the 32 visits at each gain, excess at least 8 dB, ignoring |offset| under 20 kHz): +-256 kHz at 100% at both gains;
  +-512 kHz (fs/4) at 94% at 21.5 dB and +512 kHz only at 94% at 34.0 dB. +-512 kHz is twice +-256 kHz, so it is probably the same mechanism, not an independent second offset, and it sits just above the
  tier's 0.90 occupancy line. The filter is looser than the tier's rule and the sets are single sessions, so none of this enters the tier; a second offset still needs terminated sessions through the sequencer.
  The near-DC region (a few hundred bins within +-12 kHz) also passes the loose filter and is the known session-scoped near-DC region, so it was ignored.

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
