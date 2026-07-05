# Biohub 2026 CellOps Intelligence Layer Design & Evaluation

This document provides a formal evaluation and architectural mapping of the **Biohub/SCYTHE CellOps Layer** implemented in `biohub_cellops/core.py` as adapted from the RF Signal Intelligence system in `core.py`.

---

## 1. Architectural Conversion Map

We have successfully transitioned the high-performance RF processing skeleton into a biologically sound, 3D spatial-temporal cell tracking and anomaly detection engine. The exact mapping is as follows:

| Legacy RF Module (`core.py`) | Adapted Biohub Module (`biohub_cellops/core.py`) | Biological & Physical Purpose |
| :--- | :--- | :--- |
| `RFSignal` | `CellDetection` | Dataclass representing 3D coordinates $(x,y,z)$, frame $t$, volume, intensity, and source segmentation run confidence. |
| `RFTrajectoryPoint` | `CellTrajectoryPoint` | Captures individual historical trajectory points, computing velocity and acceleration over time. |
| `SignalProcessor` | `NearestNeighborTracker` & `OptimizationTracker` | Computes greedy nearest-neighbor links and global Hungarian optimization mapping respectively in physical space ($\mu m$). |
| `GumbelTokenDropout` | `CandidateDropout` | Prunes weak, low-confidence segmentation candidates using Gumbel-Sigmoid neural scoring (with numpy fallbacks). |
| `SpeculativeEnsemble` | `SpeculativeTrackerEnsemble` | Fast/Slow lane tracking arbitrator. Sparse regions use Nearest Neighbor; dense regions or mitosis-complex regions escalate to global optimization. |
| `GhostAnomalyDetector` | `LineageAnomalyDetector` | Computes physical anomaly risks (Z-axis teleportation, volume discrepancy, non-adjacent frames, and asymmetric mitotic divisions). |
| `DOMASignalTracker` | `CellMotionTracker` | Predicts future coordinates by blending constant velocity, 3D Kalman filtering, and spatial tissue-flow fields. |
| `ExternalSourceIntegrator` | `SegmentationRunIntegrator` | Registers, activates, and ingests live data feeds from pipeline runs (e.g., Ultrack, Cellpose, StarDist). |
| `GhostAnomalyAPI` | `LineageRiskAPI` | FastAPI endpoints supporting MIP extraction, crop-window queries, edge re-evaluation, patch validation, and CSV submission export. |

---

## 2. Solved Bugs & Mismatches from Legacy Code

During the transplant of concepts from `core.py`, the following structural bugs and design traps were solved:

1. **SpectrumEncoder Mismatch:**
   - *Legacy Trap:* The constructor was defined with parameters `input_dim, hidden_dim=512, ...` but invoked elsewhere with keyword arguments `d_model=..., num_latents=...`, causing immediate crashes.
   - *Biohub Fix:* `CellSequenceEncoder` has a clean, synchronized constructor with accurate and robust default parameters.

2. **GhostAnomalyDetector Callability:**
   - *Legacy Trap:* `GhostAnomalyDetector` was referenced in the controller as a callable function (e.g., `detector(spectrum)`), but the class was not an `nn.Module` and lacked a `__call__` or `forward` method.
   - *Biohub Fix:* `LineageAnomalyDetector` exposes explicit, clean methods (`score_edge` and `score_division`) that strictly enforce physical conservation laws.

3. **Fake Coordinates & Drift:**
   - *Legacy Trap:* `_estimate_signal_position` used randomized bearing/elevation and frequency proxies.
   - *Biohub Fix:* All coordinates map to physical Euclidean distances ($\mu m$) scaled by the microscope's 3D voxel scaling `voxel_scale` (correctly handling anisotropic Z-spacing).

4. **Kinetics & Identity Swaps:**
   - *Legacy Trap:* Trajectories in the RF demo generated unique random source IDs on every emission, resetting the motion history.
   - *Biohub Fix:* Track IDs are persistent. Velocity and acceleration vectors are computed frame-by-frame and updated in active trajectory buffers.

---

## 3. Physical & Biological Validation Rules

Our implemented `LineageAnomalyDetector` evaluates the following physical constraints:

* **Z-Axis Teleportation:** Detects impossible speed jumps (in $\mu m / \text{frame}$) between sequential cell centroids.
* **Volume Conservation:** Asserts that daughter cell volume sums must match parent volume ($\pm 40\%$) during mitosis, and flagging segments where giant cell merges occur.
* **Mitotic Symmetry:** Flagging asymmetric cellular divisions where daughter volume sizes differ significantly ($\text{min} / \text{max} < 0.4$).
* **Temporal Continuity:** Highlights broken links where cells "disappear" or skip frame numbers (e.g., $t \to t+2$).

---

## 4. Test Coverage & Verification

We implemented a robust test suite in `biohub_cellops/test_core.py` covering:
1. **`CellDetection` Serialization:** Enforcing proper dictionary types and numpy compatibility.
2. **`CandidateDropout` Filtering:** Verification of Gumbel-style confidence-pruning.
3. **`NearestNeighborTracker` Linkages:** Testing greedily bounded spatial proximity linking.
4. **`OptimizationTracker` Assignment:** Asserting deterministic global bipartite matching (Hungarian algorithm).
5. **`SpeculativeTrackerEnsemble` Routing:** Validating fast/slow path escalation thresholds.
6. **`LineageAnomalyDetector` Edge / Division Rules:** Testing teleportation, volume-shift, and asymmetric mitosis checks.
7. **`CellMotionTracker` Kinetics & Flow:** Verifying velocity calculations, Constant Velocity (CV) coordinate predictions, and local tissue-flow averaging.

### Test execution output:
```bash
$ python -m unittest biohub_cellops.test_core
Ran 8 tests in 0.003s
OK
```

All implemented modules pass core validation and are ready for integration with Kaggle schema parsing, submission serialization, and dataset-specific metric calibration.

---

## 5. Remaining Integration Gates

Although the CellOps intelligence layer is structurally validated, three external gates remain before leaderboard-grade operation:

1. **Kaggle Schema Gate**
   - Parse `sample_submission.csv`
   - Emit exact column ordering and row types
   - Re-read exported CSV and validate after serialization

2. **Metric Calibration Gate**
   - Use train labels to estimate which anomaly classes most strongly correlate with score loss
   - Convert biological risk scores into metric-risk weights

3. **SCYTHE Runtime Gate**
   - Render detections, track edges, divisions, warnings, and ghost runs
   - Support guarded patch operations from the UI
   - Export only from the validated `final_submission` namespace

