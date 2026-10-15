# Dedirock numerical-environment snapshot — attempt `GATED2-20261015T005128Z-1b0b45`

Measured once, live, during `CHECK_ONLY` (S4), 2026-10-15T02:13:54Z. REMEASURED-class fields
(§9.1 of `metamorphic_r4_gate_c_preflight.md`) are point-in-time measurements, not frozen
requirements; a later `RECHECK`, if one is ever separately authorized, would measure these again
independently, not reuse these numbers.

| Field | Value |
|---|---|
| Hostname | `dedirock-919498765` |
| Kernel | `5.14.0-687.48.1.el9_8.x86_64` |
| OS | `Linux-5.14.0-687.48.1.el9_8.x86_64-x86_64-with-glibc2.34` |
| CPU model | `Intel(R) Core(TM) i9-14900K` |
| Logical CPUs | 4 |
| Uptime at measurement | 1,113,478.86 s (≈ 12.88 days) — consistent with, though not proof of, the operator's "continuously operating" statement |
| Load average (1/5/15 min) | `0.0, 0.0, 0.0` — idle at measurement time |
| Free space, job filesystem | 66.493 GB |
| Free space, `TMPDIR` | 66.493 GB |
| **Available memory** | **5.241 GB** (reported live as "5.2 GB") |
| Python | `3.12.14` (`main, Aug 13 2026`), GCC 11.5.0 20240719 (Red Hat 11.5.0-14) |
| NumPy | `2.5.3` |
| Interpreter binary SHA-256 | `1c7d2779938ef3f45cd850119a8655af4c37eaec06b45e3a2e314928a3c3b972` (new — not previously recorded anywhere in this repository; useful for a future Gate D request's `--interpreter-sha256`) |
| BLAS | `scipy-openblas` 0.3.34.106.0, `OpenBLAS 0.3.34.106.0 USE64BITINT DYNAMIC_ARCH NO_AFFINITY Haswell MAX_THREADS=64` |
| SIMD (NumPy) | baseline `X86_V2`; found `X86_V3`; not found `X86_V4`, `AVX512_ICL`, `AVX512_SPR` |
| Compiler | `gcc` 14.2.1, linker `ld.bfd` |
| float64 | 8 bytes, little-endian (checked, required to match the reference) |
| Competing r4 processes | none (`[]`) |

**On the 5.2 GB available-memory figure**, per the owner's own instruction: it remains above the
required 4 GB floor (REMEASURED item 3f, `PASS`), but it leaves limited headroom for the full
replay. This figure is recorded here attached to the record, as a REMEASURED, point-in-time
observation — **it is not a prediction of peak runtime demand** for the actual `run_job.sh`
execution, which has not yet run on this host and has no measured memory profile here. The local
reference run's own peak-memory figures (if any exist) are a separate, already-recorded dataset
and are not substituted for this host's own measurement.

**Local runtime baseline, recorded inside `PREFLIGHT_RECORD.json`'s `declaration` block** (the
reference figures the tool itself carries, from the local reference run — not a measurement of
this host, included here because it bears on the window-vs-runtime question raised earlier in
this project):

| Stage | Seconds |
|---|---|
| Bounded controls | 162 |
| Fixtures and pins | 24 |
| Smoke run and gate | 203 |
| Full harness and gate | 1,101 |
| **Total** | **1,491 (≈ 24.85 minutes)** |

This is the *local* reference run's duration, not a prediction for Dedirock — CPU, memory, and I/O
characteristics differ (4 logical CPUs here vs. whatever the local reference host has; BLAS/SIMD
configuration differs). It is included only to show that the reference baseline is far shorter
than the 4-hour declaration window, which is relevant context, not a guarantee, for the window-vs-
runtime distinction already established for this attempt (no scientific requirement to bound the
harness's runtime by the declaration's length; see the local preparation record).
