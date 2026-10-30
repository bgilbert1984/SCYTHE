# Fingerprint schema v1 (frozen on corpus #2)

Nine metrics, each needing a frozen definition and acceptance language before the second corpus
runs. Without that the trust table decays as definitions drift.

| # | Metric | Measures | State |
|---|---|---|---|
| 1 | Reference comb | coherent harmonic teeth in noise/empty captures | proven on TrevTron (149/150 Hz teeth, 100/100 noise samples) |
| 2 | LO leakage | DC-bin energy excess in noise captures | to define |
| 3 | DC offset | mean I/Q offset in LSB | already in the XTR baseline metric set |
| 4 | Clock drift | carrier-frequency drift across a capture | to define |
| 5 | Gain compression | clipping and saturation statistics | to define |
| 6 | Spectral tilt | passband power slope | to define |
| 7 | Periodic interference | cyclostationary structure that is not the comb | to define |
| 8 | Empty-frame entropy | information content of nominally empty frames | to define |
| 9 | Receiver uniqueness | distance from a reference population | **undefined** |

Metric 9 has no working definition: distance in fingerprint space needs a reference population, and
none is chosen. It blocks the freeze. Metrics 2–8 are cheap spectral statistics once defined.

Freeze rule: a definition, units, the capture type it applies to (noise-only vs. signal-bearing), and
what result counts as "present", "absent" and "not measurable" are written down and versioned.
"Not measurable" is a result, not an omission: a corpus with no noise-only captures cannot answer
metrics 1 and 2.

Negative control: alirezaaminzadeh/radioml-2018-samples is synthetic, so the battery should report
"no receiver chain" or recover its documented synthesis artifacts. Either validates the definitions.

## Proposed addition: out-of-band shape and spurs (metric 10, not yet in v1)

Source: Scholl, arXiv:2610.15494 (RTTY generators separated by DSP residue; CC BY-NC-ND, cite only).
There, a 3 kHz observation reached ~90% (fusion, >= 30 dB SNR) and a 400 Hz matched passband fell to
~70% with no fusion gain. The signature sits substantially out of band, so a matched-bandwidth
capture cannot see it.

| # | Metric | Measures | State |
|---|---|---|---|
| 10 | Out-of-band shape and spurs | filter skirt shape, discrete spurious lines, transition overshoot/ringing outside the signal passband | to define |

- Capture type: signal-bearing, with excess bandwidth beyond the occupied signal.
- "Not measurable" is the expected result for any corpus captured at matched bandwidth. Record it as
  such; it does not mean "absent".
- Report the observation bandwidth next to the result, since the metric depends on it.
- Adding it makes the freeze a ten-metric freeze. Agree that before corpus #2 runs.
- Receiver and transmitter contributions both land here. See the transmit-side dimension in
  `trust_dimensions.md`; metric 9 must not absorb them.
