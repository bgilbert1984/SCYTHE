# §5.21 authorization amendment — Nooelec SMArt XTR SN 71874552

Date: 2026-11-03 (drafted in a Claude Code session at the owner's instruction)

## What this adds

`docs/521_AUTHORIZED_RUN_DECLARATION_XTR.json`: a second frozen authorization,
bound to the XTR. The sequencer now accepts a run-time declaration that matches
EITHER authorization on every chain-defining field. The NESDR SMArt v5
authorization (`521_AUTHORIZED_RUN_DECLARATION.json`, SN 14530058, frozen
2026-09-30) is byte-for-byte unchanged.

Carried over unchanged from the NESDR authorization: bands (UHF_LOW 400-440 MHz,
UHF_HIGH 440-470 MHz), gain 29.7 dB, seed, reference 28.8 MHz at 100 ppm, site
prose, epoch.

Changed: sensor_id `71874552`, receiver description, ppm justification, and the
termination record.

## What the owner stated (this session, not independently verified)

- The XTR is cleared for catalogue / corpus-capture contact.
- A termination is fitted on the XTR input.

## What has NOT been done

- No physical inspection of the XTR and no identification of its reference
  oscillator. `reference_ppm = 100` is therefore the accepted
  uncalibrated-receiver conservative bound, NOT a tolerance taken from a
  datasheet. [`521_CATALOGUE_AUTHORIZATION_DECISION.md`: no declaration is made
  from the model specification alone.]
- The termination part is recorded as "generic 50 ohm SMA termination load
  (part number NOT verified)", connector SMA-male carried over from the
  2026-09-29 load and unverified. The run-time declaration must repeat this
  string exactly; amend this file (a new freeze) if the real part differs.
- Site prose is carried over from the NESDR declaration and must be corrected
  by the owner if the XTR is elsewhere.

A tighter reference_ppm needs a new amendment backed by inspection or a
calibrated frequency measurement.
