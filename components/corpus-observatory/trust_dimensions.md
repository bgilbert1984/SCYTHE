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
