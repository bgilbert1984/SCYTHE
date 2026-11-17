# §5.21 slice 3, staged for owner review (antenna-on observation-only declaration)

Date: 2026-11-16. Status: STAGED. Nothing here is frozen, registered or in force. No sequencer behaviour changes; the sequencer does not read these files.
The freeze is a separate owner acceptance.

## Owner decisions recorded 2026-11-16
- Clipping: keep the hard per-visit 0.001% limit. The 84-sample refusal (83 would pass; canary healthy at 14.7 / 14.3 dB) is the expected behaviour of a hard threshold: the line sits 2x above the
  highest observed good value (0.0005%) and 15x below the lowest observed bad (0.015%). Revisit only with a measured clipping-vs-spur curve between 0.0005% and 0.015%.
- Slice 3: build and stage, scoped to UHF 400-470 MHz at gain 29.0 dB, thresholds as accepted (both sides, >= 11.0 dB, every visit), tier artifact frozen from the three terminated sessions,
  observation-only.
- 750 MHz, 881 MHz and FM: excluded from slice 3 until the load-fitted residue is understood and an antenna-on set there passes the gate.
- Second intrinsic offset: not first. +-512 kHz (fs/4) is recorded as a WATCH CANDIDATE only (probably the same mechanism as +-256 kHz); a proper second offset can enter later as a new tier version
  after three terminated sessions through the sequencer.
- Evidence scope: observation-only antenna-on evidence.
- Revalidation: 12 months, plus a hardware-change trigger and a canary-failing-under-quiet-conditions trigger. One terminated session re-checks the frozen tier; the full three-session
  re-derivation only if it fails.

## What is staged
| file | what |
|---|---|
| `docs/521_ANTENNA_ON_OBSERVATION_DECLARATION_XTR_E4000.DRAFT.json` | the draft declaration: scope, gate constants, tier binding, revalidation. `status` DRAFT, `authorizes` false. |
| `docs/521_INTRINSIC_TIER_XTR_E4000.DRAFT.json` | the tier artifact built by `rf_catalogue_tiers.py` from the three terminated sessions (RERUN2 11-03, RERUN3 11-11, RERUN1 11-14); `content_sha256` 444a57aa6d8f3caedc39f7949fb0d8d9312e5e9f69a7b33c898a1424fb79e011; offsets -255,996 and +255,996 Hz. |
| `rf_antenna_on_declaration.py` | pure checker: a declaration is accepted only if its mode, gain, tuner, bands, termination statement, gate constants, tier hash and offsets, and revalidation fields are consistent, and `authorizes` is true only for a FROZEN declaration that says so. |
| `test_rf_antenna_on_declaration.py` | tests: the staged draft is consistent and authorizes nothing; out-of-scope bands (FM, 750, 881 MHz), gain, tuner, mode, constants, tier hash and revalidation are refused. |

## Not done, and why
- The checker is not called by the sequencer and no antenna-on run mode exists. Wiring it into the launch path changes how the sequencer accepts declarations, so it should come after the owner has reviewed the
  declaration text and decided on the freeze.
- No launch authorization is drafted. An antenna-on run still needs its own.

## Things to review before freezing
1. The tier's three sessions are not one chain. RERUN2 and RERUN3 share chain digest 93dca6d1... (the 2026-11-03 declaration, gain 29.7 dB, an R820T2 step the E4000 does not have); RERUN1 is chain aa2bca95...
   (E4000, 29.0 dB). All three report tuner type 1 (E4000) and share one schedule digest, which is what the tier tool checks. The XTR clamps 29.7 to a neighbouring step (29.0 is the surveys' value), but the effective
   gain of the two older sessions was not read back. The tier is therefore consistent with the declared scope (gain 29.0) by clamping, not by a verified readback. The owner chose "the three terminated sessions";
   this is the caveat on that choice.
2. The draft names the offsets as the tier's 24 Hz-bin values (+-255,996 Hz); the canary matches them to a 60 Hz tolerance against the detector's candidates (fs/8 = 256,000 Hz).
3. `run_id` is a placeholder (`...-ANTENNA-OBS-DRAFT`); run id, epoch and venue are operational fields, not bound.
4. The margin over the 11.0 dB floor is thin. In 400-470 MHz the weakest good antenna-on visit is 11.6 dB (Hwy 99, 11-14), which is 0.6 dB of margin. Other antenna-on sets: repeat 3 weakest 12.7 dB (1.7 dB),
   Phinney weakest 13.4 dB (2.4 dB), Mukilteo (11-16) weakest 14.4 dB (3.4 dB). Medians of the weaker side are 13.6 dB (Hwy 99, 11-14, 2.6 dB), 14.4 dB (repeat 3) and 14.8 dB (Phinney). Terminated and load-fitted visits in the band sit at 14.2 dB or more.
   Antenna-on costs about 1-3 dB against terminated. A visit near the weakest observed value is within 1 dB of refusal, so a marginal venue will refuse some good visits; the 11-15 repeat shows the gate also refuses a
   real floor rise. The freeze decision should weigh the 0.6 dB figure, since the 11.0 dB value was chosen 0.6 dB below that same visit.
5. Clipping stays a hard limit as decided; 1 refusal in about 420 good visits.
6. Two venues have shown a floor rise confined to 401-435 MHz: Hwy 99 on 11-15 (12/32 pass, about 6 dB; cleared of a receiver cause by a load-fitted check, and 31/32 three days later) and Lynnwood on 11-16 (19/32 pass, 2.0-3.3 dB above the 11-14 Hwy 99 floor; a load-fitted check the same day passed 32/32 at 15.5-17.5 dB, so the receiver is cleared there too). Phinney (32/32), 520 128th St (32/32) and Mukilteo (32/32 on 11-16) were each captured once in 400-470 MHz and were clean, as was Hwy 99 on 11-14. So 401-435 MHz has been refused in part at 2 of 5 venues tried; a run in that band should expect refused tunings at some sites. The upper tunings (444-457 MHz) passed 4/4 at Lynnwood and in every Hwy 99 run.

## Slice list now
1. visit gate (done, #479). 2. tuning gate (done, #479). 3a. this staging (declaration draft, tier artifact, checker). 3b. owner review and freeze (separate acceptance). 3c. sequencer wiring and launch
authorization (not started).
