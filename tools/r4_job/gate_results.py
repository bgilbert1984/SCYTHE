#!/usr/bin/env python3
"""Acceptance gate for an r4 results directory. Checks integrity and bookkeeping from the records; scientific findings are REPORTED, not gated.

Usage: gate_results.py RESULTS_DIR EXPECTED_FIXTURES EXPECTED_MUTANT_FIXTURES [--boundary-only]
Re-derives every verdict and credit from the stored observations with the harness's own judging logic, and refuses any record that disagrees.
No layer may claim production-path coverage. A resource refusal never carries relation credit.
"""
import glob, importlib.util, json, os, sys

rd, nfix, nmut = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]); boundary_only = "--boundary-only" in sys.argv
here = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("h", os.path.join(here, "..", "rf_metamorphic_harness.py"))
H = importlib.util.module_from_spec(spec); spec.loader.exec_module(H)
fails, info = [], []
def need(c, m):
    if not c: fails.append(m)
def load(p):
    with open(p) as fh: return json.load(fh)

need(not os.path.exists(os.path.join(rd, "STOPPED.json")), "STOPPED.json present (identity failure)")
ident = load(os.path.join(rd, "identity.json"))
need(ident["verdict"] == "IDENTITY_AND_NAMESPACE_VERIFIED" and len(ident["per_fixture"]) == nfix and all(ident["per_fixture"].values()), "identity/namespace control")
ok, bad, missing = H.verify_manifest(rd); need(not bad and not missing, f"manifest bad={bad} missing={missing}")
hdr = load(os.path.join(rd, "run_header.json")); need(hdr["revision"] == "metamorphic-harness.r4", f"revision {hdr['revision']}")

def rejudge(rec, n, label):
    if rec.get("status") != "EVALUATED":
        return
    need(rec.get("attribution_ok") is True and rec.get("pair_ok", True) is True, f"{label}: EVALUATED without a passed attribution gate")
    for v, j in rec["judgments"].items():
        vd, credit, _ = H.judge(v, rec["observed"], rec["expected_bins"], rec.get("target", rec.get("bin")), n, rec.get("pair_ok", True))
        need((vd, credit) == (j["verdict"], j["credit"]), f"{label}/{v}: stored {j} != re-derived {(vd, credit)}")
        if j["credit"] == "MUTANT_DETECTED":
            f = float(H.grid(n)[rec.get("target", rec.get("bin"))])
            need(H.SPAN_PREDICATES[v](f) != H.SPAN_PREDICATES["production"](f), f"{label}/{v}: detection credited where the variant is indistinguishable")

la = load(os.path.join(rd, "layerA.json")); need(la["detector_involved"] is False, "layer A involves the detector")
g = la["geometries"]
need(g[str(H.PROD_N)]["computed_exactly_on_boundary"] == [] and g["524280"]["computed_exactly_on_boundary"] == [] and g["500000"]["computed_exactly_on_boundary"] == [225000],
     "layer A geometry differs from the documented grid")
lp = load(os.path.join(rd, "layerBprime.json")); need(lp["production_path"] is False, "layer B' claims production path")
need(len(lp["cases"]) == sum(2 * len(b) for _, b in H.BPRIME_PLAN), f"layer B' case count {len(lp['cases'])}")
for c in lp["cases"]:
    need(c["n"] != H.PROD_N and not c["production_length"], "layer B' contains the production length")
    rejudge({**c, "status": "EVALUATED", "pair_ok": True, "attribution_ok": c["attribution_ok"], "target": c["bin"]}, c["n"], f"B'/{c['n']}/{c['bin']}")
lb = sorted(glob.glob(os.path.join(rd, "layerB", "*.json"))); need(len(lb) == nfix, f"layer B records {len(lb)} != {nfix}")
viol = ev = pre = 0
for f in lb:
    rec = load(f); need(rec["production_path"] is False, f"{f}: layer B claims production path")
    for c in rec["cases"]:
        if c["status"] == "EVALUATED":
            ev += 1; viol += c["judgments"]["production"]["verdict"] == H.V; rejudge(c, rec["n"], os.path.basename(f))
        else:
            pre += 1; need(c["status"] == H.P and c.get("reason"), f"{f}: unexplained non-evaluated case")
info.append(f"layer B: {ev} cases evaluated, {pre} PRECONDITION_NOT_MET, production violations (a FINDING, not a gate failure): {viol}")
if not boundary_only:
    A = sorted(glob.glob(os.path.join(rd, "phaseA", "*.json"))); B = sorted(glob.glob(os.path.join(rd, "phaseB", "*.json")))
    need(len(A) == nfix, f"phase A records {len(A)}"); need(len(B) == len(H.MUTATIONS) * nmut, f"phase B records {len(B)} != {len(H.MUTATIONS) * nmut}")
    for f in B:
        e = load(f); need(e["status"] in ("COMPLETED", "RESOURCE_LIMIT_REFUSAL"), f"{f}: status {e['status']}")
        need(not (e["status"] == "RESOURCE_LIMIT_REFUSAL" and e.get("relations")), f"{f}: refusal carries relation credit")
print("GATE", "PASS" if not fails else "FAIL")
for m in info: print("  info:", m)
for m in fails[:30]: print("  FAIL:", m)
sys.exit(0 if not fails else 1)
