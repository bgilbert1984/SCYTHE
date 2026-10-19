#!/usr/bin/env python3
"""Independent inventory and hash check (replacement Gate 3, r4.1). Separately implemented from tools/r4_job/seal_job.py: shares no code with it.
Uses the coreutils `sha256sum` binary for every digest (the packaged sealer uses hashlib).

    indep_verify_job.py JOB --expected-fixture-list FIXTURES_LIST --generated-pin PIN [--require-fixtures]

Checks: manifest syntax; every listed file exists and matches; no unlisted file (excluding holdout/ and the manifest itself); no symlinks;
STATUS.txt ends with a terminal DONE/FAILED record; manifest is not older than any sealed file (nothing written after sealing);
the job's sealed fixture list equals the externally pinned list; every fixture file present matches its pinned hash, none missing/extra.
"""
import os, re, subprocess, sys

job = os.path.abspath(sys.argv[1]); pinned = sys.argv[sys.argv.index("--expected-fixture-list") + 1]; pin_gen = sys.argv[sys.argv.index("--generated-pin") + 1]
req = "--require-fixtures" in sys.argv; failrec = "--failure-record" in sys.argv   # failure record: a preflight failure precedes fixture generation
out, fails = [], []
def rep(k, v, bad=False):
    out.append(f"{k}: {v}")
    if bad: fails.append(k)
def sha(path):
    r = subprocess.run(["sha256sum", "--", path], capture_output=True, text=True, check=True); return r.stdout.split()[0]

man = os.path.join(job, "JOB_SHA256SUMS")
if not os.path.isfile(man):
    rep("MANIFEST", "ABSENT", True); entries = {}
else:
    entries, syntax = {}, []
    txt = open(man).read(); mlines = txt.split("\n")
    if mlines and mlines[-1] == "": mlines.pop()
    for i, l in enumerate(mlines, 1):
        m = re.fullmatch(r"([0-9a-f]{64})  (.+)", l)
        if not m or m.group(2).startswith("/") or ".." in m.group(2).split("/") or m.group(2) in entries: syntax.append(i); continue
        entries[m.group(2)] = m.group(1)
    rep("MANIFEST_SYNTAX", f"{len(entries)} entries, bad lines {syntax}", bool(syntax))

missing, mismatch = [], []
for rel, d in entries.items():
    p = os.path.join(job, rel)
    if not os.path.isfile(p): missing.append(rel)
    elif sha(p) != d: mismatch.append(rel)
on_disk, links = [], []
for root, dirs, fs in os.walk(job):
    if os.path.relpath(root, job) == ".": dirs[:] = [d for d in dirs]
    for f in fs + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
        p = os.path.join(root, f); rel = os.path.relpath(p, job)
        if os.path.islink(p): links.append(rel)
        elif rel != "JOB_SHA256SUMS" and rel.split("/")[0] != "holdout": on_disk.append(rel)
extra = sorted(set(on_disk) - set(entries))
rep("FILES_LISTED_AND_PRESENT", f"{len(entries) - len(missing)}/{len(entries)}", bool(missing)); rep("MISSING_FILES", missing[:10], bool(missing))
rep("HASH_MISMATCHES", mismatch[:10], bool(mismatch)); rep("EXTRA_UNLISTED_FILES", extra[:10], bool(extra)); rep("SYMLINKS", links[:5], bool(links))
rep("ON_DISK_FILES_OUTSIDE_HOLDOUT", len(on_disk))

st = os.path.join(job, "STATUS.txt"); lines = [l for l in open(st).read().splitlines() if l.strip()] if os.path.exists(st) else []
term = bool(lines) and re.match(r"\S+ (DONE|FAILED)\b", lines[-1]) is not None
rep("TERMINAL_STATUS_IS_LAST_LINE", lines[-1][:70] if lines else "none", not term)
if os.path.isfile(man):
    mt = os.stat(man).st_mtime_ns; newer = [r for r in entries if os.path.exists(os.path.join(job, r)) and os.stat(os.path.join(job, r)).st_mtime_ns > mt]
    rep("NOTHING_MODIFIED_AFTER_MANIFEST", f"{len(newer)} files newer than manifest {newer[:3]}", bool(newer))

pin = {}
for l in open(pinned):
    if l.strip(): d, n = l.split(None, 1); pin[n.strip()] = d
for l in open(pin_gen):
    if l.strip(): d, n = l.split(None, 1); assert n.strip() not in pin, "pin overlap"; pin[n.strip()] = d
rep("EXPECTED_FILE_COUNT", len(pin), len(pin) != 37)
jl = os.path.join(job, "fixture_hashes.sha256")
if not os.path.isfile(jl): rep("SEALED_FIXTURE_LIST", "ABSENT (expected for a pre-fixture failure record)" if failrec else "ABSENT", not failrec)
else:
    jin = {}
    for l in open(jl):
        if l.strip(): d, n = l.split(None, 1); jin[n.strip()] = d
    jg = {}
    if os.path.isfile(os.path.join(job, "generated_artifacts.sha256")):
        for l in open(os.path.join(job, "generated_artifacts.sha256")):
            if l.strip(): d, n = l.split(None, 1); jg[n.strip()] = d
    rep("SEALED_PINS_EQUAL_PACKAGE_PINS", f"{len(jin)}+{len(jg)} sealed vs {len(pin)} package", {**jin, **jg} != pin)
    rep("GENERATED_PIN_IS_IN_MANIFEST", "generated_artifacts.sha256" in entries, "generated_artifacts.sha256" not in entries)
    rep("SEALED_FIXTURE_LIST_IS_IN_MANIFEST", "fixture_hashes.sha256" in entries, "fixture_hashes.sha256" not in entries)
hd = os.path.join(job, "holdout")
if not os.path.isdir(hd):
    rep("FIXTURE_BYTES", "NOT_PRESENT_NOT_REVERIFIED", req)
else:
    have = sorted(os.listdir(hd)); fm = [n for n in pin if n not in have]; fx = [n for n in have if n not in pin]
    nonreg = [n for n in have if os.path.islink(os.path.join(hd, n)) or not os.path.isfile(os.path.join(hd, n))]
    bad = [n for n in pin if n in have and n not in nonreg and sha(os.path.join(hd, n)) != pin[n]]
    fx = fx + nonreg
    rep("FIXTURE_BYTES", f"{len(pin) - len(fm) - len(bad)}/{len(pin)} match pinned hashes; missing={fm[:3]} mismatched={bad[:3]} extra={fx[:3]}", bool(fm or bad or fx))
print("\n".join(out)); print("INDEPENDENT_VERDICT:", "FAIL" if fails else "PASS", fails); sys.exit(1 if fails else 0)
