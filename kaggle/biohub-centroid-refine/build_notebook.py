"""Build the centroid-refinement experiment notebook.

Takes the harmonic fork's submission.csv (attached as a kernel input), re-centres every
node on the local intensity centre of mass of the raw volume, and scores each variant
on the 4 visible test/ movies (byte copies of train samples, so they have GEFF labels).
Topology and node count are unchanged, so any score change comes from node matching.

The scorer is the comparison cell of kaggle/biohub-0953-scored, reused verbatim.
"""

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).parent

_spec = importlib.util.spec_from_file_location("scored", HERE.parent / "biohub-0953-scored" / "build_notebook.py")
_scored = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_scored)
SCORER = _scored.COMPARE.split("cmp_subs = ")[0]

INTRO = """# Centroid refinement of the harmonic fork, scored with the competition metric

The fork writes integer voxel centres (a z voxel is 1.625 um). Each variant moves every
node to the intensity-weighted centre of mass of a smoothed window around it, iterated
like mean-shift, then scores the result with and without rounding back to integers.
"""

INSTALL = r'''import subprocess, sys
from pathlib import Path

# The Kaggle image lacks zarr; install it offline from the fork's support-pack wheels.
try:
    import zarr  # noqa: F401
except ImportError:
    wheels = [w for w in Path("/kaggle/input").rglob("*.whl")
              if w.name.lower().split("-")[0] in {"zarr", "numcodecs", "donfig", "crc32c", "google_crc32c"}]
    print("installing", sorted(w.name for w in wheels))
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", *map(str, wheels)], check=True)
'''

SETUP = r'''import functools
import time
from pathlib import Path

import numpy as np
import pandas as pd
import zarr
from scipy.ndimage import gaussian_filter

COMP_DIR = Path("/kaggle/input/competitions/biohub-cell-tracking-during-development")
if not COMP_DIR.exists():
    COMP_DIR = Path("/kaggle/input/biohub-cell-tracking-during-development")
TEST_DIR = COMP_DIR / "test"
_subs = [p for p in Path("/kaggle/input").rglob("submission.csv") if "harmonic" in str(p)]
assert _subs, "harmonic fork submission.csv not attached"
SUB = pd.read_csv(_subs[0])
print(_subs[0], SUB.shape)
'''

REFINE = r'''cmp_load_gt = functools.lru_cache(maxsize=None)(cmp_load_gt)

SCALE = np.array([1.625, 0.40625, 0.40625])
VARIANTS = [
    {"name": f"s{s}_r{r}_i{i}", "smooth_um": s, "radius_um": r, "iters": i}
    for s in (0.5, 1.0) for r in (1.5, 2.5, 4.0) for i in (1, 3)
]


def window_offsets(radius_um):
    h = np.ceil(radius_um / SCALE).astype(int)
    g = np.stack(np.meshgrid(*[np.arange(-k, k + 1) for k in h], indexing="ij"), -1).reshape(-1, 3)
    return g[((g * SCALE) ** 2).sum(1) <= radius_um ** 2]


def refine_frame(vol, zyx, radius_um, iters):
    """Mean-shift each centre towards the background-subtracted intensity centre of mass."""
    off = window_offsets(radius_um)
    hi = np.array(vol.shape) - 1
    pos = zyx.astype(float).copy()
    for _ in range(iters):
        c = np.rint(pos).astype(int)
        idx = c[:, None, :] + off[None]
        inside = ((idx >= 0) & (idx <= hi)).all(2)
        idx = np.clip(idx, 0, hi)
        v = vol[idx[..., 0], idx[..., 1], idx[..., 2]]
        v = np.where(inside, v, np.nan)
        w = np.clip(v - np.nanmin(v, 1, keepdims=True), 0, None)
        w = np.nan_to_num(w)
        tot = w.sum(1)
        ok = tot > 0
        new = c + (w[:, :, None] * off[None]).sum(1) / np.where(ok, tot, 1)[:, None]
        pos = np.where(ok[:, None], new, pos)
    # Runaway guard: a node that drifted beyond the window radius keeps its original centre.
    drift = np.sqrt((((pos - zyx) * SCALE) ** 2).sum(1))
    pos[drift > radius_um] = zyx[drift > radius_um]
    return np.clip(pos, 0, hi)


refined = {v["name"]: SUB.copy() for v in VARIANTS}
for col in "zyx":
    for df in refined.values():
        df[col] = df[col].astype(float)

t0 = time.time()
nodes_all = SUB[SUB["row_type"] == "node"]
for stem, nodes in nodes_all.groupby("dataset"):
    img = zarr.open(str(TEST_DIR / f"{stem}.zarr"), mode="r")["0"]
    for t, fr in nodes.groupby("t"):
        raw = np.asarray(img[int(t)], dtype=np.float32)
        zyx = fr[["z", "y", "x"]].to_numpy(float)
        smoothed = {s: gaussian_filter(raw, s / SCALE) for s in {v["smooth_um"] for v in VARIANTS}}
        for v in VARIANTS:
            new = refine_frame(smoothed[v["smooth_um"]], zyx, v["radius_um"], v["iters"])
            refined[v["name"]].loc[fr.index, ["z", "y", "x"]] = new
    print(f"{stem}: {len(nodes)} nodes refined, {time.time() - t0:.0f}s")
'''

SCORE = r'''stems = sorted(s for s in SUB["dataset"].astype(str).unique() if (CMP_TRAIN / f"{s}.geff").exists())
node_mask = SUB["row_type"] == "node"


def shift_um(df):
    d = (df.loc[node_mask, ["z", "y", "x"]].to_numpy(float) - SUB.loc[node_mask, ["z", "y", "x"]].to_numpy(float)) * SCALE
    return float(np.sqrt((d ** 2).sum(1)).mean())


rows = []
cases = {"base": SUB}
for name, df in refined.items():
    cases[name] = df
    r = df.copy()
    r[["z", "y", "x"]] = np.rint(r[["z", "y", "x"]].to_numpy(float))
    cases[name + "_int"] = r
for name, df in cases.items():
    for stem in stems:
        rows.append({"variant": name, "dataset": stem, **cmp_score(df, stem)})
res = pd.DataFrame(rows)
res.to_csv("/kaggle/working/refine_results.csv", index=False)

summary = res.groupby("variant")[["tp", "fp", "fn"]].sum()
summary["J_micro"] = summary["tp"] / (summary["tp"] + summary["fp"] + summary["fn"])
summary["adj_mean"] = res.groupby("variant")["adj"].mean()
summary["mean_shift_um"] = [shift_um(cases[v]) for v in summary.index]
summary["dJ_vs_base"] = summary["J_micro"] - summary.loc["base", "J_micro"]
summary = summary.sort_values("J_micro", ascending=False)
summary.to_csv("/kaggle/working/refine_summary.csv")
print(summary.round(4).to_string())
print(res.pivot(index="variant", columns="dataset", values="J").round(4).to_string())
'''

DIAG = r'''# How far are the fork's matched centres from GT, and how many labelled cells go unmatched?
diag = []
nodes = SUB[node_mask]
for stem in stems:
    gt = cmp_load_gt(stem)
    part = nodes[nodes["dataset"] == stem]
    p_t = part["t"].to_numpy(np.int64)
    p_um = part[["z", "y", "x"]].to_numpy(float) * CMP_SCALE
    g_um = gt["zyx"] * CMP_SCALE
    m = cmp_match(p_t, p_um, gt["t"], g_um)
    hit = m >= 0
    err = p_um[hit] - g_um[m[hit]]
    d = np.sqrt((err ** 2).sum(1))
    diag.append({"dataset": stem, "gt_nodes": len(gt["t"]), "matched": int(hit.sum()),
                 "d_median": np.median(d), "d_p90": np.percentile(d, 90), "frac_gt_2um": (d > 2).mean(),
                 "abs_dz": np.abs(err[:, 0]).mean(), "abs_dy": np.abs(err[:, 1]).mean(),
                 "abs_dx": np.abs(err[:, 2]).mean()})
print(pd.DataFrame(diag).round(3).to_string())
'''


def cell(kind, src):
    c = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        c.update(outputs=[], execution_count=None)
    return c


def main() -> None:
    nb = {
        "nbformat": 4, "nbformat_minor": 4,
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                     "language_info": {"name": "python"}},
        "cells": [cell("markdown", INTRO), cell("code", INSTALL), cell("code", SETUP), cell("code", SCORER),
                  cell("code", REFINE), cell("code", SCORE), cell("code", DIAG)],
    }
    (HERE / "biohub-centroid-refine.ipynb").write_text(json.dumps(nb, indent=1))


if __name__ == "__main__":
    main()
