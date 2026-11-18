"""Margin-shift test of the candidate-count gap (antenna-on vs terminated), paired by visit.

Question: is the ~2x lower per-visit candidate count of antenna-on captures explained by a higher noise floor?
The detector (`rf_spur_catalogue_sequencer.detect_candidates`) counts local maxima more than PLAN_PERSISTENCE_MARGIN_DB
above the median of the median spectrum. A flat floor rise compresses every excess, so a floor rise of x dB should lower
the count about as much as raising the margin by x dB on a terminated capture. This script counts candidates on each
LOAD-FITTED visit at margins 10..14 dB and compares them with the antenna-on count of the same visit (same tuning and
retune) at the real 10 dB margin.

Usage:  python margin_shift_test.py --load LOAD_DIR --on ON_DIR [--out results.json]
Both directories hold `capture_log.json` and the same `.u8` files (the capture scripts' layout; raw uint8 I/Q, 8 windows
of seq.WINDOW_BYTES per visit). Reads only; the detector margin is restored after each count.
Counts are compared, not the candidates themselves: that the same candidates survive is NOT tested here.
"""
import argparse
import json
import os
import statistics
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import rf_spur_catalogue_sequencer as seq   # noqa: E402

MARGINS_DB = (10.0, 11.0, 12.0, 13.0, 14.0)


def windows_of(path):
    raw = open(path, "rb").read()
    return [seq._decode_iq(raw[i * seq.WINDOW_BYTES:(i + 1) * seq.WINDOW_BYTES]) for i in range(len(raw) // seq.WINDOW_BYTES)]


def count_at(windows, margin_db):
    saved = seq.PLAN_PERSISTENCE_MARGIN_DB
    seq.PLAN_PERSISTENCE_MARGIN_DB = margin_db
    try:
        return len(seq.detect_candidates(windows))
    finally:
        seq.PLAN_PERSISTENCE_MARGIN_DB = saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", required=True)
    ap.add_argument("--on", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    files = [c["file"] for c in json.load(open(os.path.join(args.load, "capture_log.json")))["captures"]]
    on_counts = []
    load_counts = {m: [] for m in MARGINS_DB}
    for name in files:
        w = windows_of(os.path.join(args.load, name))
        for m in MARGINS_DB:
            load_counts[m].append(count_at(w, m))
        on_counts.append(count_at(windows_of(os.path.join(args.on, name)), seq.PLAN_PERSISTENCE_MARGIN_DB))
    result = {"visits": len(files), "margins_db": list(MARGINS_DB), "files": files,
              "antenna_on_counts_at_10db": on_counts,
              "load_fitted_counts_by_margin": {str(m): v for m, v in load_counts.items()}}
    print(f"antenna-on at {seq.PLAN_PERSISTENCE_MARGIN_DB} dB: median {statistics.median(on_counts):.1f}")
    for m in MARGINS_DB:
        v = load_counts[m]
        q = np.percentile(v, [25, 75])
        print(f"load-fitted at {m:.1f} dB: median {statistics.median(v):.1f} (q25-q75 {q[0]:.0f}-{q[1]:.0f})")
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(result, handle, indent=1)
            handle.write("\n")


if __name__ == "__main__":
    main()
