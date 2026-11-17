# §5.21 antenna-on observation-only freeze: record (2026-11-17)

Registered at the owner's acceptance (2026-11-17), after the staged review in `521_ANTENNA_ON_SLICE3_STAGING.md` (PR #485) and the evidence through PR #488.
Receiver: Nooelec SMArt XTR, SN 71874552, Elonics E4000.

## What was frozen
- `docs/521_ANTENNA_ON_OBSERVATION_DECLARATION_XTR_E4000.json` (file sha256 `94b86ebe99f88c4963d62553516d02af92703f2c4e33af36a56d9dfb032d795c`, pinned in `test_rf_antenna_on_declaration.py`).
  Scope: UHF_LOW and UHF_HIGH (400-470 MHz), gain 29.0 dB, E4000, OBSERVATION_ONLY, no termination claim. Gate: both intrinsic offsets detected and the weaker >= 11.0 dB, match tolerance
  60 Hz, clipping <= 0.001% per visit (hard), a tuning scored only if every visit passes. Revalidation: 12 months plus hardware-change and canary-failing-under-quiet-conditions triggers.
- `docs/521_INTRINSIC_TIER_XTR_E4000.json` (file sha256 `c46f56a6a29026c4c8baf7ce55e195d0e67b5018d95404e88f6ac1e183dd699a`, byte-identical to the staged artifact; internal `content_sha256` `444a57aa...e011`),
  offsets -255,996 and +255,996 Hz, from RERUN2 (2026-11-03), RERUN3 (2026-11-11) and RERUN1 (2026-11-14).
- The declaration differs from the staged draft (sha256 `da92f287249ceb362c1e56599b11eac4b7a1973254ca23fc290dca7064bfec15`) only in: `status` FROZEN, `authorizes` true, the `authorization` text, the tier artifact path (the `.DRAFT`
  name dropped), and the added `accepted_assumptions` and `frozen` blocks. No scope field, constant or threshold changed.

## Accepted assumptions (recorded, not hidden)
1. Margin. The 11.0 dB floor leaves 0.6 dB at the weakest good antenna-on visit observed (Hwy 99, 11-14). At a degraded venue it is effectively zero (Lynnwood: the weakest pass is 11.03 dB, five detected
   visits sit at 10.1-10.9 dB). This affects how many visits are scored; it does not affect the tier, whose offsets pass at occupancy 0.9844 / 1.0 / 1.0.
2. Gain. RERUN2 and RERUN3 declared 29.7 dB, an R820T2 step the E4000 does not have; the receiver clamped it and the effective gain was not read back. RERUN1 declared 29.0 dB (a valid step) and shows both
   offsets at occupancy 1.0, so the tier holds for a 29.0 dB run whichever step the older sessions actually ran at.
3. Bands. 401-435 MHz was refused in part at 2 of 5 venues tried (Hwy 99 11-15, 12/32; Lynnwood 11-16, 19/32), both cleared as antenna-side by load-fitted checks (32/32). A run in that band should expect
   refused tunings at some sites; the per-tuning gate handles it. The upper tunings (444-457 MHz) passed 4/4 at Lynnwood and in every Hwy 99 run.

## What this does NOT do
- It is not a launch authorization. `authorizes: true` marks the declaration as the registered scope for an antenna-on observation-only run. The sequencer does not read the file, no antenna-on run mode exists,
  and a run still needs its own launch authorization.
- It does not cover 750 MHz, 881 MHz or FM. They stay out until the load-fitted residue is understood and an antenna-on set there passes the gate.
- It does not add a second intrinsic offset. +-512 kHz is a watch candidate; a proper second offset can enter later as a new tier version after three terminated sessions.
- It does not change the clipping limit (the owner kept the hard per-visit 0.001% on 2026-11-16).
- A change to any pinned file is a new frozen record, never an edit of this one.

## Still open
Sequencer wiring and an antenna-on launch authorization (slice 3c). The run id in the declaration (`...-ANTENNA-OBS-DRAFT`) is a placeholder; run id, epoch and venue are operational fields chosen at launch and are not bound.
