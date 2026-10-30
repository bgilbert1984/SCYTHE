# Trust dimensions (proposed)

A corpus report answers separate questions. Collapsing them into one score hides the structure the
Observatory exists to show (the roadmap's per-class rule applies here too).

| Dimension | Question | Source |
|---|---|---|
| Provenance | where did it come from, and can it be re-fetched byte-identical? | source, licence, SHA-256, import date |
| Receiver signature | what does the recording chain add? | fingerprint schema v1 |
| Label reliability | do the labels mean what the card says? | detector disagreement, read per class |
| Modality | is it IQ at all? | card and file inspection |
| Reuse terms | what may be done with it, and what must be credited? | licence |

Rules:
- Each dimension carries its own confidence. A report may be High on provenance and Low on label
  reliability.
- No dimension is derived from another. Low detector agreement is ambiguous between detector
  failure and label error; the receiver-signature dimension is what separates them.
- "Trust" in the roadmap's table is a recommended-use phrase ("External test vector", "Preferred
  benchmark"), not a score and never an admission.

Open: the roadmap names these loosely; this list is a proposal to agree before schema v1 freezes.

## Proposed dimension: transmit chain (not yet agreed)

Source: Scholl, arXiv:2610.15494. Different software DSP implementations of the same nominal mode
(five RTTY 45/170 generators) are classifiable over the air, trained on synthetic signals only.

| Dimension | Question | Source |
|---|---|---|
| Transmit chain | what does the transmitter's software and hardware chain add? | fingerprint schema metric 10, plus emitter metadata where known |

Rules specific to this dimension:
- Two sources, kept apart: hardware impairments and the DSP implementation. A per-device signature
  can be a firmware or software version, and identical software on different hardware can look like
  one device. Do not report a transmitter signature as a device identity.
- The measured signature is (generating software x the radiating chain), which is not pure software.
  The paper's chain (IC-7300 DSP, DAC, analog stage) is common across its classes.
- It is the dual of receiver signature. Low detector agreement now has a third candidate cause:
  a transmit-side implementation difference. Receiver signature alone no longer separates them.
- Observation bandwidth bounds what can be said. A matched-bandwidth corpus is blind to this
  dimension; report "not measurable", not "clean".
- Representation prior for detector transferability work (one paper, one transmitter, one receiver,
  one band): spectrum best at high SNR, IQ best at low SNR, fusion helps only with excess bandwidth.
  Treat as a hypothesis to test, not a finding.
