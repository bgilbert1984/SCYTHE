# §5.21 — label for run 521-UHF-XTR-EPOCH1-RERUN2

Status: owner decision, 2026-11-05. Records how the run is described. It changes no evidence,
authorization, or code.

## Decision

Keep the run ID. Describe the run as:

> **experimental epoch (procedural), SESSION_SCOPED (evidential), cross-lifecycle verification
> not established.**

| Field | Value |
|---|---|
| Run ID | `521-UHF-XTR-EPOCH1-RERUN2` (unchanged; `EPOCH1` stays in the identifier) |
| Epoch | experimental epoch, procedural: it is epoch 1 of the §5.21 protocol and a re-run of that epoch |
| Stability scope | `SESSION_SCOPED`, evidential: a single acquisition session, no reconnect or power-cycle evidence |
| Cross-lifecycle verification | not established |

## What the two words mean

- **Procedural** says which step of the protocol the run was. It is a naming and bookkeeping
  fact, and it is why `EPOCH1` remains in the ID: the identifier records history and is not
  renamed.
- **Evidential** says what the run can support. The run contains no reconnect and no power
  cycle, so nothing in it can show `RECONNECT_STABLE` or `POWER_CYCLE_STABLE`. That follows
  ADR 0010's rule that these classes come only from the prescribed lifecycle tests, and that
  time or repetition within one session never promotes `SESSION_SCOPED`.

The `EPOCH1` in the ID is therefore not a claim about stability. Reading it as one is the
mistake this label exists to prevent.

## Lineage

| Run | State |
|---|---|
| `521-UHF-XTR-EPOCH1` | stopped by the owner at visit 64 of 192 (host going down); partial, not a catalogue |
| `521-UHF-XTR-EPOCH1-RERUN1` | complete, **superseded**: its provenance text wrongly said the termination part number was verified |
| `521-UHF-XTR-EPOCH1-RERUN2` | complete, authoritative: 192/192 visits, 0 refused, 3,350 entries |

RERUN2 identifiers for citation: chain digest
`93dca6d1139e40beec91b2176ca9bdb3315df24da5602cc39eec7076bdf7fce0`, schedule digest
`d35d706f926c740f2f211eaae34249cc739db4e611f377fa372433735ed73636`, archive
`catalogue_xtr_20261103_rerun2.tar.zst` (SHA-256
`b386797db7b9732cf36ca2ff39637941e2c66eeca5e357780cd33c5daaa5fb00`; uncompressed tar
`6e64f3f6fb5cc0a150c8e9d4598590d56f4a86deb137a08647573bff620f75c9`).

## What this does not do

- It does not edit the run's own files. `README-provenance.txt` and `catalogue.json` inside the
  read-only RERUN2 directory and its archive keep the text they were written with; this
  document is the interpretation that sits beside them.
- It does not state that any feature is stable across reconnects or power cycles. Those tests
  have not been run. Only a bounded reconnect and power-cycle run under the protocol could
  establish them.
- It does not settle the unrelated open items: the XTR reference inspection (not performed),
  the 3GPP ppm figure (unconfirmed), and Stage 1 hardware and clearances.
