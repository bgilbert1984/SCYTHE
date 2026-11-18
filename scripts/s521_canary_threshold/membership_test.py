"""Membership check for the margin-shift test: do the SAME candidates survive in load-fitted and antenna-on captures?

margin_shift_test.py compared counts only. Here, per paired visit (same tuning and retune), candidates are detected on the
load-fitted capture at the 10 dB margin (filtering that list by excess_db gives the result at higher margins, because the
peak rule is the same) and on the antenna-on capture at 10 dB, and matched by baseband frequency within a tolerance.

Reported per visit and pooled:
  on_in_load10   share of antenna-on candidates that have a load-fitted candidate (>10 dB) within tol
  on_in_loadM    the same against the load-fitted candidates above margin M (M = 12: the count-matched margin)
  load_in_on     share of load-fitted candidates above 10 dB that the antenna-on capture also has (floor compression predicts the weak
                 ones are lost and the strong ones kept; reported by load-excess band)
  shift_null     the antenna-on offsets moved by +123,456 Hz (wrapped): chance overlap if offsets had no fixed structure
  null           the same on_in_loadM with the antenna-on visit paired to a load-fitted visit of a DIFFERENT tuning, same delta
                 (same candidate density and near-DC cloud, no shared physical spurs): what chance overlap looks like.
Usage: python membership_test.py --load LOAD_DIR --on ON_DIR [--tol-hz 60] [--exclude-dc-hz 12000] [--out results.json]
Reads only.
"""
import argparse, json, os, sys, statistics
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import rf_spur_catalogue_sequencer as seq   # noqa: E402

BANDS = ((10, 12), (12, 14), (14, 17), (17, 1e9))


def cands(path):
    raw = open(path, "rb").read()
    w = [seq._decode_iq(raw[i * seq.WINDOW_BYTES:(i + 1) * seq.WINDOW_BYTES]) for i in range(len(raw) // seq.WINDOW_BYTES)]
    return np.array([(c.baseband_hz, c.excess_db) for c in seq.detect_candidates(w)]).reshape(-1, 2)


def drop_dc(x, hz):
    return x[np.abs(x[:, 0]) >= hz] if hz else x


def share(a_hz, b_hz, tol):
    """share of a that has some b within tol (both 1-D, b sorted)."""
    if len(a_hz) == 0:
        return None
    if len(b_hz) == 0:
        return 0.0
    i = np.searchsorted(b_hz, a_hz)
    lo = np.abs(a_hz - b_hz[np.clip(i - 1, 0, len(b_hz) - 1)])
    hi = np.abs(a_hz - b_hz[np.clip(i, 0, len(b_hz) - 1)])
    return float((np.minimum(lo, hi) <= tol).mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", required=True); ap.add_argument("--on", required=True)
    ap.add_argument("--tol-hz", type=float, default=60.0); ap.add_argument("--exclude-dc-hz", type=float, default=0.0); ap.add_argument("--out")
    a = ap.parse_args()
    caps = json.load(open(os.path.join(a.load, "capture_log.json")))["captures"]
    L, O = {}, {}
    for c in caps:
        L[c["file"]] = drop_dc(cands(os.path.join(a.load, c["file"])), a.exclude_dc_hz); O[c["file"]] = drop_dc(cands(os.path.join(a.on, c["file"])), a.exclude_dc_hz)
    key = {c["file"]: (c["tuning_index"], c["delta_hz"]) for c in caps}
    rows = []
    for f in L:
        ti, d = key[f]
        other = [g for g in L if key[g][1] == d and key[g][0] != ti]
        g = other[(ti // 8) % len(other)]            # deterministic different-tuning partner
        on = O[f][:, 0]; ld = L[f]
        row = {"file": f, "n_on": len(on), "n_load10": len(ld)}
        for m in (10, 12):
            lm = np.sort(ld[ld[:, 1] > m, 0]); lg = np.sort(L[g][L[g][:, 1] > m, 0])
            row[f"on_in_load{m}"] = share(on, lm, a.tol_hz); row[f"null_on_in_load{m}"] = share(on, lg, a.tol_hz)
            row[f"n_load{m}"] = len(lm)
            sh = np.where(on + 123456.0 > 900000.0, on + 123456.0 - 1800000.0, on + 123456.0)   # offset-shifted null: breaks any fixed-offset structure
            row[f"shift_null_on_in_load{m}"] = share(sh, lm, a.tol_hz)
        onsorted = np.sort(on)
        for lo, hi in BANDS:
            sel = ld[(ld[:, 1] > lo) & (ld[:, 1] <= hi), 0]
            row[f"load_in_on_{lo}_{int(min(hi,99))}"] = share(sel, onsorted, a.tol_hz); row[f"n_load_band_{lo}"] = len(sel)
        rows.append(row)
    def med(k):
        v = [r[k] for r in rows if r.get(k) is not None]; return (round(statistics.median(v), 3), round(min(v), 3), round(max(v), 3))
    print(f"tol {a.tol_hz} Hz, excluding |offset| < {a.exclude_dc_hz} Hz, {len(rows)} paired visits; median (min, max)")
    for k in ("n_on", "n_load10", "n_load12", "on_in_load10", "null_on_in_load10", "on_in_load12", "null_on_in_load12", "shift_null_on_in_load10", "shift_null_on_in_load12"):
        print(f"  {k:20s}", med(k))
    for lo, hi in BANDS:
        k = f"load_in_on_{lo}_{int(min(hi,99))}"; print(f"  load>{lo} dB retained by on: ", med(k), " (median n/visit", statistics.median(r[f'n_load_band_{lo}'] for r in rows), ")")
    if a.out:
        json.dump({"tol_hz": a.tol_hz, "exclude_dc_hz": a.exclude_dc_hz, "rows": rows}, open(a.out, "w"), indent=1)

if __name__ == "__main__":
    main()
