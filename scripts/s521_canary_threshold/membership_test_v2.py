"""Membership check v2: one-to-one matching, empirical cross-tuning null, block-bootstrap uncertainty, strength strata.

Separates three quantities that the count comparison (margin_shift_test.py) risked conflating:
  detection density      how many candidates pass the threshold (counts; reported, not scored here)
  membership stability   survival = share of REFERENCE candidates that have a one-to-one counterpart in the OTHER capture of the
                         same visit (same tuning and retune)
  tuning discrimination  delta = survival(paired) - survival(control), control = the same reference visit matched against the other
                         capture's visits of the 7 OTHER tunings at the same retune (fixed baseband structure shared by all tunings
                         gives the control as much overlap as the pairing; only the excess is tuning-specific)

Fixed before looking at v2 results: tolerances 60 Hz (the canary match tolerance) and 500 Hz; exclusion |baseband| < 12 kHz (the
near-DC cloud); candidate extraction = seq.detect_candidates at the 10 dB persistence margin, unchanged; strata of REFERENCE
excess (10,12], (12,14], (14,17], (17,inf). Matching is one-to-one (greedy two-pointer on sorted offsets, which is a maximum
matching for a fixed tolerance), so a dense cluster cannot be counted several times.
Uncertainty: 95% percentile interval of a bootstrap over the 8 tuning blocks (4 visits each; candidates and visits within a
tuning are correlated, so they are not resampled individually), 5000 resamples, seed 7535.
Usage: python membership_test_v2.py --ref DIR --other DIR --label NAME --out results.json
Reads only.
"""
import argparse, hashlib, json, os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import rf_spur_catalogue_sequencer as seq   # noqa: E402

TOLS = (60.0, 500.0)
EXCLUDE_DC_HZ = 12000.0
BANDS = ((10.0, 12.0), (12.0, 14.0), (14.0, 17.0), (17.0, float("inf")))
BOOT, SEED = 5000, 7535


def cands(path):
    raw = open(path, "rb").read()
    w = [seq._decode_iq(raw[i * seq.WINDOW_BYTES:(i + 1) * seq.WINDOW_BYTES]) for i in range(len(raw) // seq.WINDOW_BYTES)]
    x = np.array([(c.baseband_hz, c.excess_db) for c in seq.detect_candidates(w)]).reshape(-1, 2)
    return x[np.abs(x[:, 0]) >= EXCLUDE_DC_HZ]


def matched(a, b, tol):
    """size of a maximum one-to-one matching between sorted 1-D arrays a, b within tol."""
    i = j = n = 0
    while i < len(a) and j < len(b):
        if abs(a[i] - b[j]) <= tol:
            n += 1; i += 1; j += 1
        elif b[j] < a[i]:
            j += 1
        else:
            i += 1
    return n


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def boot_ci(blocks, rng):
    """blocks: list (per tuning) of arrays of per-visit values (NaN = undefined). Mean over resampled blocks."""
    flat = [np.array([v for v in b if v == v]) for b in blocks]
    est = float(np.mean(np.concatenate(flat))) if sum(len(f) for f in flat) else None
    if est is None:
        return None
    reps = []
    for _ in range(BOOT):
        pick = rng.integers(0, len(flat), len(flat))
        cat = np.concatenate([flat[k] for k in pick])
        if len(cat):
            reps.append(cat.mean())
    return [round(est, 4), round(float(np.percentile(reps, 2.5)), 4), round(float(np.percentile(reps, 97.5)), 4)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True); ap.add_argument("--other", required=True)
    ap.add_argument("--label", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    caps = json.load(open(os.path.join(a.ref, "capture_log.json")))["captures"]
    R = {c["file"]: cands(os.path.join(a.ref, c["file"])) for c in caps}
    O = {c["file"]: cands(os.path.join(a.other, c["file"])) for c in caps}
    key = {c["file"]: (c["tuning_index"], c["delta_hz"]) for c in caps}
    tunings = sorted({k[0] for k in key.values()})
    res = {"label": a.label, "ref_dir": os.path.basename(a.ref.rstrip("/")), "other_dir": os.path.basename(a.other.rstrip("/")),
           "ref_manifest_sha256": sha(os.path.join(a.ref, "MANIFEST.sha256")), "other_manifest_sha256": sha(os.path.join(a.other, "MANIFEST.sha256")),
           "script_sha256": sha(os.path.abspath(__file__)), "exclude_dc_hz": EXCLUDE_DC_HZ, "bootstrap": BOOT, "seed": SEED, "visits": len(caps),
           "counts_outside_cloud": {"ref_median": float(np.median([len(v) for v in R.values()])), "other_median": float(np.median([len(v) for v in O.values()]))},
           "strata_ref_candidates_total": {}, "results": {}}
    for lo, hi in BANDS:
        res["strata_ref_candidates_total"][f"{lo:g}-{hi:g}"] = int(sum(((v[:, 1] > lo) & (v[:, 1] <= hi)).sum() for v in R.values()))
    rng = np.random.default_rng(SEED)
    for tol in TOLS:
        out = {}
        # strata survival (reference candidates in band matched to the other capture's candidates) + overall other-in-ref
        series = {}
        for f in caps:
            f = f["file"]; ti, d = key[f]
            partners = [g for g in O if key[g][1] == d and key[g][0] != ti]
            ob = np.sort(O[f][:, 0])
            groups = [("ref_in_other_%g_%g" % b, (R[f][:, 1] > b[0]) & (R[f][:, 1] <= b[1])) for b in BANDS] + [("ref_in_other_all", np.ones(len(R[f]), bool))]
            for name, sel in groups:
                rf = np.sort(R[f][sel, 0])
                if len(rf) == 0:
                    pv = cv = float("nan")
                else:
                    pv = matched(rf, ob, tol) / len(rf)
                    cv = float(np.mean([matched(rf, np.sort(O[g][:, 0]), tol) / len(rf) for g in partners]))
                s = series.setdefault(name, {"paired": {}, "control": {}, "delta": {}, "n": 0})
                s["paired"].setdefault(ti, []).append(pv); s["control"].setdefault(ti, []).append(cv)
                s["delta"].setdefault(ti, []).append(pv - cv if pv == pv else float("nan")); s["n"] += int(sel.sum())
        for name, s in series.items():
            out[name] = {"ref_candidates_total": s["n"]}
            for k in ("paired", "control", "delta"):
                out[name][k + "_mean_ci95"] = boot_ci([np.array(s[k][t]) for t in tunings], rng)
        res["results"][f"tol_{tol:g}_hz"] = out
    json.dump(res, open(a.out, "w"), indent=1)
    print(a.label, "ref/other median candidates outside cloud:", res["counts_outside_cloud"])
    for t, out in res["results"].items():
        print(" ", t)
        for name, o in out.items():
            print("   %-24s n=%5d paired %s  control %s  delta %s" % (name, o["ref_candidates_total"], o["paired_mean_ci95"], o["control_mean_ci95"], o["delta_mean_ci95"]))


if __name__ == "__main__":
    main()
