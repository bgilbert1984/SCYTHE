#!/usr/bin/env python3
"""Prove compare_hosts.py exits nonzero on deliberately introduced mismatches.

Usage: comparator_negative_control.py baseline.json candidate.json
Each case mutates an in-memory copy written to a temp file; inputs are untouched.
Exit 0 only if the unmodified pair passes AND every mutation is rejected.
"""
import copy, json, os, subprocess, sys, tempfile
base, cand = sys.argv[1:3]
here = os.path.dirname(os.path.abspath(__file__))
cmp_ = os.path.join(here, "compare_hosts.py")
doc = json.load(open(cand))

def run(d):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(d, fh); path = fh.name
    try:
        return subprocess.run([sys.executable, cmp_, base, path], capture_output=True).returncode
    finally:
        os.unlink(path)

def first_cand(d):
    return next(f for f in d["per_fixture"] if f["candidates"])["candidates"][0]

cases = {}
d = copy.deepcopy(doc); first_cand(d)["excess_db"] += 1e-6; cases["excess_db +1e-6 (> 1e-9)"] = d
d = copy.deepcopy(doc); first_cand(d)["baseband_hz"] += 1e-6; cases["baseband_hz +1e-6 (> 1e-9)"] = d
d = copy.deepcopy(doc); c = first_cand(d); c["persistent"] = not c["persistent"]; cases["persistence flipped"] = d
d = copy.deepcopy(doc); first_cand(d)["peak_bin"] += 1; cases["peak_bin +1"] = d
d = copy.deepcopy(doc); fx = next(f for f in d["per_fixture"] if f["candidates"]); fx["candidates"].pop(); cases["candidate dropped"] = d
d = copy.deepcopy(doc); d["code"]["sequencer_sha256"] = "0" * 64; cases["module hash changed"] = d

ok = run(doc) == 0
print(f"unmodified pair exit==0: {ok}")
for name, mutated in cases.items():
    rc = run(mutated); rejected = rc != 0; ok &= rejected
    print(f"mutation '{name}': exit={rc} rejected={rejected}")
print("NEGATIVE_CONTROL:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
