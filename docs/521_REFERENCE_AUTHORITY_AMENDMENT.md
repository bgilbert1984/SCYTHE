# §5.21 Reference Authority Amendment: UNIT_INSPECTED_MODEL_CORROBORATED

Date: 2026-10-03
Status: adopted by operator decision
Supersedes: the "Smallest sufficient path to GO" in
  `docs/521_CATALOGUE_AUTHORIZATION_DECISION.md` (2026-10-02),
  insofar as that path required the physical package marking to
  unambiguously spell out the oscillator frequency. That path
  remains *sufficient*; it is no longer *necessary*.

## What prompted this amendment

On 2026-10-03 the operator physically opened the actual receiver
(NESDR SMArt v5, USB descriptor identity 14530058), removed the
cooling pad, and photographed the board. The reference oscillator
package at Y6 — immediately adjacent to the RTL2832U, the
documented reference-clock position — bears manufacturing codes,
not a legible frequency:

    64YB
    288B0
    351D

Two independent readings (photographic macro and the operator's own
magnified in-hand reading) agree: the marking cannot be decoded to a
frequency from any credible manufacturer marking table, and no such
table was found. The narrow criterion — "the package must
unambiguously spell out 28.800 MHz" — would therefore gate the
catalogue indefinitely on a marking-code lookup that may not exist,
while discarding strong unit-specific evidence the inspection did
produce: the oscillator is physically present, at the documented
reference position, on positively identified hardware, with no
contradictory marking.

This amendment is a bounded correction to an overly narrow
evidentiary test. It does not relax the underlying discipline.

## The amended rule: UNIT_INSPECTED_MODEL_CORROBORATED

A reference declaration is permitted if and only if ALL of the
following hold:

1. The actual physical unit is inspected and contains an oscillator
   component at the documented reference-clock position, with no
   observation contradicting the manufacturer-defined reference
   frequency. The package marking is recorded verbatim; it is NOT
   required to be independently decoded, and it is NOT used as the
   sole frequency authority.
2. The exact device model / hardware family is independently
   established (e.g. FCC ID, PCB revision, enclosure identity).
3. The manufacturer explicitly specifies both the reference
   frequency AND the tolerance being claimed for that hardware.

The nominal frequency comes from the manufacturer circuit definition
for the positively identified hardware; the inspection establishes
that this particular physical device actually contains the expected
oscillator in the expected reference position. This is
substantially stronger than "datasheet says RTL dongles use 28.8" —
and it is not model inference, schematic inference, or
result-selected inference.

## Negative controls

The declaration MUST be refused if any of the following hold:

- the oscillator package is absent from the inspected unit;
- it sits at a circuit position other than the documented
  reference-clock position;
- it bears a marking visibly contradicting the
  manufacturer-defined reference frequency;
- the PCB is a different model / revision family than the
  documented hardware;
- the manufacturer documentation does not actually specify the
  reference frequency and the tolerance being claimed.

## Excluded evidence

No RF observation participates in the authority chain — neither for
nor against the declaration. In particular, the observed ~100.800
MHz feature and the arithmetic 100.8 / 28.8 = 3.5 played no role in
the reference declaration and are excluded from it.

## What this amendment is not

- It does not authorize declaring a reference from manufacturer
  documentation alone (no inspection).
- It does not authorize "assuming" the fitted part matches the
  documentation.
- It does not change the prohibition on fabricated, inferred, or
  result-selected reference values in
  `docs/521_CATALOGUE_AUTHORIZATION_DECISION.md`; that prohibition
  stands in full.
