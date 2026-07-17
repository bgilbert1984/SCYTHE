# Biohub 2026 CellOps Intelligence Layer: Implemented State and Integration Plan

This document maps the biological CellOps runtime in `biohub_cellops/core.py` to
the RF-oriented patterns in the legacy root `core.py`. The root module is a donor
of architectural ideas, not the CellOps production runtime. Unless a capability is
listed as **Implemented** below, it should be treated as planned integration.

---

## 1. Architectural Conversion Map

| Legacy RF concept (`core.py`) | CellOps implementation (`biohub_cellops/core.py`) | Current status |
| :--- | :--- | :--- |
| `RFSignal` | `CellDetection` | **Implemented.** Stores dataset identity, frame, voxel coordinates, confidence, volume, intensity, and source metadata. |
| `RFTrajectoryPoint` | `CellTrajectoryPoint` | **Implemented.** Stores trajectory state; `CellMotionTracker` derives velocity and acceleration. |
| `SignalProcessor` | `NearestNeighborTracker`, `OptimizationTracker` | **Implemented.** Provides greedy and Hungarian frame-to-frame assignment in scaled physical space. |
| `GumbelTokenDropout` | `CandidateDropout` | **Implemented as a standalone component.** Tested, but the main runtime currently applies a direct confidence threshold instead of calling it. |
| `SpeculativeEnsemble` | `SpeculativeTrackerEnsemble` | **Implemented.** Routes dense or uncertain regions to the optimization tracker. |
| `GhostAnomalyDetector` | `LineageAnomalyDetector` | **Implemented.** Scores edge and division anomalies using explicit biological rules, with an optional neural score when PyTorch is available. |
| `DOMASignalTracker` | `CellMotionTracker` | **Implemented.** Supports constant-velocity, Kalman, and local tissue-flow prediction. |
| `ExternalSourceIntegrator` | `SegmentationRunIntegrator` | **Framework implemented; real adapters planned.** The registered Ultrack and Cellpose lanes currently use `MockSegmentationRun`. |
| `GhostAnomalyAPI` | `LineageRiskAPI` | **Partially implemented.** Analysis and validated submission-preview routes exist; image extraction, patch application, and production file delivery remain planned. |

The biological module avoids several known root-module defects, including the
`SpectrumEncoder` constructor mismatch, callable misuse of `GhostAnomalyDetector`,
randomized position estimates, and unstable demo identities. Those defects have not
been repaired in the legacy `core.py` itself.

---

## 2. Implemented Runtime Capabilities

### Detection, tracking, and motion state

`CellLineageIntelligenceSystem` accepts frame candidates, removes detections below
the current confidence threshold, links consecutive frames, records retained
`CellDetection` objects and confirmed `CellTrackLink` objects, and updates motion
state. Track lookup is namespaced by `(dataset, cell_id)` so internal IDs may repeat
across different Kaggle datasets without colliding.

The current nearest-neighbor and Hungarian trackers produce one-to-one assignments.
`LineageAnomalyDetector.score_division()` is implemented and tested, but automatic
division creation requires a division-capable tracker to emit two child edges for one
parent. The existing assignment trackers do not yet establish that topology.

### Biological risk rules

The rule engine currently evaluates:

- non-adjacent frame links;
- travel speed in scaled physical space;
- parent/child volume and intensity changes;
- daughter-volume conservation outside the range `0.6` to `1.4`;
- daughter asymmetry below the configured ratio, `0.4` by default; and
- unusually long parent-to-daughter distances.

These are anomaly signals. They are not yet calibrated to the competition metric and
do not independently prove that a proposed edit will improve leaderboard score.

### Kaggle node/edge submission boundary

`biohub_cellops/submission_guard.py` is the single owner of the submission
contract. Its `KaggleSubmissionCompiler` implements the observed columns exactly:

```text
id,dataset,row_type,node_id,t,z,y,x,source_id,target_id
```

Compilation performs the following transformations and checks:

1. Treats `CellDetection.embryo_id` as Kaggle's exact `dataset` value. Callers must
   populate it with the zarr stem, not a shortened embryo label.
2. Assigns globally unique integer `node_id` values in deterministic dataset/time/ID
   order.
3. Emits node rows with `source_id=-1` and `target_id=-1`.
4. Emits edge rows with `node_id=t=z=y=x=-1`.
5. Rejects empty submissions, duplicate detections, non-finite or negative node
   coordinates, dangling edges, duplicate edges, cross-dataset edges, and edges that
   do not point forward in time.
6. Writes the exact column order, optionally compares it with a supplied
   `sample_submission.csv` header, and re-reads the output to verify row count and
   serialization.
7. Optionally validates complete dataset coverage and every node's `t`, `z`, `y`,
   and `x` values against supplied `(T, Z, Y, X)` volume shapes, returning per-dataset
   node counts, edge counts, and edge/node ratios.

The runtime entry points are:

```python
rows = system.compile_kaggle_submission()

system.write_kaggle_submission(
    "/kaggle/working/submission.csv",
    sample_submission_path="/kaggle/input/.../sample_submission.csv",
)
```

`GET /api/biohub/export_submission` returns a schema-correct validated JSON preview.
It explicitly reports `writes_file: false`; writing the Kaggle artifact remains an
explicit runtime or notebook operation through `write_kaggle_submission()`.

### Trackastra adapter

`TrackastraCellOpsAdapter` accepts the notebook's `NodeRow` and `EdgeRow` lists,
plain mappings, or pandas-style DataFrames. It converts Trackastra node IDs into
dataset-scoped internal CellOps IDs, preserves the exact dataset name, rejects
dangling and duplicate graph records, and marks a pair of outgoing edges as a
division. The canonical compiler then assigns final globally unique Kaggle node IDs.

```python
from biohub_cellops.submission_guard import KaggleSubmissionCompiler
from biohub_cellops.trackastra_adapter import TrackastraCellOpsAdapter

cells, links = TrackastraCellOpsAdapter.adapt(all_node_rows, all_edge_rows)
rows = KaggleSubmissionCompiler.compile(cells, links)
KaggleSubmissionCompiler.write_csv(
    rows,
    "/kaggle/working/submission.csv",
    sample_submission_path=sample_path,
)
```

---

## 3. Verification Status

The core suite contains 11 tests, with 11 additional guard and adapter tests. The
22-test package suite covers:

- serialization of `CellDetection`;
- fallback candidate filtering;
- nearest-neighbor and Hungarian assignment;
- speculative fast/slow routing;
- edge and division anomaly rules;
- motion kinetics and prediction;
- Kaggle node/edge compilation across multiple datasets;
- CSV write/read-back validation against a sample header; and
- rejection of dangling submission edges;
- binary-division and cross-dataset graph guards; and
- Trackastra dataclass conversion, division labeling, and dangling-edge rejection.

Current verification command:

```bash
python -m unittest discover -s biohub_cellops -p 'test_*.py'
```

This validates isolated runtime behavior. It does not constitute a full hidden-test
Kaggle rerun, score validation, load test, or API integration test.

---

## 4. Planned Integration Gates

### A. Dataset and segmentation integration

- Replace `MockSegmentationRun` registrations with real Ultrack, Cellpose, StarDist,
  or notebook-output adapters.
- Populate `embryo_id` with the exact test zarr stem for every retained detection.
- Connect a division-capable tracking result so mitotic edges reach the compiler.

### B. Metric-risk calibration

- Evaluate anomaly categories against labeled training movies and the actual scoring
  implementation or a verified proxy.
- Estimate the score cost of false nodes, missing edges, incorrect edges, and division
  errors separately.
- Convert biological risk signals into measured keep/drop/relink policies.
- Keep an audit trail so a risky automatic correction can be reverted.

### C. SCYTHE operator runtime

- Render real image MIPs/crops rather than returning detection metadata with fixed
  dimensions.
- Display detections, edges, division candidates, uncertainty, and metric-risk flags.
- Add guarded patch application and persistence; the current route only validates a
  proposed parent/child patch.
- Introduce a validated `final_submission` state and permit artifact export only from
  that state.

### D. Kaggle notebook acceptance

- Run the real detector/tracker over every hidden-test input without internet access.
- Attach an offline bundle containing `biohub_cellops`; the patched Trackastra
  notebook now imports it and calls the canonical compiler unconditionally.
- Confirm `/kaggle/working/submission.csv` appears in the saved Notebook Version.
- Measure runtime, memory use, dataset coverage, and failure behavior on a full local
  or Kaggle-hosted smoke test.

Until these gates are complete, CellOps should be described as a tested intelligence
and submission-compilation layer, not a leaderboard-ready end-to-end tracker.
