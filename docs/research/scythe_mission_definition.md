# SCYTHE Mission Definition — a declared-scope layer over existing collection infrastructure (draft)

> **DRAFT DESIGN PROPOSAL. No code changes, no schema files, no validator tool, and no Gate C/Gate D
> change are made by this document.** It does not authorize anything and does not touch the GATED3
> request or any other in-progress attempt. It is written now, ahead of implementation, because the
> governance series (Gate C/D, PRs #176–#186) has reached a stable baseline and the next engineering
> focus is expected to be the RF/mission side rather than another governance refinement.

## 1. Purpose

Gate C and Gate D answer *"may this execution run?"* — package identity, host state, operator
authorization. They say nothing about *why* a collection happened or how its outputs trace back to
an objective. This document sketches a **Mission** layer whose job is exactly that: group a bounded,
declared RF-collection or replay activity together with everything it produces, so a reviewer (or
Cesium viewer) can always answer "what was this observation part of, and what was that activity
trying to find out."

## 2. What already exists — this is not a blank slate

Before sketching anything new, it matters what's already in the repository, because most of the
Evidence/Entity/graph machinery a naive design would invent from scratch is already here in working
form:

| Existing module | What it already does | Relevance here |
|---|---|---|
| `mission_runner.py` | `run_fusion_demo_5km`: a hardcoded simulation of LPI detection → AoA/TDoA tracking over a fixed sensor registry | The only literal "mission" in the codebase today — informal, not schema-driven. This draft's `SIMULATION` mission type would eventually wrap work like this, not replace it. |
| `graphops_rf_antenna.py` | The operator-antenna-declaration pattern: `OPERATOR_DECLARED` fields, digest-identified, recorded with the timestamp it took effect, **never applied backwards** over products already emitted | The template this draft reuses for sensor declarations (§5) rather than inventing a new declaration idiom — it's the same shape as the Gate C operator declaration, already established in this codebase for a different purpose. |
| `graphops_rf_ingest.py` | A validation boundary for live measured-RF spectral summaries; `RFObservationStore` already derives SNR and a "stable evidence identity"; `OBSERVATION_ORIGINS` already distinguishes `OPERATOR_SYNTHETIC` from `EXTERNAL_SENSOR` | This *is* the existing Observation/Evidence boundary for spectral data. §8 proposes threading a `mission_sha256`/`session_id` through it, not replacing it. |
| `pcap_ingest.py` | Deterministic PCAP sessionization into the hypergraph, with its own stated discipline: "PCAPs are not evidence until sessionized; sessions are not knowledge until ledgered" | An existing sibling of the "Session" concept (§6), for a different capture type. |
| `hypergraph_engine.py` (`HGNode`, `HGEdge`, `RFHypergraphAdapter`) | The actual entity graph: generic `kind`-discriminated nodes (`id`, `kind`, `position`, `frequency`, `labels`, `metadata`, `created_at`, `updated_at`) and edges (`id`, `kind`, `nodes[]`, `weight`, `labels`, `metadata`, `timestamp`) | This already *is* the Entity/Emitter/relationship graph a naive design would propose building. §8–9 map onto it instead of proposing a parallel schema. |
| `scythe_invariant_ledger.py` | A domain-agnostic transition-invariant checker (`TransitionContract`, `BoundedBalance`, `BoundedCeiling`, `InvariantVerdict`), already wired into CI | A plausible home for mission-level lifecycle invariants (§9), without a mission-specific reimplementation. |
| `poi_manager.py`, `cesium-hypergraph-globe.*` | Cesium-facing POI/hypergraph rendering, already working | Temporal replay (§10) is a convention for how callers write to the existing graph, not a new rendering system. |

**What's actually missing, and what this document is for:** none of the above has an explicit, bounded,
declared top-level envelope — a record saying *this specific collection activity, for this purpose,
with this gear, between these times, produced these sessions and this evidence*. That envelope — the
Mission — is the real gap. Everything downstream of it should be the existing machinery above, wearing
one new foreign key.

## 3. Relationship to the governance layer

Orthogonal, not layered underneath. Most mission types here (`PASSIVE_WALK`, `PASSIVE_STATIONARY`,
`FOX_HUNT`, `LONG_TERM_MONITOR`, `CALIBRATION`) are live capture and never touch Gate C/Gate D at all
— there's no frozen package, no replay host, nothing for `gate_c_preflight.py` to check. The one
exception is `REPLAY`: a mission whose evidence comes from a Gate-D-authorized harness run. For that
type only, the mission record carries the attempt's own frozen request SHA-256 (e.g. GATED3's
`29fee7603ccf1f528089b1a3266862e6c7c94e248c201d3cac042dc85c323d42`) alongside `mission_sha256` —
the same two-hash-binding pattern already used in `governance_receipt_architecture.md`
(`declaration_sha256` + `request_sha256`), reused here rather than invented twice.

## 4. Mission schema (`mission-definition/1`, sketch — not implemented)

```jsonc
{
  "schema": "mission-definition/1",
  "mission_id": "RFMAP-20261017-001",
  "mission_type": "PASSIVE_WALK",            // see §7
  "purpose": "Passive RF survey, downtown corridor",
  "operator": "Ben Gilbert",
  "sensor_package": ["<antenna declaration sha256>", "<receiver declaration sha256>"],
  "region": {"type": "polygon", "coordinates": [...]},   // optional (stationary/calibration may omit)
  "capture_profile": "urban_passive",         // §6 — a name, not inline settings
  "start_after": "2026-10-20T14:00:00+00:00",
  "expire_after": "2026-10-20T18:00:00+00:00",
  "gate_d_request_sha256": null,              // only set for mission_type REPLAY
  "mission_sha256": "<computed once frozen>"
}
```
`sensor_package` names existing `graphops_rf_antenna.py`-style declaration digests — it does not
duplicate antenna/receiver identity, it references it.

## 5. Session schema (`mission-session/1`, sketch)

A mission may contain many sessions (same relationship `pcap_ingest.py` already has between a PCAP
batch and its sessions, generalized with an explicit parent):

```jsonc
{
  "schema": "mission-session/1",
  "session_id": "RFMAP-20261017-001-S01",
  "mission_sha256": "<parent mission's digest>",
  "sensor_declarations": ["<antenna sha256>", "<receiver sha256>"],
  "gps_quality_floor": "3d_fix",
  "clock_source": "gpsdo",
  "start_utc": "...", "end_utc": "..."
}
```
Every observation inherits mission/session provenance by carrying `session_id`, the same way
`graphops_rf_ingest.py` observations would carry it once threaded through (§11 — not done here).

## 6. Capture profiles — reusable presets, not per-mission settings

The operator picks a profile name; the rest resolves deterministically. Both the name and the
resolved parameter snapshot get hashed into the session record, so "which exact settings were in
effect" is never ambiguous even though the operator only chose a name.

| Profile | Sample rate | Gain | FFT size | Averaging | GPS cadence | Upload cadence |
|---|---|---|---|---|---|---|
| `urban_passive` | moderate | AGC | moderate | light | periodic | batched |
| `fox_hunt` | moderate | manual, high dynamic range | small (fast) | none | high | real-time, event-marked |
| `stationary_observatory` | low, continuous | calibrated | large | heavy | low | periodic, + weather/thermal log |

(Concrete numeric values deliberately omitted here — they belong in the profile's own data file once
this is implemented, not duplicated into prose that would then drift out of sync with it.)

## 7. Mission types

| Type | Typical duration | GPS required | Touches Gate C/D | Typical evidence |
|---|---|---|---|---|
| `PASSIVE_WALK` | minutes–hours | yes | no | spectral summaries, GPS track |
| `PASSIVE_STATIONARY` | hours–days | optional (fixed position known) | no | spectral summaries, waterfall |
| `FOX_HUNT` | minutes–hours | yes, high cadence | no | spectral summaries, GPS track, event markers |
| `LONG_TERM_MONITOR` | days–months | optional | no | spectral summaries, thermal/weather log |
| `REPLAY` | minutes–hours | no | **yes** (Gate C/Gate D) | harness evidence, retrieved via the existing custody chain |
| `CALIBRATION` | minutes | optional | no | reference spectral captures |
| `SIMULATION` | any | no | no | synthetic observations, explicitly `OPERATOR_SYNTHETIC` per `graphops_rf_ingest.py`'s existing origin field |

## 8. Observations and entities map onto the existing hypergraph, not a new one

- An **Observation Event** is what `graphops_rf_ingest.py` already validates, with `mission_sha256`
  and `session_id` added to its allowed fields (a change to that module's `ALLOWED_FIELDS`, not
  proposed or made here — see §11).
- An **Entity/Emitter** is an `HGNode` with `kind="emitter"`: `position` and `frequency` are already
  first-class fields; confidence and lifecycle state (§9) live in `metadata`/`labels`, which are
  already open dicts — no new node type is needed.
- A **relationship** ("co-located," "same-device-probable," "handoff-successor") is an `HGEdge` with
  `kind` naming the relation, `weight` as its confidence, and `timestamp` already present.

This section is a *mapping convention* for future code to follow, mirroring what `RFHypergraphAdapter`
already does for its own inputs — not a proposal to add new fields to `HGNode`/`HGEdge` themselves.

## 9. Signal lifecycle

`Observed → Clustered → Localized → Characterized → Tracked → Attributed → Archived`, with a side
branch to `Needs Human Review` reachable from `Clustered` or `Characterized` when confidence or
ambiguity requires it. Stored as `labels["lifecycle_state"]` on the entity's `HGNode` — each
transition is a new, timestamped edge or metadata update, never an overwrite of the prior state, so
history stays replayable (§10). Whether illegal transitions (e.g. `Observed` → `Attributed`,
skipping `Localized`) should be enforced by a `scythe_invariant_ledger.py` `TransitionContract` is an
open question (§12), not decided here.

## 10. Temporal replay semantics (Cesium)

No new storage model: `HGEdge` already carries `timestamp`, and nodes already carry
`created_at`/`updated_at`. The only new requirement is a convention — every mission-relevant node/edge
change is itself a new, timestamped write, never an in-place mutation of a prior state — so
`cesium-hypergraph-globe.*` (which already renders the hypergraph) can scrub a mission's history by
replaying its node/edge stream between two timestamps instead of only showing current state. This is
a discipline for callers, not a change to `hypergraph_engine.py`.

## 11. What this draft does not do

No JSON Schema files are written. No validator tool (a `mission_preflight.py` analogous to
`gate_c_preflight.py`) exists. No change is made to `graphops_rf_ingest.py`'s `ALLOWED_FIELDS`,
`pcap_ingest.py`, `hypergraph_engine.py`, or `scythe_invariant_ledger.py`. No Gate C/Gate D protocol
is touched. Nothing here authorizes any collection, replay, or execution.

## 12. Open questions

- How `mission_sha256`/`session_id` actually get threaded into `graphops_rf_ingest.py`'s
  `ALLOWED_FIELDS` and `pcap_ingest.py`'s session nodes without breaking either module's existing
  validation boundary.
- Whether mission-lifecycle invariants (§9) belong in `scythe_invariant_ledger.py` as a generic
  `TransitionContract`, or need a mission-specific checker — not decided.
- How entity merge/split is decided when new evidence arrives (e.g. two `Clustered` entities turning
  out to be one `Localized` emitter). No algorithm is specified in this draft.
- Storage/retention policy for `Archived` entities and their evidence — not addressed.
- For `REPLAY` missions, whether `mission_sha256` should be computed before or after the Gate D
  request's own SHA-256 is frozen — an ordering question this draft leaves open.
- Numeric capture-profile values (§6) need an actual data file once this is implemented; this draft
  deliberately does not fix numbers in prose that could drift from that file.
