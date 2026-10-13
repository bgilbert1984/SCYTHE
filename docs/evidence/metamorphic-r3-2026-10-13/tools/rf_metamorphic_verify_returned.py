#!/usr/bin/env python3
"""Independent verification of a returned metamorphic r3 job directory (read-only).

Usage: rf_metamorphic_verify_returned.py JOB_DIR EXPECTED_HARNESS_SHA256 [--fixtures 36 --mutant-fixtures 6]
Checks, from the records and artifacts only: job-level checksums (STATUS.txt may differ only by lines appended
after hashing), harness-level manifests, source and production-module digests against the approved experiment,
fixture hashes, identity/namespace controls, record counts, declared dispositions, summary-versus-records
agreement, and that no refused mutant carries relation credit.  Exits 0 only if every check passes.
"""
import glob, hashlib, json, os, sys, collections
jd, want_harness = sys.argv[1], sys.argv[2]
NF = int(sys.argv[sys.argv.index("--fixtures") + 1]) if "--fixtures" in sys.argv else 36
NM = int(sys.argv[sys.argv.index("--mutant-fixtures") + 1]) if "--mutant-fixtures" in sys.argv else 6
APPROVED_PROD = {"rf_spur_catalogue_sequencer.py": "e1e82172cc2e56f05709d60b9586b4c675890f5472824eaaa90f6a461b211b82",
                 "rf_spur_retention_estimator.py": "fea25501c2584dbe04e836246a9907c358c371e660458925649741592145e9db"}
fails, notes = [], []
def need(c, m):
    if not c: fails.append(m)
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()

# 1. job-level checksums; STATUS.txt is allowed to differ only by lines appended after hashing
for line in open(os.path.join(jd, "JOB_SHA256SUMS")):
    d, rel = line.split(None, 1); rel = rel.strip(); p = os.path.join(jd, rel)
    if not os.path.exists(p): fails.append(f"missing {rel}"); continue
    if sha(p) == d: continue
    if rel == "STATUS.txt":
        lines = open(p).read().splitlines(keepends=True)
        ok = any(hashlib.sha256("".join(lines[:i]).encode()).hexdigest() == d for i in range(len(lines), 0, -1))
        if ok: notes.append("STATUS.txt: recorded digest matches the file minus lines appended after hashing (expected ordering artifact)"); continue
    fails.append(f"checksum mismatch {rel}")
# 2. harness-level manifests
def manifest_ok(rd):
    bad, miss, n = [], [], 0
    for line in open(os.path.join(rd, "MANIFEST.sha256")):
        if not line.strip(): continue
        d, rel = line.split(None, 1); p = os.path.join(rd, rel.strip()); n += 1
        if not os.path.exists(p): miss.append(rel.strip())
        elif sha(p) != d: bad.append(rel.strip())
    return n, bad, miss
for d in ("results_smoke", "results_full"):
    n, bad, miss = manifest_ok(os.path.join(jd, d)); need(not bad and not miss, f"{d} manifest bad={bad} missing={miss}"); notes.append(f"{d}: {n} manifest entries verified")
rd = os.path.join(jd, "results_full")
hdr = json.load(open(os.path.join(rd, "run_header.json")))
# 3. source / revision / production modules / fixtures
need(hdr["revision"] == "metamorphic-harness.r3", f"revision {hdr['revision']}")
need(hdr["harness_sha256"] == want_harness, "harness digest differs from the approved commit")
for f, d in APPROVED_PROD.items(): need(hdr["production_sha256"].get(f) == d, f"production module {f} digest differs")
need(all(v == "1" for v in hdr["thread_env"].values()), f"thread env {hdr['thread_env']}")
fv = open(os.path.join(jd, "logs", "fixtures_verify.txt")).read().splitlines()
need(len(fv) == NF and all(l.endswith(": OK") for l in fv), "fixture hash verification log not 36/36 OK")
# 4. identity / namespace
ident = json.load(open(os.path.join(rd, "identity.json")))
need(ident["verdict"] == "IDENTITY_AND_NAMESPACE_VERIFIED" and len(ident["per_fixture"]) == NF and all(ident["per_fixture"].values()) and not ident["namespace"]["identity_mismatches"], "identity/namespace control")
need(not os.path.exists(os.path.join(rd, "STOPPED.json")), "STOPPED.json present")
# 5. record counts, dispositions
A = sorted(glob.glob(os.path.join(rd, "phaseA", "*.json"))); B = sorted(glob.glob(os.path.join(rd, "phaseB", "*.json")))
need(len(A) == NF, f"phase A records {len(A)}"); need(len(B) == 4 * NM, f"phase B records {len(B)}")
bs = [json.load(open(f)) for f in B]
need(all(b["status"] in ("COMPLETED", "RESOURCE_LIMIT_REFUSAL") for b in bs), "undeclared phase B status")
need(all(not b["relations"] for b in bs if b["status"] == "RESOURCE_LIMIT_REFUSAL"), "refused mutant carries relation credit")
need(all(d["credit"] != "MUTANT_DETECTED" for b in bs if b["status"] != "COMPLETED" for d in b.get("relations", {}).values()), "resource refusal misclassified as detection")
# 6. summary agrees with individual records
summ = json.load(open(os.path.join(rd, "summary.json")))
cnt = collections.defaultdict(collections.Counter)
for f in A:
    for k, d in json.load(open(f))["relations"].items(): cnt[k][d["verdict"]] += 1
need({k: dict(v) for k, v in cnt.items()} == summ["relation_summary"], "summary relation_summary != records")
pc = collections.defaultdict(collections.Counter)
for f in A:
    for c in json.load(open(f)).get("persistence", {}).values():
        for k, d in c.items(): pc[k][d["verdict"]] += 1
need({k: dict(v) for k, v in pc.items()} == summ["persistence_summary"], "summary persistence_summary != records")
mm = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
for b in bs:
    for k, d in b["relations"].items(): mm[b["mutant"]][k][d["credit"]] += 1
need({m: {k: dict(c) for k, c in v.items()} for m, v in mm.items()} == {m: {k: c for k, c in v.items() if k != "_status"} for m, v in summ["mutant_matrix"].items()}, "summary mutant_matrix != records")
# 7. final gate artifacts and exit codes
need(open(os.path.join(jd, "logs", "full_gate.txt")).readline().strip() == "GATE PASS", "final gate not PASS")
ec = open(os.path.join(jd, "exit_codes.txt")).read()
need(all(f"{k} exit=0" in ec for k in ("smoke", "smoke gate", "full", "full gate", "r2-vs-r3 equivalence")), "exit codes")
eq = json.load(open(os.path.join(jd, "equivalence_r2_vs_r3.json")))
need(eq["verdict"] == "EQUIVALENT" and eq["max_abs_diff_db"] <= 1e-9, "r2-vs-r3 equivalence")
print("VERIFICATION", "PASS" if not fails else "FAIL")
for n in notes: print("  note:", n)
for m in fails: print("  FAIL:", m)
sys.exit(0 if not fails else 1)
