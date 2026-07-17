# Kaggle Notebook-Rerun Submission Guidelines and Schema Guards

This document outlines the strict execution requirements, common failure modes, and automated verification safeguards required for submitting the Biohub CellOps tracking pipeline to Kaggle.

---

## 1. The Submission Mechanism (Notebook Rerun)

For this competition, Kaggle does not accept manually uploaded CSVs. Instead, Kaggle privately re-runs the selected saved Notebook Version on a **hidden test set**, then extracts the resulting `/kaggle/working/submission.csv` for scoring.

### Actionable Failure Mode (UI Bug)
If you see an `[object Object]` error in the Kaggle UI, it is a rendering bug. The actual error is:
```text
This Competition requires a submission file named submission.csv
and the selected Notebook Version does not output this file.
```

To survive this, the notebook must follow this deterministic path start-to-finish on every run:
```text
Load competition input from /kaggle/input/...
      ↓
Run CellOps inference / tracking pipeline
      ↓
Assert spatial, temporal, and coordinate invariants
      ↓
Build submission DataFrame matching sample_submission.csv schema
      ↓
Write output to /kaggle/working/submission.csv
      ↓
Finish with zero exit errors
```

---

## 2. Trackastra-to-CellOps Export Cell

After the Trackastra notebook has populated `all_node_rows` and `all_edge_rows`, run
this cell unconditionally at the **very end** of the notebook. The `biohub_cellops`
package must be bundled as an attached Kaggle dataset or copied into the notebook
environment because internet access is unavailable during scoring.

```python
from pathlib import Path

from biohub_cellops.submission_guard import KaggleSubmissionCompiler
from biohub_cellops.trackastra_adapter import TrackastraCellOpsAdapter

WORKING = Path("/kaggle/working")
SUBMISSION_PATH = WORKING / "submission.csv"
sample_path = next(Path("/kaggle/input").rglob("sample_submission.csv"))

cells, links = TrackastraCellOpsAdapter.adapt(all_node_rows, all_edge_rows)
submission_rows = KaggleSubmissionCompiler.compile(cells, links)
dataset_shapes = {path.stem: get_volume_shape(path) for path in test_zarr_paths}
validation_report = KaggleSubmissionCompiler.validate_dataset_context(
    submission_rows,
    dataset_shapes,
)
KaggleSubmissionCompiler.write_csv(
    submission_rows,
    SUBMISSION_PATH,
    sample_submission_path=sample_path,
    dataset_shapes=dataset_shapes,
)

print("Validated submission:", SUBMISSION_PATH)
print("Rows:", len(submission_rows))
print("Columns:", KaggleSubmissionCompiler.columns)
print("Preview:", submission_rows[:3])
print("Per-dataset validation:", validation_report)
```

---

## 3. Schema Guard: The Contract Document

`biohub_cellops/submission_guard.py` is the canonical contract implementation. It
enforces the observed schema:

```text
id,dataset,row_type,node_id,t,z,y,x,source_id,target_id
```

The export cell above verifies the `sample_submission.csv` header before writing and
then parses the serialized output again. The guard also rejects empty submissions,
duplicate or invalid nodes, incorrect placeholders, dangling or cross-dataset edges,
backward-time edges, duplicate edges, and more than two outgoing edges per node.
With `(T, Z, Y, X)` shapes supplied, it additionally rejects missing or unexpected
datasets and out-of-bounds time or voxel coordinates.

For an already constructed list of schema rows, the lower-level API is:

```python
from pathlib import Path
from biohub_cellops.submission_guard import KaggleSubmissionCompiler

sample_path = next(Path("/kaggle/input").rglob("sample_submission.csv"))
KaggleSubmissionCompiler.write_csv(
    submission_rows,
    "/kaggle/working/submission.csv",
    sample_submission_path=sample_path,
)
```

---

## 4. Dummy Smoke-Test Submission

Before running the full cell tracking engine, execute a lightweight dummy round-trip to verify that Kaggle accepts the infrastructure's basic mechanics:

```python
from pathlib import Path
import pandas as pd

# Load sample submission directly from input
sample_path = next(Path("/kaggle/input").rglob("sample_submission.csv"))
submission = pd.read_csv(sample_path)

# Export direct copy as a dummy
submission.to_csv("/kaggle/working/submission.csv", index=False)

print("Dummy submission written successfully.")
print("Shape:", submission.shape)
print(submission.head())
```

Once you run **Save Version / Commit**, navigate to the notebook's **Output** panel, confirm that `submission.csv` is listed as a generated file, and click submit. This guarantees the pipeline mechanics are sound before introducing the core tracking physics.

---

## 5. Common Causes of "Submission CSV Not Found"

Your private evaluation run can fail to produce the CSV due to any of the following:
* **Directory Drift:** Writing to a relative path (e.g., `./submission.csv`) instead of `/kaggle/working/submission.csv`.
* **Casing Mismatch:** Naming the file `Submission.csv` or `submission_cellops.csv`.
* **Prior Cell Crashes:** Any exception in a cell preceding the export cell blocks execution.
* **Conditional Logic Bugs:** Wrapping the export function in a `if DEBUG:` block which is turned off during evaluation.
* **Internet Dependencies:** Importing packages or downloading model weights requiring internet access (which is disabled during competition scoring).
* **OOM/Memory Exhaustion:** Hidden test data size causing memory overflows during inference. Use `SpeculativeTrackerEnsemble` and `CandidateDropout` to manage search spaces.
* **Empty Predictions:** No tracks are successfully established, leaving a 0-row DataFrame.
