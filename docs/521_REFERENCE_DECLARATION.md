# §5.21 Reference Declaration and Catalogue Authorization

Date: 2026-10-03
Authority: UNIT_INSPECTED_MODEL_CORROBORATED for reference_hz;
  UNCALIBRATED_RECEIVER_CONSERVATIVE_BOUND for reference_ppm
  (per `docs/521_REFERENCE_AUTHORITY_AMENDMENT.md` and the accepted
  §5.21 procedure)

## Declaration

    reference_hz  = 28_800_000
    reference_ppm = 100

reference_hz authority: UNIT_INSPECTED_MODEL_CORROBORATED.

reference_ppm authority: UNCALIBRATED_RECEIVER_CONSERVATIVE_BOUND.
Per the accepted §5.21 procedure: "An uncalibrated dongle declares
±100 ppm and earns a cap of 3; a disciplined one declares what its
discipline supports." SN 14530058 has been physically identified but
has not been frequency-calibrated or disciplined; therefore the
protocol's conservative bound applies. reference_ppm is how well
the reference is actually known — the ReferenceComb matching window
(n · f_ref · ppm) — not a hardware quality grade.

Nooelec's ±0.5 ppm TCXO frequency-stability specification is
retained as corroborating hardware specification. It is NOT consumed
as the ReferenceComb tolerance: "frequency stability" does not state
this individual oscillator's absolute error against 28.800000 MHz.
A TCXO is better hardware; that does not automatically make our
knowledge of its absolute error ±0.5 ppm.

## Physical receiver inspected

- NESDR SMArt v5
- FCC ID 2A8GD-SMArt-V5
- PCB revision v5.2c1
- USB descriptor identity 14530058 (no serial number is physically
  present on the unit; identity is bridged by the operator tying
  the inspected unit to its USB descriptor)
- Inspection act: enclosure opened 2026-10-03 by the operator;
  cooling pad removed for board photography; visual inspection
  only — no modification, desoldering, probing, or powered
  operation while open.

## Physical reference evidence

- Y6 oscillator package physically present immediately adjacent to
  the RTL2832U — the documented reference-clock position.
- Package markings preserved verbatim:
      64YB
      288B0
      351D
  (pin-1 indicator present)
- The marking is not independently decoded and is NOT used as the
  sole frequency authority. It is recorded exactly as observed; it
  does not contradict the manufacturer-defined reference (no
  contradictory frequency marking present).
- Photographs (originals preserved; SHA-256 computed at receipt):

      161eb93c2f67c41fdbbf776073031e8980dc9dfa25a5ad3f41bf2017873b6eb9  tcxo_y6_closeup_1.jpg
      2633fbc625f0b58682c420c04d35c8724ab5eb1c729ca3cbd265ddb3b2a99e88  enclosure_exterior.jpg
      b35d65c1738e2f3328dc6f5a56afd72c6394f55c5753f4bd49ee66ae354087b6  tcxo_y6_closeup_2.jpg

  (see `docs/521_reference_evidence/` for the photographs and
  SHA256SUMS)

## Manufacturer corroboration

- Nooelec NESDR SMArt v5 documentation specifies a 28.8 MHz local
  oscillator feeding the RTL2832 / RF-tuner path (authority for
  reference_hz) and a TCXO frequency stability of ±0.5 ppm (max)
  (nesdr_smart_rtl_sdr_v5_datasheet_revision_1.pdf) — the stability
  figure is corroborating hardware specification, not the declared
  reference tolerance.
- FCC filing for FCC ID 2A8GD-SMArt-V5 independently establishes
  the SMArt V5 hardware family.

## Excluded evidence

The observed ~100.800 MHz feature and the arithmetic
100.8 / 28.8 = 3.5 played no role in this declaration.

## Catalogue authorization

With the reference declared under the amended authority, the §5.21
K=64 / R=8 tuner schedule is AUTHORIZED. The catalogue NO-GO
recorded in `docs/521_CATALOGUE_AUTHORIZATION_DECISION.md` is
lifted by this declaration; all other provisions of that decision
record remain in force.

Consequence, per the protocol: at ±100 ppm the harmonic cap is 3,
so there is no reference class above 86.4 MHz — the 400–470 MHz
catalogue cannot produce CONSISTENT_WITH_INTERNAL_REFERENCE
entries by construction. That is the correct behavior for an
uncalibrated receiver. The UHF run still classifies internal-mixing
products and unresolved candidates; it simply does not claim
reference-comb matches it has not earned.

The GPSDO three-frequency experiment (410 / 435 / 465 MHz) remains
the path to a measured per-unit oscillator error and could support
a future, separately declared tighter catalogue — not a post-hoc
reinterpretation of this one.
