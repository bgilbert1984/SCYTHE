# Governance profiles — an index, not a protocol

**Purpose.** This document indexes the governance profiles used by SCYTHE Gate C operator
declarations. It exists only to aid human readers; it has no enforcement role. **The declaration
schema identifier (`schema`) is the sole machine-readable authority** — `gate_c_preflight.py`
dispatches on that field alone, and this document is deliberately not consulted by, or referenced
from, any validator code. Adding a parallel "governance profile" field to the declaration JSON
itself was considered and rejected: the schema identifier already is that discriminator, and a
second field restating the same concept could drift out of sync with it without giving the
validator any new information.

## Profile 2026.1

| | |
|---|---|
| Declaration schema | `gate-c-operator-declaration/1` |
| Validity | Time-window bounded (`window_start_utc`/`window_end_utc`, exactly `window_hours`; re-checked against wall-clock time at every `CHECK_ONLY`/`RECHECK`) |
| Primary use | Early replay-governance development |
| Historical attempts | GATED1, GATED2, GATED3 |
| Status | Supported for historical compatibility |

## Profile 2026.2

| | |
|---|---|
| Declaration schema | `gate-c-operator-declaration/2` |
| Validity | Standing declaration; bound to `sandbox_epoch`; no expiry; valid until explicitly withdrawn (`<path>.withdrawn`) |
| Primary use | Single-operator, single-purpose development sandbox |
| Historical attempts | (first use pending) |
| Status | Preferred for new work |

## Transition note

GATED3 is intentionally retained as the final request authored under the `/1` declaration model.
New Gate C attempts should use `/2` unless there is a documented reason to remain on `/1`.
Historical requests are not retrofitted to newer governance profiles — see Design Principles below.

## Design principles

1. **Event-driven validity over clock-driven validity**, for single-operator sandbox environments
   where a repeating human time-box adds administrative churn without adding confidence.
2. **Backward compatibility.** `/1` and `/2` remain supported by the validator; neither attempt's
   evidence depends on the other schema existing.
3. **Historical immutability.** Existing attempts are preserved exactly as executed and are not
   rewritten to a newer governance profile after the fact.

See `metamorphic_r4_gate_c_preflight.md` §9 (Amendment 1: field classes, schema version 2) and §10
(Amendment 2: the standing declaration and `sandbox_epoch`) for the full specification this index
summarizes.
