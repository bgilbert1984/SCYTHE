# SCYTHE Mission — the missing coordination object (draft, architectural convergence)

> **DRAFT. No code changes, no schema files, no validator tool, and no Gate C/Gate D change are made
> by this document.** It does not authorize anything and does not touch the GATED3 request or any
> other in-progress attempt. It is written now, ahead of implementation, because the governance
> series (Gate C/D, PRs #176–#186) has reached a stable baseline and the next engineering focus is
> expected to be the RF/mission side rather than another governance refinement.

This is deliberately framed as a **convergence** document, not a proposal to introduce new
architecture: SCYTHE already contains several working domain-specific collection pipelines and a
shared knowledge graph. The claim below is that the one thing missing is a coordination object —
**Mission** — not another subsystem, another graph, or a replacement for anything that already
works. Every claim about "what already exists" below was checked against the actual module, not
assumed.

## 1. What already exists

```
       RF Ingest   ───────────┐
       PCAP Ingest ───────────┤
       ADS-B Pipeline ────────┼──────▶  Hypergraph Engine (HGNode / HGEdge)
       AIS Pipeline  ─────────┤                    │
                              │                    ▼
                              │           Invariant Ledger
                              │                    │
                              │                    ▼
                              └──────▶  Cesium Visualization
```

| Existing module | What it already does |
|---|---|
| `graphops_rf_ingest.py` | Validation boundary for live measured-RF spectral summaries; `RFObservationStore` derives SNR and a "stable evidence identity"; `OBSERVATION_ORIGINS` distinguishes `OPERATOR_SYNTHETIC` from `EXTERNAL_SENSOR`. |
| `pcap_ingest.py` | Deterministic PCAP → sessionization → hypergraph, with its own stated discipline: "PCAPs are not evidence until sessionized; sessions are not knowledge until ledgered." |
| ADS-B pipeline (`docs/safari_adsb_first_light_blog_post.md`) | A genuinely live, verified pipeline: NESDR capture → numpy preamble matched filter → pyModeS → served tracks, already run against the real sky. |
| AIS pipeline (`rf_scythe_api_server.py`) | A real vessel-tracking endpoint, CSV-backed today with a mock fallback. |
| `graphops_rf_antenna.py` | The operator-antenna-declaration pattern: `OPERATOR_DECLARED` fields, digest-identified, recorded with the timestamp it took effect, **never applied backwards** over products already emitted — the same shape as the Gate C operator declaration, already established here for a different purpose. |
| `capture_policy.py` | A rule-based *authorization* DSL (`AUTHORIZE`/`REQUIRE_APPROVAL`/`DENY`, with constraints like `max_duration`, `allowed_interfaces`) deciding whether a specific capture *command* may run. This is a governance concept, not an intent/parameters concept — named here specifically because §5 must avoid colliding with it. |
| `hypergraph_engine.py` (`HGNode`, `HGEdge`, `RFHypergraphAdapter`) | The actual entity graph: generic `kind`-discriminated nodes (`id`, `kind`, `position`, `frequency`, `labels`, `metadata`, `created_at`, `updated_at`) and edges (`id`, `kind`, `nodes[]`, `weight`, `labels`, `metadata`, `timestamp`). |
| `scythe_invariant_ledger.py` | A domain-agnostic transition-invariant checker (`TransitionContract`, `BoundedBalance`, `BoundedCeiling`, `InvariantVerdict`), already wired into CI. |
| `poi_manager.py`, `cesium-hypergraph-globe.*` | Cesium-facing POI/hypergraph rendering, already working. |
| `mission_runner.py` | `run_fusion_demo_5km`: a hardcoded simulation of LPI detection → AoA/TDoA tracking — the only literal "mission" in the codebase today, informal and not schema-driven. |

**What's missing isn't another graph, another ingest path, or another identity scheme.** It's the
thing that says *these observations belong together because they were collected for one
operational purpose.* That's Mission.

## 2. Hierarchy

```
Mission
  ├── Session A  (e.g. Monday morning walk)
  ├── Session B  (e.g. Tuesday rooftop)
  ├── Session C  (e.g. stationary replay)
  └── Session D  (e.g. vehicle route)
       └── ObservationSet  (one physical acquisition artifact)
             └── Observation  (one extracted record)
                   └── Entity reference  (an HGNode this observation contributed to)
```

A Mission owns **multiple** Sessions — that's how people actually collect data (a multi-day survey
is several distinct outings, not one continuous session). Everything below Mission is existing
machinery; Mission is the one new root.

## 3. Relationship to the governance layer

Orthogonal, not layered underneath, and this document adds to it rather than replacing it:

| Layer | Answers |
|---|---|
| **Governance** (Gate C/D, declarations, receipts) | Who approved an execution, and under what constraints? |
| **Mission** (this document) | What were we trying to accomplish, and why? |
| **Collection** (existing ingest pipelines) | What was actually observed? |
| **Knowledge** (existing hypergraph) | What relationships exist between what was observed? |

Most mission types are live capture and never touch Gate C/Gate D at all — there's no frozen
package, no replay host, nothing for `gate_c_preflight.py` to check. The one exception is `REPLAY`
(§6): a mission whose evidence comes from a Gate-D-authorized harness run. For that type only, the
mission record carries the attempt's own frozen request SHA-256 (e.g. GATED3's
`29fee7603ccf1f528089b1a3266862e6c7c94e248c201d3cac042dc85c323d42`) alongside `mission_sha256` —
the same two-hash-binding pattern already used in `governance_receipt_architecture.md`
(`declaration_sha256` + `request_sha256`), reused here rather than invented twice.

## 4. Mission schema (`mission-definition/1`, sketch — not implemented)

```jsonc
{
  "schema": "mission-definition/1",
  "mission_id": "RFMAP-20261017-001",
  "mission_type": "RF_SURVEY",                // §6 — the evidence-grounded vocabulary
  "purpose": "Locate intermittent 915 MHz emitter, downtown Seattle corridor",
  "classification": "unclassified",
  "operator": "Ben Gilbert",
  "mission_parameters": {                     // §5 — renamed from "Capture Policy" to avoid
    "sdr": "RTL-SDR v5",                       // colliding with the existing capture_policy.py
    "antenna_sha256": "<graphops_rf_antenna.py declaration digest>",
    "gain": "AGC",
    "expected_environment": "urban, multipath-heavy",
    "frequency_plan": {"center_mhz": 915, "span_mhz": 2},
    "gps_cadence": "1 Hz",
    "operator_notes": "intermittent, likely a periodic beacon",
    "capture_profile": "urban_passive"         // a named preset (§7) resolving to these same fields
  },
  "region": {"type": "polygon", "coordinates": [...]},   // optional (stationary/calibration may omit)
  "start_after": "2026-10-20T14:00:00+00:00",
  "expire_after": "2026-10-20T18:00:00+00:00",
  "gate_d_request_sha256": null,              // only set for mission_type REPLAY
  "mission_sha256": "<computed once frozen>"
}
```
`mission_parameters.antenna_sha256` names an existing `graphops_rf_antenna.py`-style declaration
digest — it does not duplicate antenna identity, it references it.

## 5. Mission Parameters, not Capture Policy

`capture_policy.py` already exists and already owns the term "Capture Policy" for a different
concept — authorization rules for whether a specific capture command may run. Mission's intent
section is deliberately named **Mission Parameters** instead: SDR, antenna, gain, expected
environment, frequency plan, GPS cadence, operator notes, classification, purpose. Mission says
*what we intended*; `capture_policy.py` still independently says *whether a given command is
allowed to execute* — the two are not merged, and nothing about `capture_policy.py` changes here.

## 6. Mission types — only categories with existing evidence

| Type | Evidence it's grounded in | Touches Gate C/D |
|---|---|---|
| `RF_SURVEY` | `graphops_rf_ingest.py`, `graphops_rf_antenna.py`; sub-classified by `mission_parameters.rf_posture` (`WALK`, `STATIONARY`, `FOX_HUNT`, `LONG_TERM_MONITOR`) rather than separate top-level types, so downstream code only ever asks `mission.mission_type` | no |
| `PCAP_COLLECTION` | `pcap_ingest.py` | no |
| `ADSB_COLLECTION` | the live ADS-B pipeline (`docs/safari_adsb_first_light_blog_post.md`) | no |
| `AIS_COLLECTION` | the AIS vessel-tracking endpoint (`rf_scythe_api_server.py`) | no |
| `REPLAY` | the Gate C/D governance series itself | **yes** |
| `CALIBRATION` | reference captures against a known source | no |
| `SIMULATION` | `mission_runner.py`'s existing `run_fusion_demo_5km`; synthetic observations use the existing `OPERATOR_SYNTHETIC` origin from `graphops_rf_ingest.py` | no |
| `CUSTOM` | escape hatch for anything not yet in this table | depends |

`WARDRIVE`, `LTE_COLLECTION`, and `OSINT_COLLECTION` were considered and deliberately left out —
no corresponding module or pipeline was found in this repository. They can be added honestly once
they exist, rather than speculatively now.

## 7. Capture profiles

A capture profile is a named, reusable preset that resolves to a `mission_parameters` object — the
operator picks a name, the parameters resolve deterministically, and both the name and the resolved
snapshot get hashed into the mission record.

| Profile | Sample rate | Gain | FFT size | Averaging | GPS cadence | Upload cadence |
|---|---|---|---|---|---|---|
| `urban_passive` | moderate | AGC | moderate | light | periodic | batched |
| `fox_hunt` | moderate | manual, high dynamic range | small (fast) | none | high | real-time, event-marked |
| `stationary_observatory` | low, continuous | calibrated | large | heavy | low | periodic, + weather/thermal log |

(Concrete numeric values deliberately omitted — they belong in the profile's own data file once
implemented, not duplicated into prose that could drift from it.)

## 8. Session schema (`mission-session/1`) — purely factual, no intent

```jsonc
{
  "schema": "mission-session/1",
  "session_id": "RFMAP-20261017-001-S01",
  "mission_id": "RFMAP-20261017-001",
  "started_utc": "...", "ended_utc": "...",
  "operator": "Ben Gilbert",
  "sensor_declaration_sha256": "<graphops_rf_antenna.py digest actually in use>",
  "receiver": "RTL-SDR v5",
  "capture_profile_used": "urban_passive",
  "location_source": "gps",
  "session_sha256": "<computed once frozen>"
}
```
A Session records *what happened*; it makes no decisions. If a session deviated from its mission's
planned `mission_parameters` (different profile, different antenna), that's visible as a factual
difference between the two records, not a judgment encoded into either one.

## 9. ObservationSet — the hidden gap

Neither `graphops_rf_ingest.py` nor `pcap_ingest.py` currently has an explicit object for *the
physical acquisition artifact itself*, as distinct from the records extracted from it —
`graphops_rf_ingest.py` validates one spectral-summary record at a time with no enclosing "this
sweep" concept, and `pcap_ingest.py` sessionizes a PCAP file directly into graph sessions with no
named object for "the file." That's the gap `ObservationSet` fills: one physical acquisition
artifact (`capture1.sigmf`, `capture2.pcapng`, `flight.csv`, `ais.csv`, `rtl_power.csv`), recording
*how many* observations it produced and the lineage of how it was handled. Precision matters here,
confirmed at implementation (PR #188): `ObservationSet` does not hold Observation objects — there is
no `Observation` type in this codebase yet, in `scythe_mission.py` or anywhere else. It holds a
count (`observation_count`) and provenance (`preprocessing_history`), not payloads. The acquisition
envelope and the extracted-observation data model are two different things; this section specifies
only the former.

```jsonc
{
  "schema": "observation-set/1",
  "observation_set_id": "RFMAP-20261017-001-S01-OS01",
  "session_id": "RFMAP-20261017-001-S01",
  "source_filename": "rtl_power_20261020_1400.csv",
  "source_sha256": "<digest of the acquired file>",
  "acquisition_tool_version": "...",
  "preprocessing_history": [],
  "observation_count": 0
}
```
This cleanly separates *a capture artifact* from *the scientific observations extracted from it* —
a distinction a growing repository needs and doesn't currently have anywhere.

## 10. Observations and entities map onto the existing hypergraph, not a new one

- An **Observation** is what `graphops_rf_ingest.py` already validates, with `observation_set_id`
  (and transitively `session_id`/`mission_id`) added to its allowed fields — a change to that
  module's `ALLOWED_FIELDS`, not proposed or made here (§13).
- An **Entity/Emitter** is an `HGNode` with `kind="emitter"`: `position` and `frequency` are already
  first-class fields; confidence and lifecycle state (§11) live in `metadata`/`labels`, which are
  already open dicts — no new node type is needed.
- A **relationship** ("co-located," "same-device-probable," "handoff-successor") is an `HGEdge` with
  `kind` naming the relation, `weight` as its confidence, and `timestamp` already present.

This is a *mapping convention* mirroring what `RFHypergraphAdapter` already does for its own
inputs — not a proposal to add new fields to `HGNode`/`HGEdge` themselves.

## 11. Signal lifecycle

`Observed → Clustered → Localized → Characterized → Tracked → Attributed → Archived`, with a side
branch to `Needs Human Review` reachable from `Clustered` or `Characterized` when confidence or
ambiguity requires it. Stored as `labels["lifecycle_state"]` on the entity's `HGNode` — each
transition is a new, timestamped write, never an overwrite of the prior state, so history stays
replayable (§12). Whether illegal transitions should be enforced by a `scythe_invariant_ledger.py`
`TransitionContract` is an open question (§15), not decided here.

## 12. Temporal replay semantics (Cesium)

No new storage model: `HGEdge` already carries `timestamp`, and nodes already carry
`created_at`/`updated_at`. The only new requirement is a convention — every mission-relevant
node/edge change is itself a new, timestamped write, never an in-place mutation — so
`cesium-hypergraph-globe.*` (which already renders the hypergraph) can scrub a mission's history
between two timestamps instead of only showing current state. A discipline for callers, not a
change to `hypergraph_engine.py`.

## 13. The migration path is one optional foreign key

This is the point of framing this as convergence rather than a new system: adopting Mission does
not require rewriting anything that already works.

```
graphops_rf_ingest  ──┐
pcap_ingest          ──┤
ADS-B pipeline       ──┼──▶  + one optional field: mission_id
AIS pipeline         ──┤
Cesium replay        ──┤
Invariant ledger     ──┘
```
What does **not** change: the hypergraph schema (`HGNode`/`HGEdge`), existing evidence identity,
RF observation hashes, invariant-ledger semantics. Every existing subsystem gains exactly one new
reference; none is restructured around a new abstraction.

## 14. What this draft does not do

No JSON Schema files are written. No validator tool (a `mission_preflight.py` analogous to
`gate_c_preflight.py`) exists. No change is made to `graphops_rf_ingest.py`'s `ALLOWED_FIELDS`,
`pcap_ingest.py`, `hypergraph_engine.py`, `scythe_invariant_ledger.py`, or `capture_policy.py`. No
Gate C/Gate D protocol is touched. Nothing here authorizes any collection, replay, or execution.

## 15. Open questions

- How `mission_id`/`session_id`/`observation_set_id` actually get threaded into
  `graphops_rf_ingest.py`'s `ALLOWED_FIELDS` and `pcap_ingest.py`'s session nodes without breaking
  either module's existing validation boundary.
- This draft confirms the ADS-B and AIS pipelines exist (§1) but does not examine their ingest code
  paths in the depth `graphops_rf_ingest.py`/`pcap_ingest.py` received — threading `mission_id`
  through them needs that examination first.
- Whether mission-lifecycle invariants (§11) belong in `scythe_invariant_ledger.py` as a generic
  `TransitionContract`, or need a mission-specific checker — not decided.
- How entity merge/split is decided when new evidence arrives. No algorithm is specified here.
- Storage/retention policy for `Archived` entities and their evidence — not addressed.
- Whether a continuous `LONG_TERM_MONITOR` session is chunked into multiple `ObservationSet`s (e.g.
  one per upload interval) or treated as one open-ended set — not decided.
- For `REPLAY` missions, whether `mission_sha256` should be computed before or after the Gate D
  request's own SHA-256 is frozen — an ordering question left open.
- Numeric capture-profile values (§7) need an actual data file once implemented; this draft
  deliberately does not fix numbers in prose that could drift from that file.
