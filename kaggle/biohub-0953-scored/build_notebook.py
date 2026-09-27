"""Build a private copy of anvithpothula/biohub-0-953-lb-original with a comparison cell.

The upstream notebook is copied unchanged; one cell is appended that scores both this
run's submission.csv and our DoG+Trackastra v7 submission (attached as a kernel input)
on the visible test/ movies, which are byte copies of train samples and so have GEFF
labels under train/. Same scorer as kaggle/biohub-thr-sweep.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent

COMPARE = r'''import json
from pathlib import Path

import numpy as np
import pandas as pd
import zarr
from scipy.optimize import linear_sum_assignment

CMP_SCALE = np.array([1.625, 0.40625, 0.40625])
CMP_MAX_D_UM = 7.0
CMP_TRAIN = COMP_DIR / "train"


def cmp_load_gt(stem: str) -> dict:
    root = CMP_TRAIN / f"{stem}.geff"
    g = zarr.open(str(root), mode="r")
    ids = np.asarray(g["nodes/ids"])
    row_of = {int(v): i for i, v in enumerate(ids)}
    raw = np.asarray(g["edges/ids"]).reshape(-1, 2)
    with (root / "zarr.json").open() as f:
        meta = json.load(f)["attributes"]["geff"]
    return {
        "t": np.asarray(g["nodes/props/t/values"]).astype(np.int64),
        "zyx": np.stack([np.asarray(g[f"nodes/props/{k}/values"]) for k in "zyx"], 1).astype(float),
        "edges": np.array([[row_of[int(a)], row_of[int(b)]] for a, b in raw], dtype=np.int64).reshape(-1, 2),
        "est_total": int((meta.get("extra") or {}).get("estimated_number_of_nodes", 0)),
    }


def cmp_match(p_t, p_um, g_t, g_um):
    matched = np.full(len(p_t), -1, np.int64)
    for t in np.intersect1d(np.unique(p_t), np.unique(g_t)):
        pi, gi = np.flatnonzero(p_t == t), np.flatnonzero(g_t == t)
        d = np.sqrt(((g_um[gi][:, None] - p_um[pi][None]) ** 2).sum(2))
        w = np.where(d <= CMP_MAX_D_UM, 1.0 / (1.0 + d), -1.0)
        if not (w > -1.0).any():
            continue
        r, c = linear_sum_assignment(w, maximize=True)
        keep = w[r, c] > -1.0
        matched[pi[c[keep]]] = gi[r[keep]]
    return matched


def cmp_score(sub: pd.DataFrame, stem: str) -> dict:
    """Node-count-adjusted edge Jaccard for one dataset of a submission."""
    gt = cmp_load_gt(stem)
    part = sub[sub["dataset"] == stem]
    nodes = part[part["row_type"] == "node"]
    row_of = {int(v): i for i, v in enumerate(nodes["node_id"])}
    p_t = nodes["t"].to_numpy(np.int64)
    p_zyx = nodes[["z", "y", "x"]].to_numpy(float)
    ed = part[part["row_type"] == "edge"]
    e = np.array([(row_of[int(a)], row_of[int(b)]) for a, b in zip(ed["source_id"], ed["target_id"])],
                 dtype=np.int64).reshape(-1, 2)
    n_est = gt["est_total"]
    if not len(e):
        return {"J": 0.0, "adj": 0.0, "n_pred": len(nodes), "n_est": n_est}

    m = cmp_match(p_t, p_zyx * CMP_SCALE, gt["t"], gt["zyx"] * CMP_SCALE)
    e = np.unique(e, axis=0)
    e = e[p_t[e[:, 1]] - p_t[e[:, 0]] == 1]
    e = e[np.lexsort((e[:, 1], e[:, 0]))]
    rank = np.ones(len(e), np.int64)
    for i in range(1, len(e)):
        rank[i] = rank[i - 1] + 1 if e[i, 0] == e[i - 1, 0] else 1
    e = e[rank <= 2]

    ms, mt = m[e[:, 0]], m[e[:, 1]]
    gt_set = set(map(tuple, gt["edges"].tolist()))
    tp = sum((int(a), int(b)) in gt_set for a, b in zip(ms, mt) if a >= 0 and b >= 0)
    n_gt = len(gt["t"])
    gt_out = np.bincount(gt["edges"][:, 0], minlength=n_gt)
    gt_in = np.bincount(gt["edges"][:, 1], minlength=n_gt)
    graded = (np.where(ms >= 0, gt_out[np.clip(ms, 0, None)] > 0, False)
              | np.where(mt >= 0, gt_in[np.clip(mt, 0, None)] > 0, False))
    fp, fn = int(graded.sum()) - tp, len(gt["edges"]) - tp
    j = tp / max(tp + fp + fn, 1)
    adj = max(0.0, j * (1 - 0.1 * (len(nodes) - n_est) / n_est)) if n_est else j
    return {"J": round(j, 4), "adj": round(adj, 4), "tp": tp, "fp": fp, "fn": fn,
            "n_pred": len(nodes), "n_est": n_est, "node_ratio": round(len(nodes) / max(n_est, 1), 3)}


cmp_subs = {"harmonic_0953": Path("/kaggle/working/submission.csv")}
_ours = [p for p in Path("/kaggle/input").rglob("submission.csv") if "notebookf5391491c2" in str(p)]
if _ours:
    cmp_subs["dog_trackastra_v7"] = _ours[0]
else:
    print("WARNING: v7 submission input not found; scoring harmonic only")

cmp_rows = []
for label, path in cmp_subs.items():
    sub = pd.read_csv(path)
    for stem in sorted(sub["dataset"].astype(str).unique()):
        if (CMP_TRAIN / f"{stem}.geff").exists():
            cmp_rows.append({"pipeline": label, "dataset": stem, **cmp_score(sub, stem)})
cmp_df = pd.DataFrame(cmp_rows)
cmp_df.to_csv("/kaggle/working/pipeline_comparison.csv", index=False)
print(cmp_df.to_string())
print(cmp_df.pivot(index="dataset", columns="pipeline", values="adj"))
print(cmp_df.groupby("pipeline")[["J", "adj", "node_ratio"]].mean())
'''


def main(upstream: Path) -> None:
    nb = json.loads(upstream.read_text())
    nb["cells"].append({"cell_type": "code", "metadata": {}, "outputs": [], "execution_count": None,
                        "source": COMPARE.splitlines(keepends=True)})
    for c in nb["cells"]:
        if c["cell_type"] == "code":
            c["outputs"], c["execution_count"] = [], None
    (HERE / "biohub-0953-scored.ipynb").write_text(json.dumps(nb, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
