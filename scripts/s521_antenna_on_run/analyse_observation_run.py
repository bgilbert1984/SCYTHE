"""Numbers quoted in docs/521_ANTENNA_ON_FIRST_OBSERVATION_RUN_EVIDENCE.md, from the run's antenna_on_observation.json
(`python analyse_observation_run.py <run dir>`). The raw windows are in the archive named in the doc; the sample std needs them."""
import collections
import json
import sys

import numpy as np

run_dir = sys.argv[1].rstrip("/") + "/"
d = json.load(open(run_dir + "antenna_on_observation.json"))
v = sorted(d["visits"], key=lambda x: x["position"])
print(d["summary"])
print("reasons", collections.Counter(r for x in v for r in x["gate"]["reasons"]))
passed = [x["gate"]["passed"] for x in v]
for s in range(0, len(v), 16):
    print(f"visits {s}-{s + 15}: {sum(passed[s:s + 16])}/16 pass")
k = max(range(10, len(v) - 10), key=lambda i: sum(passed[:i]) / i - sum(passed[i:]) / (len(v) - i))
print("changepoint (max pass-rate drop) at visit", k, "pass rate before/after", round(sum(passed[:k]) / k, 2), round(sum(passed[k:]) / (len(v) - k), 2))


def weaker(x):
    e = x["gate"]["canary_excess_db"]
    return min(e.values()) if e and all(y is not None for y in e.values()) else None


for name, part in (("first 96", v[:96]), ("last 96", v[96:])):
    ex = [weaker(x) for x in part if weaker(x) is not None]
    print(name, "both detected", len(ex), "weaker-side excess min/med/max", round(min(ex), 1), round(float(np.median(ex)), 1), round(max(ex), 1),
          "| candidates median", float(np.median([x["n_candidates"] for x in part])))
for name, part in (("early", v[:96]), ("late", v[96:])):
    lo = [x for x in part if x["lo_hz"] < 435e6]
    hi = [x for x in part if x["lo_hz"] >= 435e6]
    print(name, "lower-band pass", sum(x["gate"]["passed"] for x in lo), "/", len(lo), " upper-band pass", sum(x["gate"]["passed"] for x in hi), "/", len(hi))
try:
    std = [float(np.fromfile(run_dir + x["spool_path"], dtype=np.uint8, count=2000000).astype(np.float32).std()) for x in v]
    for name, part in (("first 96", std[:96]), ("last 96", std[96:])):
        print(name, "sample std min/med/max", round(min(part), 2), round(float(np.median(part)), 2), round(max(part), 2))
except FileNotFoundError:
    print("spool not present: skipped the sample-std numbers")
