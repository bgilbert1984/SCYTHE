# Detector interface (proposed)

The unit measured is **Detector × Receiver × Dataset × Behavior**. A detector is benchmarkable if it can
be driven through this surface without the harness knowing how it works.

```
describe() -> {name, version, input: {sample_rate, window, dtype}, classes: [...]}
classify(window) -> {label, score}
```

- `describe()` is frozen with a report; the detector's version is part of every result.
- Windows are passed in the shape `input` declares. Any resampling or normalisation done for the
  detector is recorded as preprocessing in the report, not hidden in the call.
- The detector is not given labels or the receiver fingerprint.

Report rule: **agreement is per class, never aggregate.** TrevTron is the warning: 100% on six signal
classes and 0% on noise would average to a flattering single cell. Each row of the matrix also carries
the corpus's receiver-fingerprint result, because that column is what separates detector failure from
label error.

Open: where the Hann detector bundle lives, and so whether it already has this shape.
