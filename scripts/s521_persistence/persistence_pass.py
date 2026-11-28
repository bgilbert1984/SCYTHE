"""Narrowband-line persistence pass over the §5.21 antenna-on observation runs (observation only, no demodulation).

For every spool window (8 MiB of interleaved uint8 I/Q at 2.048 MS/s) it finds narrow spectral lines, converts them to absolute
frequency (tuned centre + offset), and aggregates how often each 1 kHz frequency bin shows a line in the visits that can observe it.
A visit can observe a bin only where the detector is allowed to report (outside the DC guard, the canary lines and the pass-band
edges): that one eligibility mask is used both to find lines and to count the visits that cover a bin, so a bin the detector
cannot see is never counted as a non-detection, and a bin no visit can see is reported as UNOBSERVABLE.
A line is "RF-fixed" only if the visits that saw it had centres at least 50 kHz apart (the schedule retunes the same tuning by
-100/+200/-50 kHz for this reason); a line that stays at the same offset from the centre is an instrument spur, not RF. The same
rule (RF-fixed, persistence >= 0.6, >= 3 covering visits) decides whether a run "detects" a frequency in the cross-run step.
Lines from different runs are associated into one frequency only within a bounded cluster (a maximum diameter, not a chain of
neighbours); every member frequency and run is kept, and an association that is not clean is marked unresolved. Frequency
proximity is a potential association, not proof of signal identity.
Output is frequency, persistence and SNR: no audio and no content.

Integrity: the dataset revision must be a full commit sha; captures.parquet is fetched at that revision and any local copy must
match it byte for byte; every window must be exactly 8,388,608 bytes and match the sha256 in the manifest; reads use an isolated
temporary cache (the shared Hugging Face cache is never touched). A detection document is accepted only if its visit records
themselves reconcile with the authoritative manifest (one verified record per manifest visit, same positions, same inputs), the
expected run inventory is present with no duplicates, and the document's own hash matches. Provenance is checked against the
records, never trusted on its own.

usage: persistence_pass.py detect OUT_DIR --revision SHA [--captures PATH] [--sets a,b] [--local-root ~/s521_runs | --remote-only] [--workers 3]
       persistence_pass.py aggregate OUT_DIR [--captures PATH] [--sets a,b]
       persistence_pass.py classify OUT_DIR --results PATH [--captures PATH] [--sets a,b]
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor

import numpy as np

REPO_ID = "bgilbert1984/scythe-uhf-rtl-sdr-evidence"
ANALYSIS_SCHEMA_VERSION = 2
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
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
GROUP_MAX_DIAMETER_HZ = 3_000.0  # a cluster of lines from different runs spans at most this much (not a chain of neighbours)
PRESENCE_TOL_BINS = 2            # 1 kHz bins either side of a cluster searched for a run's qualified line
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
          "ref_hz": REF_HZ, "ref_tol_hz": REF_TOL_HZ, "group_max_diameter_hz": GROUP_MAX_DIAMETER_HZ, "presence_tol_bins": PRESENCE_TOL_BINS,
          "expected_bytes": EXPECTED_BYTES}


class IntegrityError(Exception):
    pass


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha256_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def config_sha256():
    return sha256_text(json.dumps(CONFIG, sort_keys=True))


def code_sha256():
    return file_sha256(os.path.abspath(__file__))


def check_revision(revision):
    if not isinstance(revision, str) or not REVISION_RE.match(revision):
        raise IntegrityError("the dataset revision must be a full 40-character commit sha (a branch or tag name can move)")


def git_state():
    env = os.environ.get("S521_SOFTWARE_COMMIT")          # set when the script runs from a copy outside a git checkout
    if env:
        return {"git_head": env, "git_dirty": None}
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
    if local_root and set_id in LOCAL_DIRS:
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


# ---- the authoritative manifest and the evidence it binds ----------------------------------------------------------------

def manifest_rows(df):
    """{set_id: [row, ...]} for the observation runs, one row per visit sorted by position, with every input that affects analysis."""
    out = {}
    for r in df[df.kind == "observation_run_visit"].itertuples():
        out.setdefault(r.set_id, []).append({
            "set_id": str(r.set_id), "site_key": str(r.site_key), "position": int(r.visit_position), "file": str(r.file), "sha256": str(r.sha256),
            "centre_hz": float(r.centre_hz), "retune_delta_hz": float(r.retune_delta_hz), "clipping_pct": float(r.clipping_pct)})
    for rows in out.values():
        rows.sort(key=lambda x: x["position"])
    return out


def manifest_sha256(rows):
    """sha256 over the canonical rows of one run: the captures AND the inputs (centre, retune, position, clipping, site) analysis depends on."""
    return sha256_text(canonical(rows))


def load_captures(path, revision):
    """(DataFrame, sha256 of the parquet) for the pinned revision. A local copy is accepted only if it is byte-identical."""
    check_revision(revision)
    import pandas as pd
    from huggingface_hub import hf_hub_download
    with tempfile.TemporaryDirectory(prefix="s521_persist_") as tmp:
        remote = hf_hub_download(REPO_ID, "metadata/captures.parquet", repo_type="dataset", revision=revision, cache_dir=tmp)
        remote_sha = file_sha256(remote)
        if path:
            if file_sha256(path) != remote_sha:
                raise IntegrityError("the local captures.parquet is not the file at the pinned revision")
            return pd.read_parquet(path), remote_sha
        return pd.read_parquet(remote), remote_sha


def document_sha256(doc):
    """Hash of a detection document, excluding the hash field itself."""
    d = json.loads(json.dumps(doc))
    d["provenance"].pop("detection_document_sha256", None)
    return sha256_text(canonical(d))


def provenance_for(revision, captures_sha, manifest, expected, processed, verified, doc_sha=None):
    return {"analysis_schema_version": ANALYSIS_SCHEMA_VERSION, "source_dataset_revision": revision, "captures_parquet_sha256": captures_sha,
            "capture_manifest_sha256": manifest, "detection_document_sha256": doc_sha, "analysis_config_sha256": config_sha256(),
            "analysis_code_sha256": code_sha256(), "software": git_state(), "expected_visits": expected, "processed_visits": processed,
            "integrity_verified": verified}


VISIT_FIELDS = ("file", "sha256", "centre_hz", "retune_delta_hz", "clipping_pct")


def validate_detection_doc(doc, rows, revision, captures_sha):
    """Accept a detection document only if its VISIT RECORDS reconcile with the authoritative manifest; the provenance block is
    checked against them and is never trusted on its own. Raises IntegrityError naming the first discrepancy."""
    if not isinstance(doc, dict):
        raise IntegrityError("detection document is not an object")
    set_id = doc.get("set_id")
    pv = doc.get("provenance")
    if not isinstance(pv, dict) or pv.get("analysis_schema_version") != ANALYSIS_SCHEMA_VERSION:
        raise IntegrityError(f"{set_id}: no provenance block of schema {ANALYSIS_SCHEMA_VERSION}")
    if not rows:
        raise IntegrityError(f"{set_id}: not in the capture manifest")
    if doc.get("site_key") != rows[0]["site_key"] or any(r["site_key"] != rows[0]["site_key"] for r in rows):
        raise IntegrityError(f"{set_id}: site assignment differs from the manifest")
    for key, want in (("source_dataset_revision", revision), ("captures_parquet_sha256", captures_sha), ("analysis_config_sha256", config_sha256()),
                      ("analysis_code_sha256", code_sha256()), ("capture_manifest_sha256", manifest_sha256(rows))):
        if pv.get(key) != want:
            raise IntegrityError(f"{set_id}: provenance {key} does not match")
    visits = doc.get("visits")
    if not isinstance(visits, list) or len(visits) != len(rows):
        raise IntegrityError(f"{set_id}: {0 if not isinstance(visits, list) else len(visits)} visit records for {len(rows)} manifest visits")
    positions = [v.get("position") for v in visits]
    if len(set(positions)) != len(positions) or sorted(positions) != [r["position"] for r in rows]:
        raise IntegrityError(f"{set_id}: visit positions are duplicated or differ from the manifest")
    by_pos = {r["position"]: r for r in rows}
    for v in visits:
        row = by_pos[v["position"]]
        if v.get("error") is not None or v.get("sha256_verified") is not True:
            raise IntegrityError(f"{set_id}: visit {v['position']} is not a verified record")
        for f in VISIT_FIELDS:
            if v.get(f) != row[f]:
                raise IntegrityError(f"{set_id}: visit {v['position']} {f} differs from the manifest")
    if pv.get("expected_visits") != len(rows) or pv.get("processed_visits") != len(rows) or pv.get("integrity_verified") is not True:
        raise IntegrityError(f"{set_id}: provenance counts or integrity flag do not match the records")
    if pv.get("detection_document_sha256") != document_sha256(doc):
        raise IntegrityError(f"{set_id}: the document does not match its recorded hash")


def cmd_detect(a):
    check_revision(a.revision)
    df, captures_sha = load_captures(a.captures, a.revision)
    rows_by_set = manifest_rows(df)
    wanted = set(a.sets.split(",")) if a.sets else None
    if wanted and wanted - set(rows_by_set):
        raise IntegrityError(f"not in the manifest: {sorted(wanted - set(rows_by_set))}")
    os.makedirs(a.out, exist_ok=True)
    failed = []
    for set_id in sorted(rows_by_set):
        if wanted and set_id not in wanted:
            continue
        rows = rows_by_set[set_id]
        dest = os.path.join(a.out, f"{set_id}.json")
        if os.path.exists(dest):
            try:
                with open(dest) as fh:
                    validate_detection_doc(json.load(fh), rows, a.revision, captures_sha)
                print("skip (document validates against the manifest)", set_id, flush=True)
                continue
            except (IntegrityError, OSError, ValueError, KeyError, TypeError) as exc:
                print(f"recompute ({set_id}: existing file rejected: {exc})", flush=True)
        local_root = None if a.remote_only else a.local_root
        jobs = [(set_id, r["file"], r["centre_hz"], r["retune_delta_hz"], r["position"], local_root, r["sha256"], a.revision) for r in rows]
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            res = {r["position"]: r for r in ex.map(one_visit, jobs, chunksize=4)}
        visits = []
        for row in rows:
            r = res[row["position"]]
            visits.append({"position": row["position"], "file": row["file"], "sha256": row["sha256"], "centre_hz": row["centre_hz"],
                           "retune_delta_hz": row["retune_delta_hz"], "clipping_pct": row["clipping_pct"], "lines": r["lines"],
                           "error": r["error"], "sha256_verified": r["sha256_verified"]})
        errs = [v for v in visits if v["error"]]
        ok = len(visits) - len(errs)
        prov = provenance_for(a.revision, captures_sha, manifest_sha256(rows), len(rows), ok, not errs and all(v["sha256_verified"] for v in visits))
        doc = {"set_id": set_id, "site_key": rows[0]["site_key"], "provenance": prov, "visits": visits}
        prov["detection_document_sha256"] = document_sha256(doc)
        with open(dest, "w") as fh:
            json.dump(doc, fh)
        print(f"{set_id}: {ok}/{len(visits)} visits verified and processed, {sum(len(v['lines']) for v in visits)} line detections", flush=True)
        if errs:
            failed.append(set_id)
            print("  first error:", errs[0]["error"], flush=True)
    if failed:
        print("INCOMPLETE:", failed, flush=True)
        return 2
    return 0


# ---- aggregation and classification --------------------------------------------------------------------------------------

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


def is_qualified(st):
    """Observed, persistent and RF-fixed: the single rule for a run detecting a frequency."""
    return st["covering_visits"] >= MIN_COVER and st["persistence"] >= MIN_PERSIST and st["rf_fixed"]


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


def presence(aggs, f_min, f_max=None, tol_bins=PRESENCE_TOL_BINS):
    """Per run, the state of a frequency span [f_min, f_max] by the same rule as the single-run step.
    Qualified bins (observed, persistent, RF-fixed) are ranked before any other: nearby unqualified energy never overrides a
    qualified detection. DETECTING: a qualified bin exists. QUIET: observed and under 20% persistence. INTERMEDIATE: observed, neither.
    UNOBSERVABLE: fewer than 3 visits can observe it (not a non-detection)."""
    f_max = f_min if f_max is None else f_max
    out = {}
    for sid, ag in aggs.items():
        nb = len(ag["cover"])
        k_lo = max(int((f_min - ag["f_lo"]) / 1000 + 0.5) - tol_bins, 0)
        k_hi = min(int((f_max - ag["f_lo"]) / 1000 + 0.5) + tol_bins + 1, nb)
        window = range(k_lo, k_hi)
        observed = [k for k in window if ag["cover"][k] >= MIN_COVER]
        if not observed:
            best = max(window, key=lambda q: ag["cover"][q], default=None)
            st = bin_status(ag, best) if best is not None else {"covering_visits": 0, "detecting_visits": 0, "persistence": 0.0,
                                                                  "centre_offset_spread_khz": 0.0, "rf_fixed": False, "mean_snr_db": None}
            out[sid] = {**st, "state": "UNOBSERVABLE"}
            continue
        qualified = [k for k in observed if is_qualified(bin_status(ag, k))]
        pool = qualified or observed
        best = max(pool, key=lambda q: (ag["det"][q], ag["cover"][q]))
        st = bin_status(ag, best)
        if qualified:
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


def cluster_seeds(seeds):
    """Associate lines from different runs into clusters of bounded DIAMETER (not a chain of neighbours). Every seed lands in exactly
    one cluster, so no qualified observation is dropped. seeds: [(freq_hz, run, line)]."""
    clusters = []
    for item in sorted(seeds, key=lambda x: (x[0], x[1])):
        if clusters and item[0] - clusters[-1][0][0] <= GROUP_MAX_DIAMETER_HZ:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    return clusters


def classify_docs(docs):
    """docs: {set_id: detection document}. Returns (classified frequencies, per-run summaries)."""
    site_of = {sid: d["site_key"] for sid, d in docs.items()}
    aggs = {sid: aggregate_run(d) for sid, d in docs.items()}
    seeds = [(ln["freq_hz"], sid, ln) for sid, ag in aggs.items() for ln in lines_of(ag, require_rf_fixed=True)]
    clusters = cluster_seeds(seeds)
    out = []
    for i, g in enumerate(clusters):
        f = float(np.mean([x[0] for x in g]))
        f_min, f_max = min(x[0] for x in g), max(x[0] for x in g)
        pres = presence(aggs, f_min, f_max)
        members = [{"run": sid, "freq_hz": freq, "persistence": ln["persistence"], "covering_visits": ln["covering_visits"],
                    "detecting_visits": ln["detecting_visits"], "centre_offset_spread_khz": ln["centre_offset_spread_khz"], "mean_snr_db": ln["mean_snr_db"]}
                   for freq, sid, ln in g]
        runs_in = [m["run"] for m in members]
        same_run_twice = len(set(runs_in)) != len(runs_in)
        near_prev = i > 0 and f_min - max(x[0] for x in clusters[i - 1]) <= GROUP_MAX_DIAMETER_HZ
        near_next = i + 1 < len(clusters) and min(x[0] for x in clusters[i + 1]) - f_max <= GROUP_MAX_DIAMETER_HZ
        entry = {"freq_hz": round(f, 1), "freq_min_hz": f_min, "freq_max_hz": f_max, "class": classify_frequency(f, pres, site_of),
                 "unresolved_association": bool(same_run_twice or near_prev or near_next),
                 "runs_detecting": sum(p["state"] == "DETECTING" for p in pres.values()),
                 "runs_observed": sum(p["state"] != "UNOBSERVABLE" for p in pres.values()),
                 "sites_detecting": sorted({site_of[s] for s, p in pres.items() if p["state"] == "DETECTING"}),
                 "sites_observed": sorted({site_of[s] for s, p in pres.items() if p["state"] != "UNOBSERVABLE"}),
                 "max_snr_db": max(x[2]["mean_snr_db"] for x in g), "reference_harmonic": reference_harmonic(f), "members": members,
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


def revision_of_documents(out_dir, wanted=None):
    """The one dataset revision all detection documents claim; refuses mixed or non-commit revisions."""
    revs = set()
    for name in sorted(os.listdir(out_dir)):
        if name.endswith(".json") and not name.startswith(("summary", "results")):
            with open(os.path.join(out_dir, name)) as fh:
                d = json.load(fh)
            if wanted and d.get("set_id") not in wanted:
                continue
            revs.add((d.get("provenance") or {}).get("source_dataset_revision"))
    if len(revs) != 1:
        raise IntegrityError(f"detection documents must come from exactly one dataset revision, found {sorted(map(str, revs))}")
    rev = revs.pop()
    check_revision(rev)
    return rev


def load_docs(out_dir, rows_by_set, revision, captures_sha, wanted=None):
    """Load detection documents under the completeness gate: the expected run inventory is present with no duplicates, and every
    document reconciles with the authoritative manifest at the record level."""
    expected_sets = set(wanted) if wanted else set(rows_by_set)
    if wanted and wanted - set(rows_by_set):
        raise IntegrityError(f"not in the manifest: {sorted(wanted - set(rows_by_set))}")
    docs = {}
    for name in sorted(os.listdir(out_dir)):
        if not name.endswith(".json") or name.startswith(("summary", "results")):
            continue
        with open(os.path.join(out_dir, name)) as fh:
            d = json.load(fh)
        set_id = d.get("set_id")
        if name != f"{set_id}.json":
            raise IntegrityError(f"{name}: the file name does not match its set_id {set_id!r}")
        if set_id in docs:
            raise IntegrityError(f"duplicate run identifier {set_id}")
        if wanted and set_id not in wanted:
            continue
        validate_detection_doc(d, rows_by_set.get(set_id), revision, captures_sha)
        docs[set_id] = d
    if set(docs) != expected_sets:
        raise IntegrityError(f"run inventory differs from the expected one: missing {sorted(expected_sets - set(docs))}, unexpected {sorted(set(docs) - expected_sets)}")
    return docs


def open_and_load(a):
    wanted = set(a.sets.split(",")) if a.sets else None
    revision = revision_of_documents(a.out, wanted)
    df, captures_sha = load_captures(a.captures, revision)
    rows_by_set = manifest_rows(df)
    docs = load_docs(a.out, rows_by_set, revision, captures_sha, wanted)
    return docs, rows_by_set, revision, captures_sha


def cmd_aggregate(a):
    docs, _, _, _ = open_and_load(a)
    _, runs = classify_docs(docs)
    for sid, r in runs.items():
        print(f"{sid} ({r['site_key']}): visits used {r['visits_used']} (excluded {r['visits_excluded']}), observable {r['observable_fraction_400_470_mhz']:.1%} of 400-470 MHz, "
              f"persistent lines {r['persistent_lines']}, RF-fixed {r['rf_fixed_persistent_lines']}")


def cmd_classify(a):
    docs, rows_by_set, revision, captures_sha = open_and_load(a)
    classified, runs = classify_docs(docs)
    from collections import Counter
    counts = Counter(x["class"] for x in classified)
    per_run_manifests = {sid: manifest_sha256(rows_by_set[sid]) for sid in docs}
    total = sum(len(rows_by_set[sid]) for sid in docs)
    prov = provenance_for(revision, captures_sha, sha256_text(canonical(per_run_manifests)), total, total, True)
    prov["capture_manifest_sha256_by_run"] = per_run_manifests
    prov["detection_document_sha256"] = {sid: d["provenance"]["detection_document_sha256"] for sid, d in docs.items()}
    result = {"provenance": prov, "config": CONFIG, "runs": runs, "class_counts": dict(counts), "distinct_rf_fixed_frequencies": len(classified), "classified": classified}
    with open(a.results, "w") as fh:
        json.dump(result, fh, indent=1)
    print(dict(counts))
    for x in classified:
        if x["class"] != "SINGLE_RUN":
            flag = "  [association unresolved]" if x["unresolved_association"] else ""
            print(f"{x['freq_hz']/1e6:10.4f} MHz  {x['class']:34s} runs {x['runs_detecting']}/{x['runs_observed']}  sites {','.join(x['sites_detecting'])}  snr {x['max_snr_db']}{flag}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect")
    d.add_argument("out")
    d.add_argument("--revision", required=True, help="pinned dataset revision (full 40-character commit sha)")
    d.add_argument("--captures", help="local copy of metadata/captures.parquet; must be byte-identical to the file at that revision")
    d.add_argument("--sets")
    d.add_argument("--local-root", default=os.path.expanduser("~/s521_runs"))
    d.add_argument("--workers", type=int, default=3)
    d.add_argument("--remote-only", action="store_true", help="read every window from the pinned dataset revision, never from --local-root")
    g = sub.add_parser("aggregate")
    g.add_argument("out")
    g.add_argument("--captures")
    g.add_argument("--sets")
    k = sub.add_parser("classify")
    k.add_argument("out")
    k.add_argument("--results", required=True)
    k.add_argument("--captures")
    k.add_argument("--sets")
    a = ap.parse_args()
    try:
        return {"detect": cmd_detect, "aggregate": cmd_aggregate, "classify": cmd_classify}[a.cmd](a) or 0
    except IntegrityError as exc:
        print("REFUSED:", exc, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
