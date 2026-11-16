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
4. Antenna-on cost is about 1-3 dB against terminated at the venues tried, so the 11.0 dB floor leaves 1-4 dB of margin in 400-470 MHz (weakest antenna-on good visit 11.6 dB, weakest load-fitted visit in that band
   about 14 dB). The gate refuses a venue-side floor rise correctly (11-15 repeat), and a venue that is marginal will refuse visits; that is the intended behaviour.
5. Clipping stays a hard limit as decided; 1 refusal in about 420 good visits.
6. Hwy 99 is the only venue with a bad day in the evidence; Phinney, 520 128th St and Mukilteo (intermediate set) were each captured once.

## Slice list now
1. visit gate (done, #479). 2. tuning gate (done, #479). 3a. this staging (declaration draft, tier artifact, checker). 3b. owner review and freeze (separate acceptance). 3c. sequencer wiring and launch
authorization (not started).
