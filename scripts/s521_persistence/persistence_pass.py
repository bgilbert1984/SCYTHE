"""Narrowband-line persistence pass over the §5.21 antenna-on observation runs (observation only, no demodulation).

For every spool window (8 MiB of interleaved uint8 I/Q at 2.048 MS/s) it finds narrow spectral lines, converts them to absolute
frequency (tuned centre + offset), and aggregates how often each 1 kHz frequency bin shows a line in the visits that can observe it.
A visit can observe a bin only where the detector is allowed to report (outside the DC guard, the canary lines and the pass-band
edges): that one eligibility mask is used both to find lines and to count the visits that cover a bin, so a bin the detector
cannot see is never counted as a non-detection, and a bin no visit can see is reported as UNOBSERVABLE.
A line is "RF-fixed" only if the visits that saw it had centres at least 50 kHz apart (the schedule retunes the same tuning by
-100/+200/-50 kHz for this reason); a line that stays at the same offset from the centre is an instrument spur, not RF. The same
rule (RF-fixed, persistence >= 0.6, >= 3 covering visits) decides whether a run "detects" a frequency in the cross-run step.
Output is frequency, persistence and SNR: no audio and no content.

Integrity: every window must be exactly 8,388,608 bytes and match the sha256 in the dataset's captures.parquet; remote reads use a
pinned dataset revision and an isolated temporary cache (the shared Hugging Face cache is never touched); a run is processed
only if all its visits read and verify, and every result file carries its provenance.

usage: persistence_pass.py detect OUT_DIR --revision SHA [--captures PATH] [--sets a,b] [--local-root ~/s521_runs] [--workers 3]
       persistence_pass.py aggregate OUT_DIR [--sets a,b]
       persistence_pass.py classify OUT_DIR --results PATH [--sets a,b]
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor

import numpy as np

REPO_ID = "bgilbert1984/scythe-uhf-rtl-sdr-evidence"
ANALYSIS_SCHEMA_VERSION = 1
EXPECTED_BYTES = 8_388_608       # one window: 4,194,304 complex samples
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
MIN_COVER = 3                    # covering visits needed for a bin to count as observed in a run
MIN_PERSIST = 0.6                # detecting / covering visits for a persistent line
QUIET_PERSIST = 0.2              # below this a covered bin counts as quiet
MIN_SPREAD_HZ = 50_000.0         # centre spread needed for a line to be RF-fixed
REF_HZ = 28_800_000.0            # the receiver's reference clock; its harmonics are candidates for instrument lines
REF_TOL_HZ = 2_000.0
GROUP_HZ = 3_000.0               # lines within this are one frequency across runs
F_LO, F_HI = 398e6, 472e6        # aggregation range
LOCAL_DIRS = {
    "run_20261122_harbourpointe_r2": "antenna_on_harbourpointe_20261122_r2",
    "run_20261124_hwy99_ofinnigans": "antenna_on_ofinnigans_20261124",
    "run_20261126_hwy99": "antenna_on_hwy99_20261126",
    "run_20261126_sea6thave_r2": "antenna_on_6thave_20261126_r2",
    "run_20261127_hwy99": "antenna_on_hwy99_20261127",
}
CONFIG = {"nfft": NFFT, "edge_hz": EDGE_HZ, "dc_guard_hz": DC_GUARD_HZ, "canary_hz": CANARY_HZ, "canary_guard_hz": CANARY_GUARD_HZ,
          "floor_half_bins": FLOOR_HALF, "detect_db": DETECT_DB, "max_width_bins": MAX_WIDTH_BINS, "clip_max_pct": CLIP_MAX_PCT,
          "min_cover": MIN_COVER, "min_persist": MIN_PERSIST, "quiet_persist": QUIET_PERSIST, "min_spread_hz": MIN_SPREAD_HZ,
          "ref_hz": REF_HZ, "ref_tol_hz": REF_TOL_HZ, "group_hz": GROUP_HZ, "expected_bytes": EXPECTED_BYTES}


class IntegrityError(Exception):
    pass


def config_sha256():
    return hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()


def code_sha256():
    with open(os.path.abspath(__file__), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def git_state():
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True, timeout=20, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", here], cwd=here, capture_output=True, text=True, timeout=20, check=True).stdout.strip())
        return {"git_head": head, "git_dirty": dirty}
    except Exception:
        return {"git_head": None, "git_dirty": None}


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


def eligible_offsets(off):
    """The one mask of centre offsets where a line may be reported: used by detection and by coverage counting alike."""
    a = np.abs(np.asarray(off, dtype=float))
    return (a < EDGE_HZ) & (a > DC_GUARD_HZ) & (np.abs(a - CANARY_HZ) > CANARY_GUARD_HZ)


def detect_lines(raw):
    """Return [(offset_hz, snr_db)] of narrow lines in one window."""
    db = psd_db(raw)
    floor = running_median(db, FLOOR_HALF)
    snr = db - floor
    off = (np.arange(NFFT) - NFFT // 2) * BIN_HZ
    ok = eligible_offsets(off)
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


def validate_window(raw, expected_sha):
    if len(raw) != EXPECTED_BYTES:
        raise IntegrityError(f"window is {len(raw)} bytes, expected {EXPECTED_BYTES}")
    if expected_sha is not None and hashlib.sha256(raw).hexdigest() != expected_sha:
        raise IntegrityError("window sha256 differs from the dataset metadata")


def read_window(set_id, rel, local_root, revision):
    sub = rel.split(f"data/runs/{set_id}/", 1)[1]
    if set_id in LOCAL_DIRS:
        with open(os.path.join(local_root, LOCAL_DIRS[set_id], sub), "rb") as fh:
            return fh.read()
    from huggingface_hub import hf_hub_download
    with tempfile.TemporaryDirectory(prefix="s521_persist_") as tmp:          # isolated cache: nothing shared is modified
        p = hf_hub_download(REPO_ID, rel, repo_type="dataset", revision=revision, cache_dir=tmp)
        with open(p, "rb") as fh:
            return fh.read()


def one_visit(job):
    set_id, rel, centre, delta, pos, local_root, expected_sha, revision = job
    base = {"set_id": set_id, "position": pos, "centre_hz": centre, "retune_delta_hz": delta}
    try:
        raw = read_window(set_id, rel, local_root, revision)
        validate_window(raw, expected_sha)
        return {**base, "lines": detect_lines(raw), "error": None, "sha256_verified": expected_sha is not None}
    except Exception as exc:  # recorded and counted against completeness, never hidden
        return {**base, "lines": [], "error": repr(exc)[:200], "sha256_verified": False}


def manifest_sha256(rows):
    """sha256 over the sorted 'file  sha256' pairs of a run's visits: pins exactly which captures were analysed."""
    text = "".join(f"{f}  {s}\n" for f, s in sorted(rows))
    return hashlib.sha256(text.encode()).hexdigest()


def load_captures(path, revision):
    import pandas as pd
    if path:
        return pd.read_parquet(path)
    from huggingface_hub import hf_hub_download
    with tempfile.TemporaryDirectory(prefix="s521_persist_") as tmp:
        return pd.read_parquet(hf_hub_download(REPO_ID, "metadata/captures.parquet", repo_type="dataset", revision=revision, cache_dir=tmp))


def provenance_for(revision, manifest, expected, processed, verified):
    return {"analysis_schema_version": ANALYSIS_SCHEMA_VERSION, "source_dataset_revision": revision, "capture_manifest_sha256": manifest,
            "analysis_config_sha256": config_sha256(), "software": {"code_sha256": code_sha256(), **git_state()},
            "expected_visits": expected, "processed_visits": processed, "integrity_verified": verified}


def provenance_matches(prov, revision, manifest):
    return (isinstance(prov, dict) and prov.get("analysis_schema_version") == ANALYSIS_SCHEMA_VERSION and prov.get("source_dataset_revision") == revision
            and prov.get("capture_manifest_sha256") == manifest and prov.get("analysis_config_sha256") == config_sha256()
            and (prov.get("software") or {}).get("code_sha256") == code_sha256()
            and prov.get("integrity_verified") is True and prov.get("processed_visits") == prov.get("expected_visits"))


def cmd_detect(a):
    c = load_captures(a.captures, a.revision)
    c = c[c.kind == "observation_run_visit"].sort_values(["set_id", "visit_position"])
    wanted = set(a.sets.split(",")) if a.sets else None
    os.makedirs(a.out, exist_ok=True)
    failed = []
    for set_id, grp in c.groupby("set_id"):
        if wanted and set_id not in wanted:
            continue
        manifest = manifest_sha256(zip(grp.file, grp.sha256))
        dest = os.path.join(a.out, f"{set_id}.json")
        if os.path.exists(dest):
            try:
                with open(dest) as fh:
                    existing = json.load(fh)
                if provenance_matches(existing.get("provenance"), a.revision, manifest):
                    print("skip (provenance matches)", set_id, flush=True)
                    continue
            except (OSError, ValueError):
                pass
            print("recompute (existing file has no matching provenance)", set_id, flush=True)
        jobs = [(set_id, r.file, float(r.centre_hz), float(r.retune_delta_hz), int(r.visit_position), a.local_root, str(r.sha256), a.revision)
                for r in grp.itertuples()]
        clipped = {int(r.visit_position): float(r.clipping_pct) for r in grp.itertuples()}
        site = str(grp.site_key.iloc[0])
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            res = list(ex.map(one_visit, jobs, chunksize=4))
        for r in res:
            r["clipping_pct"] = clipped[r["position"]]
        errs = [r for r in res if r["error"]]
        ok = len(res) - len(errs)
        prov = provenance_for(a.revision, manifest, len(res), ok, not errs and all(r["sha256_verified"] for r in res))
        with open(dest, "w") as fh:
            json.dump({"set_id": set_id, "site_key": site, "provenance": prov, "visits": res}, fh)
        print(f"{set_id}: {ok}/{len(res)} visits verified and processed, {sum(len(r['lines']) for r in res)} line detections", flush=True)
        if errs:
            failed.append(set_id)
            print("  first error:", errs[0]["error"], flush=True)
    if failed:
        print("INCOMPLETE:", failed, flush=True)
        return 2
    return 0


def aggregate_run(doc, f_lo=F_LO, f_hi=F_HI):
    """Per-run table: for each 1 kHz bin, the visits that can observe it, those that detect a line there, SNR and centre offsets."""
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
        lo = max(int((c - EDGE_HZ - f_lo) / 1000), 0)
        hi = min(int((c + EDGE_HZ - f_lo) / 1000) + 1, nb)
        if lo >= hi:
            continue
        ks = np.arange(lo, hi)
        cover[ks[eligible_offsets(f_lo + ks * 1000.0 - c)]] += 1          # the same mask the detector used
        seen = set()
        for off, snr in v["lines"]:
            k = int((c + off - f_lo) / 1000 + 0.5)
            if 0 <= k < nb and k not in seen and bool(eligible_offsets(f_lo + k * 1000.0 - c)):
                seen.add(k)
                det[k] += 1
                snr_sum[k] += snr
                offs[k].append(off)
    return {"cover": cover, "det": det, "snr_sum": snr_sum, "offs": offs, "visits_used": used, "f_lo": f_lo}


def bin_status(agg, k):
    cover, det = int(agg["cover"][k]), int(agg["det"][k])
    offsets = agg["offs"][k]
    spread = (max(offsets) - min(offsets)) if offsets else 0.0
    return {"covering_visits": cover, "detecting_visits": det, "persistence": round(det / cover, 3) if cover else 0.0,
            "centre_offset_spread_khz": round(spread / 1000.0, 1), "rf_fixed": bool(offsets and spread >= MIN_SPREAD_HZ),
            "mean_snr_db": round(float(agg["snr_sum"][k] / det), 1) if det else None}


def lines_of(agg, min_cover=MIN_COVER, min_persist=MIN_PERSIST, require_rf_fixed=False):
    """Persistent lines of one run (optionally only the RF-fixed ones), each reduced to its strongest bin."""
    out = []
    cover, det = agg["cover"], agg["det"]
    nb = len(cover)
    k = 0
    while k < nb:
        if cover[k] >= min_cover and det[k] > 0 and det[k] / cover[k] >= min_persist:
            j = k
            while j + 1 < nb and det[j + 1] > 0 and cover[j + 1] >= min_cover and det[j + 1] / cover[j + 1] >= min_persist:
                j += 1
            best = max(range(k, j + 1), key=lambda q: det[q])
            st = bin_status(agg, best)
            if st["rf_fixed"] or not require_rf_fixed:
                out.append({"freq_hz": agg["f_lo"] + best * 1000.0, **st})
            k = j + 1
        else:
            k += 1
    return out


def presence(aggs, f_hz, tol_bins=2):
    """Per run, the state of one absolute frequency, by the same rule as the single-run step.
    DETECTING: observed, persistent and RF-fixed. QUIET: observed and under 20% persistence. INTERMEDIATE: observed, neither.
    UNOBSERVABLE: fewer than 3 visits can observe it (not a non-detection)."""
    out = {}
    for sid, ag in aggs.items():
        k0 = int((f_hz - ag["f_lo"]) / 1000 + 0.5)
        ks = [k for k in range(max(k0 - tol_bins, 0), min(k0 + tol_bins + 1, len(ag["cover"]))) if ag["cover"][k] >= MIN_COVER]
        if not ks:
            best = max(range(max(k0 - tol_bins, 0), min(k0 + tol_bins + 1, len(ag["cover"]))), key=lambda q: ag["cover"][q], default=None)
            st = bin_status(ag, best) if best is not None else {"covering_visits": 0, "detecting_visits": 0, "persistence": 0.0, "centre_offset_spread_khz": 0.0, "rf_fixed": False, "mean_snr_db": None}
            out[sid] = {**st, "state": "UNOBSERVABLE"}
            continue
        best = max(ks, key=lambda q: (ag["det"][q], ag["cover"][q]))
        st = bin_status(ag, best)
        if st["persistence"] >= MIN_PERSIST and st["rf_fixed"]:
            state = "DETECTING"
        elif st["persistence"] < QUIET_PERSIST:
            state = "QUIET"
        else:
            state = "INTERMEDIATE"
        out[sid] = {**st, "state": state}
    return out


def reference_harmonic(f_hz):
    h = round(f_hz / REF_HZ)
    return {"n": int(h), "offset_hz": round(f_hz - h * REF_HZ, 1)} if h >= 1 and abs(f_hz - h * REF_HZ) <= REF_TOL_HZ else None


def classify_frequency(f, pres, site_of):
    detecting = [s for s, p in pres.items() if p["state"] == "DETECTING"]
    covering = [s for s, p in pres.items() if p["state"] != "UNOBSERVABLE"]
    det_sites = sorted({site_of[s] for s in detecting})
    cov_sites = sorted({site_of[s] for s in covering})
    all_detect = bool(covering) and len(detecting) == len(covering)
    if reference_harmonic(f):
        # Frequency alignment alone is only a candidate. "Consistent with internal mixing" needs the line RF-fixed and present
        # wherever it is observable across at least three venues. Origin is NEVER confirmed here: that needs a terminated-input
        # or other independent test, and EXTERNAL_RF_CONFIRMED needs independent evidence of external origin.
        return "CONSISTENT_WITH_INTERNAL_MIXING" if (all_detect and len(detecting) >= 3 and len(det_sites) >= 3) else "REFERENCE_HARMONIC_CANDIDATE"
    if len(detecting) >= 2 and len(det_sites) >= 2:
        return "PRESENT_AT_ALL_OBSERVED_SITES" if (all_detect and len(cov_sites) >= 3) else "MULTI_SITE_PARTIAL"
    if len(detecting) >= 2:
        others = [s for s in covering if site_of[s] != det_sites[0]]
        quiet = [s for s in others if pres[s]["state"] == "QUIET"]
        return "SITE_LOCAL_PERSISTENT" if others and len(quiet) == len(others) else "MULTI_RUN_SINGLE_SITE_OTHERS_UNCLEAR"
    return "SINGLE_RUN" if detecting else "NOT_PERSISTENT"


def classify_docs(docs):
    """docs: {set_id: detection document}. Returns (classified frequencies, per-run summaries)."""
    site_of = {sid: d["site_key"] for sid, d in docs.items()}
    aggs = {sid: aggregate_run(d) for sid, d in docs.items()}
    seeds = sorted((ln["freq_hz"], sid, ln) for sid, ag in aggs.items() for ln in lines_of(ag, require_rf_fixed=True))
    groups = []
    for item in seeds:
        if groups and item[0] - groups[-1][-1][0] <= GROUP_HZ:
            groups[-1].append(item)
        else:
            groups.append([item])
    out = []
    for g in groups:
        f = float(np.mean([x[0] for x in g]))
        pres = presence(aggs, f)
        entry = {"freq_hz": round(f, 1), "class": classify_frequency(f, pres, site_of),
                 "runs_detecting": sum(p["state"] == "DETECTING" for p in pres.values()),
                 "runs_observed": sum(p["state"] != "UNOBSERVABLE" for p in pres.values()),
                 "sites_detecting": sorted({site_of[s] for s, p in pres.items() if p["state"] == "DETECTING"}),
                 "sites_observed": sorted({site_of[s] for s, p in pres.items() if p["state"] != "UNOBSERVABLE"}),
                 "max_snr_db": max(x[2]["mean_snr_db"] for x in g), "reference_harmonic": reference_harmonic(f),
                 "per_run": {s: {k: p[k] for k in ("state", "covering_visits", "detecting_visits", "persistence", "centre_offset_spread_khz", "rf_fixed")}
                             for s, p in pres.items()}}
        out.append(entry)
    nb_band = int((470e6 - 400e6) / 1000)
    k0 = int((400e6 - F_LO) / 1000)
    runs = {}
    for sid, ag in aggs.items():
        observed = ag["cover"][k0:k0 + nb_band] >= MIN_COVER
        runs[sid] = {"site_key": site_of[sid], "visits_used": ag["visits_used"], "visits_excluded": len(docs[sid]["visits"]) - ag["visits_used"],
                     "observable_fraction_400_470_mhz": round(float(observed.mean()), 4),
                     "persistent_lines": len(lines_of(ag)), "rf_fixed_persistent_lines": len(lines_of(ag, require_rf_fixed=True))}
    return out, runs


def load_docs(out_dir, wanted=None):
    """Load detection documents and refuse anything incomplete or without matching provenance (the completeness gate)."""
    docs, revisions, configs, codes = {}, set(), set(), set()
    expected = processed = 0
    manifests = {}
    for name in sorted(os.listdir(out_dir)):
        if not name.endswith(".json") or name.startswith(("summary", "results")):
            continue
        with open(os.path.join(out_dir, name)) as fh:
            d = json.load(fh)
        if wanted and d["set_id"] not in wanted:
            continue
        pv = d.get("provenance")
        if not isinstance(pv, dict) or pv.get("analysis_schema_version") != ANALYSIS_SCHEMA_VERSION:
            raise IntegrityError(f"{name}: no provenance block")
        if pv.get("integrity_verified") is not True or pv.get("processed_visits") != pv.get("expected_visits"):
            raise IntegrityError(f"{name}: incomplete ({pv.get('processed_visits')}/{pv.get('expected_visits')} visits, integrity_verified={pv.get('integrity_verified')})")
        if pv.get("analysis_config_sha256") != config_sha256() or (pv.get("software") or {}).get("code_sha256") != code_sha256():
            raise IntegrityError(f"{name}: produced by a different analysis configuration or code than this script")
        docs[d["set_id"]] = d
        revisions.add(pv["source_dataset_revision"]); configs.add(pv["analysis_config_sha256"]); codes.add(pv["software"]["code_sha256"])
        manifests[d["set_id"]] = pv["capture_manifest_sha256"]
        expected += pv["expected_visits"]; processed += pv["processed_visits"]
    if wanted and set(docs) != set(wanted):
        raise IntegrityError(f"missing detection files for {sorted(set(wanted) - set(docs))}")
    if not docs:
        raise IntegrityError("no detection files")
    if len(revisions) != 1:
        raise IntegrityError(f"detection files come from different dataset revisions: {sorted(revisions)}")
    return docs, {"revision": revisions.pop(), "manifests": manifests, "expected": expected, "processed": processed}


def cmd_aggregate(a):
    docs, _ = load_docs(a.out, set(a.sets.split(",")) if a.sets else None)
    _, runs = classify_docs(docs)
    for sid, r in runs.items():
        print(f"{sid} ({r['site_key']}): visits used {r['visits_used']} (excluded {r['visits_excluded']}), observable {r['observable_fraction_400_470_mhz']:.1%} of 400-470 MHz, "
              f"persistent lines {r['persistent_lines']}, RF-fixed {r['rf_fixed_persistent_lines']}")


def cmd_classify(a):
    docs, meta = load_docs(a.out, set(a.sets.split(",")) if a.sets else None)
    classified, runs = classify_docs(docs)
    from collections import Counter
    counts = Counter(x["class"] for x in classified)
    manifest = hashlib.sha256(json.dumps(meta["manifests"], sort_keys=True).encode()).hexdigest()
    result = {"provenance": provenance_for(meta["revision"], manifest, meta["expected"], meta["processed"], True),
              "config": CONFIG, "runs": runs, "class_counts": dict(counts), "distinct_rf_fixed_frequencies": len(classified), "classified": classified}
    with open(a.results, "w") as fh:
        json.dump(result, fh, indent=1)
    print(dict(counts))
    for x in classified:
        if x["class"] != "SINGLE_RUN":
            print(f"{x['freq_hz']/1e6:10.4f} MHz  {x['class']:34s} runs {x['runs_detecting']}/{x['runs_observed']}  sites {','.join(x['sites_detecting'])}  snr {x['max_snr_db']}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect")
    d.add_argument("out")
    d.add_argument("--revision", required=True, help="pinned dataset revision (commit sha)")
    d.add_argument("--captures", help="local copy of metadata/captures.parquet at that revision (default: download it)")
    d.add_argument("--sets")
    d.add_argument("--local-root", default=os.path.expanduser("~/s521_runs"))
    d.add_argument("--workers", type=int, default=3)
    g = sub.add_parser("aggregate")
    g.add_argument("out")
    g.add_argument("--sets")
    k = sub.add_parser("classify")
    k.add_argument("out")
    k.add_argument("--results", required=True)
    k.add_argument("--sets")
    a = ap.parse_args()
    try:
        return {"detect": cmd_detect, "aggregate": cmd_aggregate, "classify": cmd_classify}[a.cmd](a) or 0
    except IntegrityError as exc:
        print("REFUSED:", exc, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
