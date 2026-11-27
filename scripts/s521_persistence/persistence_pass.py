"""Narrowband-line persistence pass over the §5.21 antenna-on observation runs (observation only, no demodulation).

For every spool window (8 MiB of interleaved uint8 I/Q at 2.048 MS/s) it finds narrow spectral lines, converts them to absolute
frequency (tuned centre + offset), and aggregates how often each 1 kHz frequency bin shows a line in the visits that cover it.
A line is "RF-fixed" only if it is seen at absolute-frequency-consistent positions from visits whose tuned centres differ by
at least 50 kHz (the schedule retunes the same tuning by -100/+200/-50 kHz for this reason); a line that stays at the same
offset from the centre is an instrument spur, not RF. Output is frequency, persistence and SNR: no audio and no content.

usage: persistence_pass.py detect OUT_DIR [--local-root ~/s521_runs] [--workers 3]
       persistence_pass.py aggregate OUT_DIR
       persistence_pass.py classify OUT_DIR
Inputs: the dataset's metadata/captures.parquet (path via --captures) and, for windows not on this host, the public dataset.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np

REPO_ID = "bgilbert1984/scythe-uhf-rtl-sdr-evidence"
FS = 2_048_000.0
NFFT = 4096                      # 500 Hz bins
BIN_HZ = FS / NFFT
EDGE_HZ = 920_000.0              # beyond this the anti-alias roll-off dominates
DC_GUARD_HZ = 15_000.0
CANARY_HZ = 255_996.0            # receiver artefact lines of the frozen canary gate; excluded from detection
CANARY_GUARD_HZ = 1_500.0
FLOOR_HALF = 100                 # +/-100 bins (50 kHz) running median for the local floor
DETECT_DB = 10.0                 # peak over local floor
MAX_WIDTH_BINS = 12              # lines wider than 6 kHz at the threshold are not "narrowband"
CLIP_MAX_PCT = 0.001             # visits above the frozen clipping limit are excluded (overload makes spurs)
LOCAL_DIRS = {
    "run_20261122_harbourpointe_r2": "antenna_on_harbourpointe_20261122_r2",
    "run_20261124_hwy99_ofinnigans": "antenna_on_ofinnigans_20261124",
    "run_20261126_hwy99": "antenna_on_hwy99_20261126",
    "run_20261126_sea6thave_r2": "antenna_on_6thave_20261126_r2",
}
SITE_OF = {  # published site keys
    "run_20261117_mukilteo": "muk12502", "run_20261118_mukilteo": "muk12502", "run_20261118_mukilteo_12426": "muk12426",
    "run_20261119_hwy99": "hwy99", "run_20261122_harbourpointe_r2": "harbourpointe",
    "run_20261124_hwy99_ofinnigans": "hwy99", "run_20261126_hwy99": "hwy99", "run_20261126_sea6thave_r2": "sea6thave",
}


def running_median(x, half):
    """Median over a +/-half window, computed on 8-bin blocks and interpolated (the floor varies slowly)."""
    n = len(x)
    pad = np.pad(x, half, mode="edge")
    step = 8
    centres = np.arange(0, n, step)
    med = np.array([np.median(pad[c:c + 2 * half + 1]) for c in centres])
    return np.interp(np.arange(n), centres, med)


def psd_db(raw):
    iq = np.frombuffer(raw, dtype=np.uint8).astype(np.float32).reshape(-1, 2)
    z = (iq[:, 0] - 127.5) + 1j * (iq[:, 1] - 127.5)
    seg = len(z) // NFFT
    z = z[: seg * NFFT].reshape(seg, NFFT) * np.hanning(NFFT).astype(np.float32)
    p = (np.abs(np.fft.fftshift(np.fft.fft(z, axis=1), axes=1)) ** 2).mean(axis=0)
    return 10.0 * np.log10(p + 1e-12)


def detect_lines(raw):
    """Return [(offset_hz, snr_db)] of narrow lines in one window."""
    db = psd_db(raw)
    floor = running_median(db, FLOOR_HALF)
    snr = db - floor
    off = (np.arange(NFFT) - NFFT // 2) * BIN_HZ
    ok = (np.abs(off) < EDGE_HZ) & (np.abs(off) > DC_GUARD_HZ) & (np.abs(np.abs(off) - CANARY_HZ) > CANARY_GUARD_HZ)
    out = []
    i = 1
    while i < NFFT - 1:
        if ok[i] and snr[i] >= DETECT_DB and snr[i] >= snr[i - 1] and snr[i] >= snr[i + 1]:
            lo = i
            while lo > 0 and snr[lo - 1] >= DETECT_DB - 6.0 and i - lo < MAX_WIDTH_BINS:
                lo -= 1
            hi = i
            while hi < NFFT - 1 and snr[hi + 1] >= DETECT_DB - 6.0 and hi - i < MAX_WIDTH_BINS:
                hi += 1
            if hi - lo + 1 <= MAX_WIDTH_BINS:
                out.append((float(off[i]), float(snr[i])))
            i = hi + 1
        else:
            i += 1
    return out


def read_window(args):
    set_id, rel, local_root = args
    sub = rel.split(f"data/runs/{set_id}/", 1)[1]
    if set_id in LOCAL_DIRS:
        with open(os.path.join(local_root, LOCAL_DIRS[set_id], sub), "rb") as fh:
            return fh.read()
    from huggingface_hub import hf_hub_download
    p = hf_hub_download(REPO_ID, rel, repo_type="dataset")
    with open(p, "rb") as fh:
        raw = fh.read()
    try:
        os.remove(os.path.realpath(p))          # the cache copy is 8 MiB; keep disk flat
        os.remove(p)
    except OSError:
        pass
    return raw


def one_visit(job):
    set_id, rel, centre, delta, pos, local_root = job
    try:
        raw = read_window((set_id, rel, local_root))
        return {"set_id": set_id, "position": pos, "centre_hz": centre, "retune_delta_hz": delta, "lines": detect_lines(raw), "error": None}
    except Exception as exc:  # recorded, not hidden
        return {"set_id": set_id, "position": pos, "centre_hz": centre, "retune_delta_hz": delta, "lines": [], "error": repr(exc)[:200]}


def cmd_detect(a):
    import pandas as pd
    c = pd.read_parquet(a.captures)
    c = c[c.kind == "observation_run_visit"].sort_values(["set_id", "visit_position"])
    os.makedirs(a.out, exist_ok=True)
    for set_id, grp in c.groupby("set_id"):
        dest = os.path.join(a.out, f"{set_id}.json")
        if os.path.exists(dest):
            print("skip", set_id, flush=True)
            continue
        jobs = [(set_id, r.file, float(r.centre_hz), float(r.retune_delta_hz), int(r.visit_position), a.local_root)
                for r in grp.itertuples()]
        clipped = {int(r.visit_position): float(r.clipping_pct) for r in grp.itertuples()}
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            res = list(ex.map(one_visit, jobs, chunksize=4))
        for r in res:
            r["clipping_pct"] = clipped[r["position"]]
        errs = [r for r in res if r["error"]]
        json.dump({"set_id": set_id, "site_key": SITE_OF[set_id], "visits": res}, open(dest, "w"))
        print(f"{set_id}: {len(res)} visits, {len(errs)} errors, {sum(len(r['lines']) for r in res)} line detections", flush=True)
        if errs:
            print("  first error:", errs[0]["error"], flush=True)


def aggregate_run(doc, f_lo=398e6, f_hi=472e6):
    """Per-run table: for each 1 kHz bin, covering visits, detecting visits, SNR and offset spread."""
    nb = int((f_hi - f_lo) / 1000)
    cover = np.zeros(nb, dtype=np.int32)
    det = np.zeros(nb, dtype=np.int32)
    snr_sum = np.zeros(nb)
    offs = [[] for _ in range(nb)]
    used = 0
    for v in doc["visits"]:
        if v["error"] or v["clipping_pct"] > CLIP_MAX_PCT:
            continue
        used += 1
        c = v["centre_hz"]
        lo = int((c - EDGE_HZ - f_lo) / 1000)
        hi = int((c + EDGE_HZ - f_lo) / 1000)
        lo, hi = max(lo, 0), min(hi, nb)
        cover[lo:hi] += 1
        seen = set()
        for off, snr in v["lines"]:
            k = int((c + off - f_lo) / 1000 + 0.5)
            if 0 <= k < nb and k not in seen:
                seen.add(k)
                det[k] += 1
                snr_sum[k] += snr
                offs[k].append(off)
    return {"cover": cover, "det": det, "snr_sum": snr_sum, "offs": offs, "visits_used": used, "f_lo": f_lo}


def lines_of(agg, min_cover=3, min_persist=0.6):
    out = []
    cover, det = agg["cover"], agg["det"]
    k = 0
    nb = len(cover)
    while k < nb:
        if cover[k] >= min_cover and det[k] > 0 and det[k] / cover[k] >= min_persist:
            j = k
            while j + 1 < nb and det[j + 1] > 0 and cover[j + 1] >= min_cover and det[j + 1] / cover[j + 1] >= min_persist:
                j += 1
            best = max(range(k, j + 1), key=lambda q: det[q])
            offsets = agg["offs"][best]
            out.append({"freq_hz": agg["f_lo"] + best * 1000.0, "persistence": round(float(det[best] / cover[best]), 3),
                        "covering_visits": int(cover[best]), "detecting_visits": int(det[best]),
                        "mean_snr_db": round(float(agg["snr_sum"][best] / det[best]), 1),
                        "centre_offset_spread_khz": round((max(offsets) - min(offsets)) / 1000.0, 1) if offsets else 0.0,
                        "rf_fixed": bool(offsets and (max(offsets) - min(offsets)) >= 50_000.0)})
            k = j + 1
        else:
            k += 1
    return out


def cmd_aggregate(a):
    runs = {}
    for name in sorted(os.listdir(a.out)):
        if not name.endswith(".json") or name.startswith(("lines_", "summary")):
            continue
        doc = json.load(open(os.path.join(a.out, name)))
        agg = aggregate_run(doc)
        runs[doc["set_id"]] = {"site_key": doc["site_key"], "visits_used": agg["visits_used"], "lines": lines_of(agg)}
    json.dump(runs, open(os.path.join(a.out, "summary_lines.json"), "w"), indent=1)
    for sid, r in runs.items():
        fx = [x for x in r["lines"] if x["rf_fixed"]]
        print(f"{sid} ({r['site_key']}): visits used {r['visits_used']}, persistent lines {len(r['lines'])}, RF-fixed {len(fx)}")

REF_HZ = 28_800_000.0            # the receiver's reference clock; its harmonics are instrument lines, not RF


def presence(aggs, f_hz, tol_bins=2):
    """Per run: (covering visits, detecting visits) at absolute frequency f_hz, taking the best bin within +/-tol_bins kHz."""
    out = {}
    for sid, ag in aggs.items():
        k = int((f_hz - ag["f_lo"]) / 1000 + 0.5)
        lo, hi = max(k - tol_bins, 0), min(k + tol_bins + 1, len(ag["cover"]))
        if lo >= hi:
            out[sid] = (0, 0)
            continue
        j = max(range(lo, hi), key=lambda q: ag["det"][q])
        out[sid] = (int(ag["cover"][j]), int(ag["det"][j]))
    return out


def cmd_classify(a):
    docs = {}
    for name in sorted(os.listdir(a.out)):
        if name.endswith(".json") and not name.startswith(("lines_", "summary")):
            d = json.load(open(os.path.join(a.out, name)))
            docs[d["set_id"]] = d
    aggs = {sid: aggregate_run(d) for sid, d in docs.items()}
    lines = []
    for sid, ag in aggs.items():
        for ln in lines_of(ag):
            if ln["rf_fixed"]:
                lines.append((ln["freq_hz"], sid, ln))
    lines.sort(key=lambda x: x[0])
    groups = []
    for item in lines:
        if groups and item[0] - groups[-1][-1][0] <= 3000.0:
            groups[-1].append(item)
        else:
            groups.append([item])
    out = []
    for g in groups:
        f = float(np.mean([x[0] for x in g]))
        pres = presence(aggs, f)
        site_of = {sid: docs[sid]["site_key"] for sid in docs}
        covering = [s for s, (c, d) in pres.items() if c >= 3]
        detecting = [s for s, (c, d) in pres.items() if c >= 3 and d / c >= 0.5]
        det_sites = sorted({site_of[s] for s in detecting})
        cov_sites = sorted({site_of[s] for s in covering})
        h = round(f / REF_HZ)
        if h >= 1 and abs(f - h * REF_HZ) <= 2000.0:
            cls = "INSTRUMENT_REFERENCE_HARMONIC"
        elif len(detecting) >= 2 and len(detecting) == len(covering) and len(cov_sites) >= 3:
            cls = "EVERY_COVERED_RUN_AT_EVERY_SITE"
        elif len(detecting) >= 2 and len(det_sites) == 1:
            others = [s for s in covering if site_of[s] != det_sites[0]]
            quiet = [s for s in others if pres[s][1] / pres[s][0] < 0.2]
            cls = "SITE_LOCAL_PERSISTENT" if others and len(quiet) == len(others) else "MULTI_RUN_SINGLE_SITE_OTHERS_UNCLEAR"
        elif len(detecting) >= 2:
            cls = "MULTI_SITE_PARTIAL"
        else:
            cls = "SINGLE_RUN"
        out.append({"freq_hz": round(f, 1), "class": cls, "runs_detecting": len(detecting), "runs_covering": len(covering),
                    "sites_detecting": det_sites, "sites_covering": cov_sites,
                    "per_run": {s: {"covering": c, "detecting": d} for s, (c, d) in pres.items() if c},
                    "max_snr_db": max(x[2]["mean_snr_db"] for x in g)})
    json.dump(out, open(os.path.join(a.out, "summary_classified.json"), "w"), indent=1)
    from collections import Counter
    print(Counter(x["class"] for x in out))
    for x in out:
        if x["class"] in ("SITE_LOCAL_PERSISTENT", "EVERY_COVERED_RUN_AT_EVERY_SITE", "MULTI_SITE_PARTIAL", "INSTRUMENT_REFERENCE_HARMONIC"):
            print(f"{x['freq_hz']/1e6:10.4f} MHz  {x['class']:34s} runs {x['runs_detecting']}/{x['runs_covering']}  sites {','.join(x['sites_detecting'])}  snr {x['max_snr_db']}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect")
    d.add_argument("out")
    d.add_argument("--captures", required=True)
    d.add_argument("--local-root", default=os.path.expanduser("~/s521_runs"))
    d.add_argument("--workers", type=int, default=3)
    g = sub.add_parser("aggregate")
    g.add_argument("out")
    k = sub.add_parser("classify")
    k.add_argument("out")
    a = ap.parse_args()
    return {"detect": cmd_detect, "aggregate": cmd_aggregate, "classify": cmd_classify}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
