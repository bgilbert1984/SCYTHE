#!/usr/bin/env python3
"""Redaction-aware verification of the committed metamorphic r3 evidence directory (stdlib only).

Usage: verify_committed_evidence.py EVIDENCE_DIR [--originals DEDIROCK_JOB_DIR LOCAL_JOB_DIR]

Two layers are verified WITHOUT weakening either:
  1. COMMITTED_EVIDENCE_INTEGRITY   the outer SHA256SUMS describes exactly the committed bytes: every listed file matches,
     no file is missing, and no file exists that the manifest does not list.
  2. ORIGINAL manifests, read from the committed tree: every entry of every inner manifest (the harness MANIFEST.sha256 files
     and JOB_SHA256SUMS) must match the committed file, EXCEPT entries for the files declared in REDACTIONS.txt, whose
     entry must equal the recorded ORIGINAL digest. The set of inner-manifest mismatches must equal the declared redactions
     that appear in those manifests exactly: an undeclared mismatch is rejected, and a declared redaction that did not
     actually change the file is rejected. STATUS.txt is checked against STATUS_ADDENDUM.txt (sealed digest, original final
     digest, committed digest).
Outcome lines:
  COMMITTED_EVIDENCE_INTEGRITY: VERIFIED | FAILED
  ORIGINAL_MANIFEST_ENTRIES_VS_RECORDED_ORIGINAL_DIGESTS: CONSISTENT | INCONSISTENT
  ORIGINAL_JOB_INTEGRITY: VERIFIED_FROM_RETAINED_ORIGINALS | NOT_REVERIFIED_FROM_THIS_CHECKOUT | FAILED
Without --originals the last line is NOT_REVERIFIED_FROM_THIS_CHECKOUT: prior verification of the originals is historical evidence
(historical_verification_results.txt), not something this checkout re-ran. Exit 0 only if no line says FAILED/INCONSISTENT.
"""
import hashlib, os, re, subprocess, sys

ev = os.path.abspath(sys.argv[1]); fails = []
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
def read_sums(path):
    out = {}
    for line in open(path):
        if line.strip():
            d, rel = line.split(None, 1); out[os.path.normpath(rel.strip())] = d
    return out

# ---- layer 1: outer manifest vs committed bytes
outer = read_sums(os.path.join(ev, "SHA256SUMS"))
on_disk = {os.path.relpath(os.path.join(r, f), ev) for r, _, fs in os.walk(ev) for f in fs} - {"SHA256SUMS"}
layer1 = []
for rel, d in outer.items():
    p = os.path.join(ev, rel)
    if not os.path.exists(p): layer1.append(f"missing {rel}")
    elif sha(p) != d: layer1.append(f"outer mismatch {rel}")
for rel in sorted(on_disk - set(outer)): layer1.append(f"undeclared file {rel}")
def show(msgs, limit=12):
    for m in msgs[:limit]: print("  -", m)
    if len(msgs) > limit: print(f"  ... and {len(msgs) - limit} more")


print("COMMITTED_EVIDENCE_INTEGRITY:", "VERIFIED" if not layer1 else "FAILED", f"({len(outer)} files listed, {len(on_disk)} on disk)"); show(layer1)
fails += layer1

# ---- declared redactions
red = {}
for line in open(os.path.join(ev, "REDACTIONS.txt")):
    parts = line.split()
    if len(parts) == 3 and re.fullmatch(r"[0-9a-f]{64}", parts[0]) and re.fullmatch(r"[0-9a-f]{64}", parts[1]):
        red[parts[2]] = (parts[0], parts[1])
layer2 = []
for rel, (orig, comm) in red.items():
    if rel not in on_disk: layer2.append(f"declared redaction missing from tree: {rel}")
    elif sha(os.path.join(ev, rel)) != comm: layer2.append(f"committed copy of {rel} != REDACTIONS committed digest")
    if orig == comm: layer2.append(f"{rel}: declared as redacted but original == committed")

# ---- STATUS addendum cross-check
add = open(os.path.join(ev, "STATUS_ADDENDUM.txt")).read()
for job in ("dedirock", "local"):
    sec = re.search(r"\[%s\](.*?)(?=\n\[|\Z)" % job, add, re.S).group(1)
    g = lambda label: re.search(label + r":? ([0-9a-f]{64})", sec).group(1)
    sealed, final, comm = g(r"sealed digest \(JOB_SHA256SUMS, unchanged\)"), g("complete final status log"), g(r"committed \(path-redacted\) copy")
    key = f"{job}/STATUS.txt"
    if read_sums(os.path.join(ev, job, "JOB_SHA256SUMS")).get("STATUS.txt") != sealed: layer2.append(f"{job}: addendum sealed digest != JOB_SHA256SUMS")
    if red.get(key, ("", ""))[0] != final: layer2.append(f"{job}: addendum final digest != REDACTIONS original")
    if red.get(key, ("", ""))[1] != comm or sha(os.path.join(ev, key)) != comm: layer2.append(f"{job}: addendum committed digest mismatch")

# ---- inner manifests, read from the committed tree
checked = 0; observed_mismatch = set()
for job in ("dedirock", "local"):
    jd = os.path.join(ev, job)
    inner = {"JOB_SHA256SUMS": read_sums(os.path.join(jd, "JOB_SHA256SUMS"))}
    for sub in ("results_full", "results_smoke"):
        inner[f"{sub}/MANIFEST.sha256"] = {f"{sub}/{r}": d for r, d in read_sums(os.path.join(jd, sub, "MANIFEST.sha256")).items()}
    for name, entries in inner.items():
        for rel, d in entries.items():
            p = os.path.join(jd, rel); key = f"{job}/{rel}"; checked += 1
            if not os.path.exists(p): layer2.append(f"{name}: entry for missing file {key}"); continue
            if sha(p) == d: continue
            observed_mismatch.add(key)
            if key not in red: layer2.append(f"{name}: UNDECLARED mismatch {key}")
            elif key.endswith("/STATUS.txt"): pass                  # sealed digest checked against the addendum above
            elif red[key][0] != d: layer2.append(f"{name}: entry for {key} != recorded ORIGINAL digest")
declared_in_manifests = {k for k in red if not k.endswith("/STATUS.txt") or True}
for key in red:
    jobfile = key.split("/", 1)
    appears = any(key == f"{j}/{rel}" for j in ("dedirock", "local") for rel in
                  list(read_sums(os.path.join(ev, j, "JOB_SHA256SUMS"))))
    if appears and key not in observed_mismatch: layer2.append(f"declared redaction {key} never mismatched its manifest entry")
print("ORIGINAL_MANIFEST_ENTRIES_VS_RECORDED_ORIGINAL_DIGESTS:", "CONSISTENT" if not layer2 else "INCONSISTENT",
      f"({checked} inner-manifest entries read, {len(observed_mismatch)} declared-redaction mismatches, {len(red)} declared redactions)")
show(layer2); fails += layer2

# ---- layer 3: originals, only if supplied
if "--originals" in sys.argv:
    i = sys.argv.index("--originals"); od = {"dedirock": sys.argv[i + 1], "local": sys.argv[i + 2]}; l3 = []
    verifier = os.path.join(ev, "tools", "rf_metamorphic_verify_returned.py")
    for job, d in od.items():
        r = subprocess.run([sys.executable, verifier, d, "e1e655f68fa0"], capture_output=True, text=True)
        # the verifier needs the full harness digest; read it from the original run header
        import json
        want = json.load(open(os.path.join(d, "results_full", "run_header.json")))["harness_sha256"]
        r = subprocess.run([sys.executable, verifier, d, want], capture_output=True, text=True)
        if r.returncode != 0: l3.append(f"{job}: original job verification failed: {r.stdout.strip()[-200:]}")
        for key, (orig, comm) in red.items():
            if key.startswith(job + "/"):
                p = os.path.join(d, key.split("/", 1)[1])
                if not os.path.exists(p) or sha(p) != orig: l3.append(f"{key}: retained original does not match the recorded original digest")
    print("ORIGINAL_JOB_INTEGRITY:", "VERIFIED_FROM_RETAINED_ORIGINALS" if not l3 else "FAILED"); show(l3); fails += l3
else:
    print("ORIGINAL_JOB_INTEGRITY: NOT_REVERIFIED_FROM_THIS_CHECKOUT  (the retained originals are not in this checkout; "
          "earlier verification is preserved as historical evidence in historical_verification_results.txt)")
sys.exit(1 if fails else 0)
