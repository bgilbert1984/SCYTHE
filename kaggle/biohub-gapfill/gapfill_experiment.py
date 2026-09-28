"""Gap-fill experiment: can our DoG detections repair the harmonic fork's tracks?

Runs locally (no Kaggle kernel). Scores the fork's submission and several repaired
variants on the 4 visible test/ movies, which are byte copies of train samples.

Inputs:
  --labels  directory holding <stem>.geff label stores for the 4 test copies. Each file
            downloads on its own, e.g.
            kaggle competitions download biohub-cell-tracking-during-development \
                -f train/44b6_0113de3b.geff/nodes/ids/c/0 -p <labels>/44b6_0113de3b.geff/nodes/ids/c
  --fork    submission.csv from the benspectrcydegilbert/biohub-harmonic-submit kernel
  --dog     submission.csv from the DoG+Trackastra notebook (v8, DOG_THR_PCT=85)

The scorer is the comparison cell of kaggle/biohub-0953-scored, reused verbatim.
Needs numpy, pandas, scipy and zarr>=3.
"""

import argparse
import functools
import importlib.util
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from scipy.spatial import cKDTree

HERE = Path(__file__).parent
STEMS = ["44b6_0113de3b", "44b6_0b24845f", "6bba_05b6850b", "6bba_05db0fb1"]


def load_scorer(labels: Path) -> dict:
    spec = importlib.util.spec_from_file_location("scored", HERE.parent / "biohub-0953-scored" / "build_notebook.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ns = {"COMP_DIR": labels}
    exec(mod.COMPARE.split("cmp_subs = ")[0], ns)
    ns["CMP_TRAIN"] = labels
    ns["cmp_load_gt"] = functools.lru_cache(maxsize=None)(ns["cmp_load_gt"])
    return ns


def micro(ns, sub):
    r = pd.DataFrame([{"dataset": s, **ns["cmp_score"](sub, s)} for s in STEMS])
    tp, fp, fn = r.tp.sum(), r.fp.sum(), r.fn.sum()
    return tp, fp, fn, tp / (tp + fp + fn), r


def classify_missed_edges(ns, fork):
    """Why each labelled edge the fork misses is missed."""
    scale, counts = ns["CMP_SCALE"], Counter()
    for s in STEMS:
        gt = ns["cmp_load_gt"](s)
        part = fork[fork.dataset == s]
        nodes = part[part.row_type == "node"].reset_index(drop=True)
        row = {v: i for i, v in enumerate(nodes.node_id)}
        ed = part[part.row_type == "edge"]
        e = np.array([(row[a], row[b]) for a, b in zip(ed.source_id, ed.target_id)])
        m = ns["cmp_match"](nodes.t.values, nodes[["z", "y", "x"]].values * scale, gt["t"], gt["zyx"] * scale)
        g2p = {g: p for p, g in enumerate(m) if g >= 0}
        out = np.bincount(e[:, 0], minlength=len(nodes))
        inn = np.bincount(e[:, 1], minlength=len(nodes))
        linked = set(map(tuple, e.tolist()))
        for a, b in gt["edges"]:
            pa, pb = g2p.get(a), g2p.get(b)
            if pa is not None and pb is not None and (pa, pb) in linked:
                continue
            if pa is None or pb is None:
                counts["a labelled node is undetected"] += 1
            else:
                counts[f"both detected, not linked (source out={out[pa]}, target in={inn[pb]})"] += 1
    return counts


def gapfill(ns, fork, dog, r2=8.0, snap=3.0, excl=4.0, mode="dog"):
    """Bridge a track end at t to a track start at t+2 through a new node at t+1.

    mode="dog" uses the DoG detection nearest the midpoint (within snap um); mode="interp"
    uses the midpoint. Skipped when a fork node already sits within excl um of it.
    """
    scale = ns["CMP_SCALE"]
    out_rows, added = [fork], 0
    next_id = int(fork.loc[fork.row_type == "node", "node_id"].max()) + 1
    next_row = int(fork["id"].max()) + 1
    for s, part in fork.groupby("dataset"):
        n, e = part[part.row_type == "node"], part[part.row_type == "edge"]
        um = n[["z", "y", "x"]].to_numpy(float) * scale
        t, ids = n.t.to_numpy(), n.node_id.to_numpy()
        trees = {tt: cKDTree(um[t == tt]) for tt in np.unique(t)}
        dg = dog[(dog.dataset == s) & (dog.row_type == "node")]
        dum, dt = dg[["z", "y", "x"]].to_numpy(float) * scale, dg.t.to_numpy()
        dtrees = {tt: (cKDTree(dum[dt == tt]), dum[dt == tt]) for tt in np.unique(dt)}
        ends = np.flatnonzero(~np.isin(ids, e.source_id.values) & (t < t.max() - 1))
        starts = np.flatnonzero(~np.isin(ids, e.target_id.values) & (t > 1))
        new_nodes, new_edges = [], []
        for tt in np.unique(t[ends]):
            a, b = ends[t[ends] == tt], starts[t[starts] == tt + 2]
            if not len(a) or not len(b):
                continue
            cost = np.sqrt(((um[a][:, None] - um[b][None]) ** 2).sum(2))
            cost = np.where(cost <= r2, cost, 1e6)
            for i, j in zip(*linear_sum_assignment(cost)):
                if cost[i, j] >= 1e6:
                    continue
                pos = (um[a[i]] + um[b[j]]) / 2
                if mode == "dog":
                    if tt + 1 not in dtrees:
                        continue
                    tree, pts = dtrees[tt + 1]
                    d, k = tree.query(pos)
                    if d > snap:
                        continue
                    pos = pts[k]
                if tt + 1 in trees and trees[tt + 1].query(pos)[0] <= excl:
                    continue
                new_nodes.append((s, next_id, tt + 1, *np.rint(pos / scale).astype(int)))
                new_edges += [(s, ids[a[i]], next_id), (s, next_id, ids[b[j]])]
                next_id += 1
        rows = [{"dataset": s, "row_type": "node", "node_id": nid, "t": tt, "z": z, "y": y, "x": x,
                 "source_id": -1, "target_id": -1} for s, nid, tt, z, y, x in new_nodes]
        rows += [{"dataset": s, "row_type": "edge", "node_id": -1, "t": -1, "z": -1, "y": -1, "x": -1,
                  "source_id": a, "target_id": b} for s, a, b in new_edges]
        if rows:
            df = pd.DataFrame(rows)
            df.insert(0, "id", range(next_row, next_row + len(df)))
            next_row += len(df)
            out_rows.append(df)
            added += len(new_nodes)
    return pd.concat(out_rows, ignore_index=True), added


def orphan_insert(ns, fork, dog, excl):
    """Add DoG+Trackastra edges whose endpoints are both farther than excl um from any fork node."""
    scale = ns["CMP_SCALE"]
    out = [fork]
    next_id = int(fork.loc[fork.row_type == "node", "node_id"].max()) + 1
    next_row = int(fork["id"].max()) + 1
    added = 0
    for s in STEMS:
        fn = fork[(fork.dataset == s) & (fork.row_type == "node")]
        fum = fn[["z", "y", "x"]].values * scale
        trees = {t: cKDTree(fum[fn.t.values == t]) for t in np.unique(fn.t)}
        part = dog[dog.dataset == s]
        dn, de = part[part.row_type == "node"], part[part.row_type == "edge"]
        far = {nid: t not in trees or trees[t].query(p)[0] > excl
               for nid, t, p in zip(dn.node_id, dn.t, dn[["z", "y", "x"]].values * scale)}
        keep = [(a, b) for a, b in zip(de.source_id, de.target_id) if far[a] and far[b]]
        ids = sorted({x for pair in keep for x in pair})
        remap = {old: next_id + i for i, old in enumerate(ids)}
        next_id += len(ids)
        nn = dn[dn.node_id.isin(ids)].drop(columns="id")
        nn["node_id"] = nn.node_id.map(remap)
        ee = pd.DataFrame([{"dataset": s, "row_type": "edge", "node_id": -1, "t": -1, "z": -1, "y": -1, "x": -1,
                            "source_id": remap[a], "target_id": remap[b]} for a, b in keep])
        df = pd.concat([nn, ee], ignore_index=True)
        if len(df):
            df.insert(0, "id", range(next_row, next_row + len(df)))
            next_row += len(df)
            out.append(df)
            added += len(nn)
    return pd.concat(out, ignore_index=True), added


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--fork", type=Path, required=True)
    ap.add_argument("--dog", type=Path, required=True)
    args = ap.parse_args()
    ns = load_scorer(args.labels)
    fork, dog = pd.read_csv(args.fork), pd.read_csv(args.dog)

    base = micro(ns, fork)
    print(f"base: TP {base[0]} FP {base[1]} FN {base[2]} J {base[3]:.4f}\n")
    for reason, n in sorted(classify_missed_edges(ns, fork).items()):
        print(f"  missed edges, {reason}: {n}")
    print()

    def report(label, sub, added):
        tp, fp, fn, j, r = micro(ns, sub)
        print(f"{label:34s} +{added:5d} nodes  TP {tp} FP {fp} FN {fn}  J {j:.4f}  dJ {j - base[3]:+.4f}")

    for mode in ("dog", "interp"):
        for r2 in (5.0, 8.0, 12.0):
            for excl in (2.0, 4.0):
                report(f"gap2 {mode} r2={r2} excl={excl}", *gapfill(ns, fork, dog, r2=r2, excl=excl, mode=mode))
    for excl in (5.0, 7.0, 9.0, 12.0):
        report(f"orphan DoG tracks excl={excl}", *orphan_insert(ns, fork, dog, excl))


if __name__ == "__main__":
    main()
