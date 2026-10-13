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

def run(d, base_doc=None):
    paths = []
    def dump(x):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(x, fh); paths.append(fh.name); return fh.name
    try:
        left = base if base_doc is None else dump(base_doc)
        return subprocess.run([sys.executable, cmp_, left, dump(d)], capture_output=True).returncode
    finally:
        for p in paths:
            os.unlink(p)

def first_cand(d):
    return next(f for f in d["per_fixture"] if f["candidates"])["candidates"][0]

cases = {}
d = copy.deepcopy(doc); first_cand(d)["excess_db"] += 1e-6; cases["excess_db +1e-6 (> 1e-9)"] = d
d = copy.deepcopy(doc); first_cand(d)["baseband_hz"] += 1e-6; cases["baseband_hz +1e-6 (> 1e-9)"] = d
d = copy.deepcopy(doc); c = first_cand(d); c["persistent"] = not c["persistent"]; cases["persistence flipped"] = d
d = copy.deepcopy(doc); first_cand(d)["peak_bin"] += 1; cases["peak_bin +1"] = d
d = copy.deepcopy(doc); fx = next(f for f in d["per_fixture"] if f["candidates"]); fx["candidates"].pop(); cases["candidate dropped"] = d
d = copy.deepcopy(doc); fx = next(f for f in d["per_fixture"] if f["candidates"]); fx["candidates"].pop(); fx["candidate_count"] -= 1; cases["candidate dropped, count updated consistently"] = d
d = copy.deepcopy(doc); fx = next(f for f in d["per_fixture"] if f["candidates"]); fx["candidate_count"] += 1; cases["candidate_count altered, list intact"] = d
d = copy.deepcopy(doc); d["per_fixture"].pop(); cases["last fixture dropped"] = d
d = copy.deepcopy(doc); d["per_fixture"].pop(0); cases["first fixture dropped"] = d
d = copy.deepcopy(doc); d["per_fixture"].append(copy.deepcopy(d["per_fixture"][0])); cases["extra fixture appended"] = d
d = copy.deepcopy(doc); d["code"]["sequencer_sha256"] = "0" * 64; cases["module hash changed"] = d

# Both inputs identically incoherent: they compare equal to each other, so only a per-input
# coherence check can reject them.
_b = json.load(open(base)); _c = copy.deepcopy(doc)
for _d in (_b, _c):
    _f = next(f for f in _d["per_fixture"] if f["candidates"]); _f["candidates"].pop()
pair_rc = run(_c, _b)

ok = run(doc) == 0
print(f"unmodified pair exit==0: {ok}")
for name, mutated in cases.items():
    rc = run(mutated); rejected = rc != 0; ok &= rejected
    print(f"mutation '{name}': exit={rc} rejected={rejected}")
rejected = pair_rc != 0; ok &= rejected
print(f"mutation 'both inputs identically incoherent': exit={pair_rc} rejected={rejected}")
print("NEGATIVE_CONTROL:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
