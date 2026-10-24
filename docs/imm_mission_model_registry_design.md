# Mission model registry and the `imm` commands (design draft)

**Status:** design only. Nothing here is implemented, and no code, schema file or endpoint is added by the change that carries
this document. It proposes how the console's `imm` commands could stop being stubs without becoming ML plumbing bolted onto a
command line, by making a trained model a first-class, mission-scoped, immutable artifact whose outputs are tracked as derived
interpretations, never as observations.

It started from a review that proposed a "Mission Model Registry". What is kept from it, what is changed, and why are marked below.

## 1. Where things stand

| Piece | State |
|---|---|
| `imm train / generate / visualize / load / save / encode / decode / cross-domain` (`handleImmCommand` in `command-ops-visualization.html`) | Console stubs. `train` prints five made-up epochs with a made-up loss; `load` and `save` print success and do nothing; `generate` returns a point count; `visualize` waits one second and reports success. `generate` no longer throws (#311) and says nothing is drawn. |
| An IMM implementation | **None in the repository.** No Kalman, IMM or tracking code exists in the Python or JavaScript. |
| `model_registry.py` (`ModelRegistry`, `ModelRole`) | Exists, and is **not** this: it routes questions between Ollama-hosted language models (roles such as `PRIMARY_SYNTHESIS`, `THREAT_EXPERT`) and surfaces their disagreement. See section 3. |
| `scythe_mission.py` | `Mission`, `Session` (frozen, digest-identified) and `ObservationSet` (append-only until `seal()`), with canonical `digest()`. |
| `writebus.py` | The write path. `WriteContext` and `Provenance` already carry `mission_id`, `source`, `model_version`, `evidence_refs`, `caused_by_event_id`, `parent_write_fingerprint`; a commit is idempotent and is published on the graph event bus as `ENTITY_UPSERT`. |
| `GET /api/missions/<mission_id>/events` | A mission-scoped, paginated, sequence-ordered event read. |
| The page's live entity stream | `EntitySync` (now the `entity-sync` workspace tool, #312): handlers receive each entity event, by type and as `entity`. |

## 2. Decision needed first: which "IMM"?

The word is used for two different things, and the design depends on which one is meant.

- The console text says **Inductive Moment Matching** ("IMM-RF-NeRF: Inductive Moment Matching for RF visualization"). That is a
  generative-model family. `train`, `generate`, `encode`, `decode` and the `--lhc` / `--jwst` flags all read as that.
- The review that prompted this document describes **Interacting Multiple Model** estimation: a bank of motion filters (constant
  velocity, constant turn, ...) with mode probabilities, giving predicted trajectories and confidence ellipses. That is a tracking
  technique, and it is the one that produces something worth drawing on the globe from the live ADS-B and AIS tracks.

They share nothing but an acronym. This draft therefore makes the registry **algorithm-agnostic** (a model has a `kind`) and
treats the two as different kinds. Recommendation: the first concrete producer is the tracking kind (section 9, phase 3), because the
inputs exist (the ADS-B pipeline is verified against the real sky; AIS is CSV-backed with a mock fallback today) and its output is plainly a prediction. The `imm` console command should not be silently
repurposed from one meaning to the other; either rename it or keep both under explicit kinds (`imm --kind track-imm`). That choice is
the owner's, not this document's.

## 3. Naming: not `ModelRegistry`

`model_registry.ModelRegistry` is taken and is about language-model routing. A second class of that name, or growing it, would
confuse both. The proposed module is `mission_models.py` and its store the **mission model registry**. Whether a language model could
ever be registered as a model artifact (so that its use is also provenanced) is out of scope here.

## 4. Principles (inherited, not new)

1. **Observations are immutable; derived products are separate and say what they derive from.** `ObservationSet` is sealed and
   append-only; `Provenance` already has `model_version`, `evidence_refs` and `caused_by_event_id`. A prediction is a derived write
   that cites its model and its evidence. It is never written as an observation.
2. **Mission is one optional foreign key** (`scythe_mission_definition.md` section 13). A model and its predictions may carry a
   `mission_id`; ambient, pre-mission use stays possible and is not an error.
3. **A change is a new timestamped write, never an in-place edit** (section 12 of that document). A model is never edited; a retrained
   model is a new artifact that names its predecessor. Being loaded or retired is a record about a model, not a field of it.
4. **Identity is a canonical digest of an identity-only projection**, as `Mission.digest()` and `ObservationSet.digest()` do, so
   serialization stays separate from identity.
5. **Models augment, they do not override** (the stated principle of `model_registry.py`). A prediction never changes an entity's
   lifecycle state (`Observed ... Tracked ... Attributed`) by itself; a person or an existing pipeline step does.
6. **Quarantine travels.** A model trained on anything EXTERNAL / INADMISSIBLE (`components/corpus-observatory/quarantine_policy.md`)
   inherits both marks, and so do its outputs. They cannot become evidence about a live site, receiver or emitter, and cannot enter
   the section 5.21 catalogue.

## 5. The artifact

A **model artifact** (`model-artifact/1`, sketch) is frozen after creation, like `Mission`. The identity-only projection (hashed to
`model_id`) is the definition, not the weights:

```jsonc
{
  "schema": "model-artifact/1",
  "kind": "track-imm",                    // algorithm family; see section 2
  "algorithm": "imm-kf",                  // concrete algorithm within the kind
  "algorithm_version": "1",
  "feature_schema_sha256": "...",         // digest of the declared input/output feature schema
  "parameters": { },                      // training/config parameters, canonical JSON
  "training_inputs": [                    // what it was fitted on
    { "observation_set_id": "...", "source_sha256": "...", "observation_count": 4812 }
  ],
  "training_origin": "MISSION_OBSERVED",  // or EXTERNAL (then INADMISSIBLE propagates)
  "derived_from": null                    // model_id of the artifact this retrains, if any
}
```

Outside the identity projection (so identity does not depend on them): `weights_sha256` (and where the weights live),
`created_at`, `created_by`, and `mission_id`. Two runs that produce different weights from the same definition are different
*artifacts of the same definition*, which is why `weights_sha256` is recorded separately; whether training is deterministic enough
for equal definitions to imply equal weights is an open question (section 10).

Rules that make the training record reproducible:

- Training may only use a **sealed** `ObservationSet`. An unsealed set can still grow, so a model "trained on it" could not be
  reproduced. This reuses an invariant that is already enforced in code.
- The registry is **append-only**. There is no update and no delete; retirement is a record.

Records about a model (each a new timestamped write, none an edit of the artifact):

| Record | Meaning |
|---|---|
| `model-activation/1` | `(mission_id, model_id, activated_by, activated_at)`. "Loaded" means *available to that mission's workspace* and nothing more. |
| `model-deactivation/1` | The same, closing it. |
| `model-retirement/1` | Do not activate again (superseded, withdrawn, found faulty), with a reason. |

## 6. The output: a prediction is an interpretation

A prediction (`model-prediction/1`, sketch) is a derived record:

```jsonc
{
  "schema": "model-prediction/1",
  "model_id": "...",
  "subject_entity_id": "...",           // the HGNode it is about (a tracked emitter, aircraft, vessel)
  "input_window": { "from": "...", "to": "..." },
  "generated_at": "...",
  "horizon_s": 120,
  "output": { },                        // kind-specific; for track kinds: timestamped states with covariance, mode probabilities
  "admission": "DERIVED"                // or INADMISSIBLE when the model's training_origin is EXTERNAL
}
```

It is written through **`WriteBus`**, not around it, so it gets idempotency, provenance and audit like every other write:

- `Provenance.source` = `model:<kind>/<algorithm>`; `model_version` = `model_id`; `evidence_refs` = the digests of the observation
  sets or event ids the window was read from; `caused_by_event_id` = the event that triggered the prediction; `mission_id` from the
  `WriteContext`.
- Hypergraph mapping follows the existing convention (mission definition section 10): an `HGNode` with `kind="prediction"` and an
  `HGEdge` of `kind="predicts"` to the subject. No new fields on `HGNode` or `HGEdge`.
- Predictions are new writes over time, never overwrites. A later prediction for the same subject supersedes the earlier one by time,
  and the earlier stays replayable on the timeline.

This replaces the review's "Mission Event" with what already exists: the write is the event, and the bus fans it out.

## 7. Event flow

```
live tracks / sealed observation sets
            |
   model service (server)  <-- model artifact (registry) + activation for the mission
            |  predict
        WriteBus  (provenance, idempotency, audit; ENTITY_UPSERT on the graph event bus)
            |
   graph event bus
     |-- hypergraph engine         (prediction node + predicts edge)
     |-- mission events read       (timeline: GET /api/missions/<id>/events)
     `-- entity stream             (EntitySync -> the page)
                                      `-- 'model-predictions' workspace tool -> Cesium layer
```

Consequences:

- **The models run on the server.** The observations, the registry and `WriteBus` are server-side; the page does not train or run
  inference. The `imm` commands become thin clients of server endpoints.
- **The hypergraph, the timeline and the globe are subscribers.** Nothing couples an IMM to the hypergraph directly, which is what
  the review wanted.
- **The globe layer is an ordinary lifecycle tool**, the same shape as ADS-B and AIS: it listens to `EntitySync` for `model-prediction`
  entities, draws a predicted-path polyline and a confidence ellipse through a sink service, removes what it drew on deactivate, and
  drops late results. What the globe shows is predictions; model internals are not drawn.
- `EntitySync` caps its cache at 5000 entities and trims by `lastUpdated`. Predictions are many and short-lived, so the layer should
  not rely on the cache; it should hold only the latest prediction per subject.

## 8. The `imm` commands, rewritten as operations on this model

| Command | Today | Under this design |
|---|---|---|
| `imm train <dataset>` | Prints fake epochs | Creates a training run over **sealed** observation sets, then a model artifact (section 5). Returns `model_id` and a summary (kind, input counts, `training_origin`, `created_at`). Asynchronous: the console shows the job, not a blocking call. |
| `imm save <path>` | Prints success | Not a separate step: an artifact is registered when it is created. `save` can become *export a bundle by `model_id`* (definition, weights, digests) for moving between hosts. |
| `imm load <path>` | Prints success | *Activate a model for the current mission* (an activation record). Availability only; nothing is drawn. Importing an exported bundle is a separate, explicit step and keeps the bundle's `training_origin`. |
| `imm generate <features>` | Point count, draws nothing | *Predict* for a subject or a window with an active model; the result is a prediction write (section 6). |
| `imm visualize` | Prints success | Shows predictions for the selected subject on the globe via the tool in section 7. |
| `imm encode / decode / cross-domain` | Stubs | Out of scope until the kind they belong to (the generative one) has an implementation. |

## 9. Phasing (each phase is its own reviewable change, in this order)

0. **Decisions** in section 10, in particular which IMM (section 2).
1. **Types only**: `mission_models.py` with frozen dataclasses, `to_dict` / `from_dict`, `digest()`, and the rules above as
   enforced errors, in the manner of PR #188 for `scythe_mission.py`. It imports nothing from the ingest paths or `WriteBus`.
2. **The registry store**: an append-only SQLite store plus read endpoints (list, get by `model_id`, activations for a mission).
3. **A baseline producer** of the tracking kind. A constant-velocity predictor over the existing ADS-B track history first (AIS once it is live),
   labelled as the baseline, just to prove the plumbing end to end; then a bank of motion models with mode probabilities. All behind
   the same interface: `fit(sealed inputs) -> weights`, `predict(state, horizon) -> output`.
4. **Prediction writes through `WriteBus`**, with `mission_id`, provenance and the admission mark.
5. **The frontend tool and Cesium layer**, following the AIS pattern, with the mutation-tested teardown the other tools have.
6. **Rewire the console commands** and delete the stubs.

## 10. Open questions

- **Which IMM** (section 2), and whether both kinds are wanted.
- **There is no `Observation` type in the codebase** (`scythe_mission_definition.md` section 9: `ObservationSet` holds a count and
  provenance, not records). "Train on the mission's observations" therefore has nothing to read. Live ADS-B and AIS arrive as
  entities, not as sealed observation sets. Either tracks get sealed into observation sets (which says something about what a track
  *is* as evidence), or training reads the entity history and the "sealed inputs only" rule needs a different anchor for
  reproducibility (for example a digest of the exact event range).
- **Identity**: is `model_id` the definition digest only (as sketched), or definition plus training inputs? The sketch includes the
  inputs; confirm.
- **Weights**: where they are stored, size limits, and retention. Equal definitions giving equal weights depends on seeded,
  deterministic training; not assumed here.
- **Authority**: who may train, activate or export. This is a governance question (the Gate layer, or a `capture_policy`-style
  rule), orthogonal to the mission and not decided here.
- **Calibration**: what a "confidence ellipse" claims, and how that is checked against what later happened. A prediction that is
  never scored should not be drawn as if it were reliable.
- **Admission marks**: `OBSERVATION_ORIGINS` in `graphops_rf_ingest.py` has two values (`OPERATOR_SYNTHETIC`, `EXTERNAL_SENSOR`). A
  model-derived origin would be an additive value following the pattern of #189 / #190; not proposed as a change here.
- **Volume**: prediction rate, retention and what the timeline shows (every prediction, or the latest per subject).
- **`scythe_architectural_invariants.md`** is cited by `scythe_mission.py` and the mission definition but is not in the repository
  (checked 2026-10-24). The invariants used above are taken from the code that enforces them, not from that document.

## 11. What this change does not do

No module, endpoint, schema file, store, console command, frontend tool or test is added or changed. The `imm` stubs stay exactly as
they are after #311. Nothing here authorizes training on, importing or exporting any data.
