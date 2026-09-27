"""Build our submission notebook from anvithpothula/biohub-0-953-lb-original.

The upstream notebook is copied unchanged and one cell is appended. That cell
re-reads the written /kaggle/working/submission.csv through the CellOps
KaggleSubmissionCompiler (schema, per-dataset node IDs, edge integrity, coverage
of every test dataset, and coordinate bounds). It never rewrites the file, so the
upstream output is submitted byte-for-byte. A contract violation raises and fails
the run, instead of spending a leaderboard slot on a malformed file.

Usage: python build_notebook.py <path to upstream .ipynb>
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent

GUARD = r'''import json
import sys
from pathlib import Path

# biohub_cellops must be attached as an offline Kaggle dataset.
_cellops_modules = list(Path("/kaggle/input").rglob("biohub_cellops/submission_guard.py"))
assert _cellops_modules, "Attached inputs do not contain biohub_cellops"
if str(_cellops_modules[0].parents[1]) not in sys.path:
    sys.path.insert(0, str(_cellops_modules[0].parents[1]))

from biohub_cellops.submission_guard import KaggleSubmissionCompiler

_sub_path = Path("/kaggle/working/submission.csv")
_rows = KaggleSubmissionCompiler.read_csv(_sub_path)
_shapes = {}
for _zp in sorted(TEST_DIR.glob("*.zarr")):
    with (_zp / "0" / "zarr.json").open() as _f:
        _shapes[_zp.stem] = tuple(json.load(_f)["shape"])
_report = KaggleSubmissionCompiler.validate_dataset_context(_rows, _shapes)

with (COMP_DIR / "sample_submission.csv").open() as _f:
    _sample_header = _f.readline().strip().split(",")
assert _sample_header == KaggleSubmissionCompiler.columns, _sample_header

print(f"CellOps guard: PASS  {len(_rows)} rows, {len(_report)} datasets")
for _dataset, _counts in _report.items():
    print(f"  {_dataset}: {_counts}")
'''


def main(upstream: Path) -> None:
    nb = json.loads(upstream.read_text())
    for c in nb["cells"]:
        if c["cell_type"] == "code":
            c["outputs"], c["execution_count"] = [], None
    nb["cells"].append({"cell_type": "code", "metadata": {}, "outputs": [], "execution_count": None,
                        "source": GUARD.splitlines(keepends=True)})
    (HERE / "biohub-harmonic-submit.ipynb").write_text(json.dumps(nb, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
