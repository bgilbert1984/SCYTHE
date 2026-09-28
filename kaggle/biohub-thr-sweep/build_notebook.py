"""Build the DoG-threshold sweep notebook from the submission notebook's cells.

Detection, masking, and tracking cells are copied verbatim so the sweep measures
exactly the code that gets submitted; only the scoring and sweep cells are new.
"""

import json
from pathlib import Path

HERE = Path(__file__).parent
SOURCE = HERE.parent / "notebookf5391491c2" / "notebookf5391491c2.ipynb"
REUSED_CELLS = (3, 7, 9, 11, 13, 15, 17, 21)  # install, imports, io, iso, DoG, masks, model, tracking

INTRO = """# DoG threshold sweep, scored with the competition metric

Sweeps `DOG_THR_PCT` through the submission notebook's own detect -> mask -> Trackastra
pipeline on train movies, scoring each with a re-implementation of the organisers'
metric (after sleepymegacat/the-metric-decides-your-architecture-8-measured):
node-count-adjusted edge Jaccard. Division Jaccard (0.1 weight) is not scored.

The visible test/ volumes are copies of train samples, so they are excluded here.
"""

METRIC = r'''MAX_D_UM = 7.0
NODE_ALPHA = 0.1


def load_gt(zarr_path: Path) -> dict:
    import zarr as _zarr

    root = zarr_path.with_suffix(".geff")
    g = _zarr.open(str(root), mode="r")
    ids = np.asarray(g["nodes/ids"])
    row_of = {int(v): i for i, v in enumerate(ids)}
    raw_edges = np.asarray(g["edges/ids"]).reshape(-1, 2)
    edges = np.array([[row_of[int(a)], row_of[int(b)]] for a, b in raw_edges], dtype=np.int64).reshape(-1, 2)
    with (root / "zarr.json").open() as f:
        meta = json.load(f)["attributes"]["geff"]
    return {
        "t": np.asarray(g["nodes/props/t/values"]).astype(np.int64),
        "zyx": np.stack([np.asarray(g[f"nodes/props/{k}/values"]) for k in "zyx"], 1).astype(float),
        "edges": edges,
        "est_total": int((meta.get("extra") or {}).get("estimated_number_of_nodes", 0)),
    }


def match_nodes(p_t, p_um, g_t, g_um):
    """One-to-one per-frame matching maximising sum 1/(1+d) within MAX_D_UM."""
    matched = np.full(len(p_t), -1, np.int64)
    for t in np.intersect1d(np.unique(p_t), np.unique(g_t)):
        pi, gi = np.flatnonzero(p_t == t), np.flatnonzero(g_t == t)
        d = np.sqrt(((g_um[gi][:, None] - p_um[pi][None]) ** 2).sum(2))
        w = np.where(d <= MAX_D_UM, 1.0 / (1.0 + d), -1.0)
        if not (w > -1.0).any():
            continue
        r, c = linear_sum_assignment(w, maximize=True)
        keep = w[r, c] > -1.0
        matched[pi[c[keep]]] = gi[r[keep]]
    return matched


def score_sample(nodes: list[NodeRow], edges: list[EdgeRow], gt: dict) -> dict:
    """Node-count-adjusted edge Jaccard, mirroring the scorer's edge filters."""
    row_of = {n.node_id: i for i, n in enumerate(nodes)}
    p_t = np.array([n.t for n in nodes], dtype=np.int64)
    p_zyx = np.array([(n.z, n.y, n.x) for n in nodes], dtype=float).reshape(-1, 3)
    n_gt_edges = len(gt["edges"])
    e = np.array([(row_of[x.source_id], row_of[x.target_id]) for x in edges], dtype=np.int64).reshape(-1, 2)
    if not len(nodes) or not len(e):
        return {"J": 0.0, "adj": 0.0, "n_pred": len(nodes), "n_est": gt["est_total"]}

    m = match_nodes(p_t, p_zyx * SCALE_ZYX, gt["t"], gt["zyx"] * SCALE_ZYX)
    e = np.unique(e, axis=0)                          # duplicate pairs count once
    e = e[p_t[e[:, 1]] - p_t[e[:, 0]] == 1]           # only dt == 1 edges are scored
    e = e[np.lexsort((e[:, 1], e[:, 0]))]
    rank = np.ones(len(e), np.int64)                  # out-degree capped at 2
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
    fp, fn = int(graded.sum()) - tp, n_gt_edges - tp
    j = tp / max(tp + fp + fn, 1)
    n_est = gt["est_total"]
    adj = max(0.0, j * (1 - NODE_ALPHA * (len(nodes) - n_est) / n_est)) if n_est else j
    return {"J": round(j, 4), "adj": round(adj, 4), "tp": tp, "fp": fp, "fn": fn,
            "n_pred": len(nodes), "n_est": n_est, "node_ratio": round(len(nodes) / max(n_est, 1), 3)}
'''

SWEEP = r'''import time

SWEEP_THR_PCT = (85.0, 90.0, 95.0)   # 80 = current submission setting; 50-80 swept in v1
SAMPLES_PER_EMBRYO = 2

_test_stems = {p.stem for p in TEST_DIR.glob("*.zarr")}
_by_embryo: dict[str, list[Path]] = {}
for _zp in sorted(TRAIN_DIR.glob("*.zarr")):
    if _zp.stem not in _test_stems and _zp.with_suffix(".geff").exists():
        _by_embryo.setdefault(_zp.stem.split("_")[0], []).append(_zp)
sweep_paths = [p for paths in _by_embryo.values() for p in paths[:SAMPLES_PER_EMBRYO]]
print("sweep movies:", [p.stem for p in sweep_paths])

results = []
for zp in sweep_paths:
    gt = load_gt(zp)
    for thr in SWEEP_THR_PCT:
        # track_one_dataset calls detect_peaks_dog with its defaults, so swap the default threshold in.
        detect_peaks_dog.__defaults__ = (thr, None)
        t0 = time.time()
        nodes, edges, _ = track_one_dataset(zp)
        row = {"dataset": zp.stem, "thr_pct": thr, "sec": round(time.time() - t0),
               "n_edges": len(edges), **score_sample(nodes, edges, gt)}
        results.append(row)
        print(row, flush=True)
detect_peaks_dog.__defaults__ = (DOG_THR_PCT, None)

df = pd.DataFrame(results)
df.to_csv(OUTPUT_DIR / "thr_sweep.csv", index=False)
print(df.pivot(index="dataset", columns="thr_pct", values="adj"))
print("\nmean adj by threshold:\n", df.groupby("thr_pct")[["J", "adj", "node_ratio", "sec"]].mean())
'''


def cell(kind, text):
    base = {"cell_type": kind, "metadata": {}, "source": text.splitlines(keepends=True)}
    if kind == "code":
        base.update(outputs=[], execution_count=None)
    return base


def main():
    src = json.loads(SOURCE.read_text())
    cells = [cell("markdown", INTRO)]
    for i in REUSED_CELLS:
        c = dict(src["cells"][i])
        c.update(outputs=[], execution_count=None)
        cells.append(c)
    cells += [cell("code", METRIC), cell("code", SWEEP)]
    nb = {"cells": cells, "metadata": src["metadata"], "nbformat": src["nbformat"],
          "nbformat_minor": src["nbformat_minor"]}
    (HERE / "biohub-thr-sweep.ipynb").write_text(json.dumps(nb, indent=1))


if __name__ == "__main__":
    main()
