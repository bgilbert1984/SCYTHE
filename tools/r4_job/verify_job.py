#!/usr/bin/env python3
"""Independent verifier for a sealed r4 job. Implemented separately from seal_job.py (imports nothing from it; uses the coreutils `sha256sum` for digests).

    verify_job.py JOB_DIR [--require-fixtures] [--fixture-list FILE] [--generated-pin FILE]

The pins default to this package's own copies (tools/r4_job/generated_artifacts.sha256, fixtures/fixture_hashes.sha256), NOT the job's copies; the job's
sealed copies must equal them. Reports three SEPARATE dispositions in the last lines:
    INDEPENDENT_VERIFICATION: PASS | FAIL     manifest, inventory, ordering and fixture custody
    TERMINAL_STATUS: DONE | FAILED | NONE     what the job itself recorded
    JOB_ACCEPTED: YES | NO                    YES only if verification passed AND the terminal record is DONE (a verified FAILED record is intact evidence of a failure, not an accepted job)
Exit status: 0 iff INDEPENDENT_VERIFICATION is PASS.
"""
import json, os, re, subprocess, sys

here = os.path.dirname(os.path.abspath(__file__))
def opt(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
job = os.path.abspath(sys.argv[1]); req = "--require-fixtures" in sys.argv
pin_fix = opt("--fixture-list", os.path.join(here, "..", "..", "fixtures", "fixture_hashes.sha256"))
pin_gen = opt("--generated-pin", os.path.join(here, "generated_artifacts.sha256"))
out, fails = [], []
def rep(key, val, bad=False):
    out.append(f"{key}: {val}")
    if bad: fails.append(key)
def sha(path):
    return subprocess.run(["sha256sum", "--", path], capture_output=True, text=True, check=True).stdout.split()[0]
def read_list(path):
    d, probs = {}, []
    for n, l in enumerate(open(path).read().split("\n"), 1):
        if not l.strip(): continue
        m = re.fullmatch(r"([0-9a-f]{64})  (\S.*)", l)
        if not m: probs.append(f"malformed line {n}"); continue
        if m.group(2) in d: probs.append(f"duplicate {m.group(2)}")
        d[m.group(2)] = m.group(1)
    return d, probs

# ---- manifest and inventory
man = os.path.join(job, "JOB_SHA256SUMS"); entries = {}
if not os.path.isfile(man):
    rep("MANIFEST", "ABSENT", True)
else:
    lines = open(man).read().split("\n")
    if lines and lines[-1] == "": lines.pop()
    bad_lines = []
    for i, l in enumerate(lines, 1):
        m = re.fullmatch(r"([0-9a-f]{64})  (.+)", l)
        if not m or m.group(2).startswith("/") or ".." in m.group(2).split("/") or m.group(2) in entries: bad_lines.append(i); continue
        entries[m.group(2)] = m.group(1)
    rep("MANIFEST_SYNTAX", f"{len(entries)} entries, bad lines {bad_lines}", bool(bad_lines))
missing = [r for r in entries if not os.path.isfile(os.path.join(job, r))]
mismatch = [r for r in entries if r not in missing and sha(os.path.join(job, r)) != entries[r]]
on_disk, links = [], []
for root, dirs, fs in os.walk(job):
    top = os.path.relpath(root, job).split(os.sep)[0]
    for name in fs + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
        p = os.path.join(root, name); rel = os.path.relpath(p, job)
        if os.path.islink(p): links.append(rel)
        elif rel != "JOB_SHA256SUMS" and top != "holdout": on_disk.append(rel)
if os.path.islink(os.path.join(job, "holdout")): links.append("holdout")
extra = sorted(set(on_disk) - set(entries))
rep("FILES_LISTED_AND_PRESENT", f"{len(entries) - len(missing)}/{len(entries)}", bool(missing))
rep("HASH_MISMATCHES", mismatch[:10], bool(mismatch)); rep("UNLISTED_FILES", extra[:10], bool(extra)); rep("SYMLINKS", links[:5], bool(links))

# ---- terminal record and ordering
st = os.path.join(job, "STATUS.txt"); recs = [l for l in open(st).read().splitlines() if l.strip()] if os.path.exists(st) else []
m = re.match(r"\S+ (DONE|FAILED)\b", recs[-1]) if recs else None
terminal = m.group(1) if m else "NONE"
rep("TERMINAL_RECORD_IS_THE_LAST_LINE", recs[-1][:80] if recs else "none", terminal == "NONE")
if os.path.isfile(man):
    t = os.stat(man).st_mtime_ns
    newer = [r for r in entries if os.path.exists(os.path.join(job, r)) and os.stat(os.path.join(job, r)).st_mtime_ns > t]
    rep("NOTHING_WRITTEN_AFTER_THE_MANIFEST", newer[:5], bool(newer))

# ---- fixture custody against the PACKAGE pins
jl, jp, hd = (os.path.join(job, n) for n in ("fixture_hashes.sha256", "generated_artifacts.sha256", "holdout"))
if not os.path.exists(jl) and not os.path.isdir(hd) and terminal == "FAILED":
    rep("FIXTURE_CUSTODY", "NOT_APPLICABLE (the job failed before fixtures were generated)")
else:
    want, p1 = read_list(pin_fix) if os.path.exists(pin_fix) else ({}, ["package fixture list not found"])
    gen, p2 = read_list(pin_gen) if os.path.exists(pin_gen) else ({}, ["package generated-artifact pin not found"])
    probs = p1 + p2 + [f"{n} pinned in both lists" for n in set(want) & set(gen)]
    for mine, sealed in ((pin_fix, jl), (pin_gen, jp)):
        if not os.path.exists(sealed): probs.append(f"{os.path.basename(sealed)} absent from the job")
        elif os.path.exists(mine) and open(mine, "rb").read() != open(sealed, "rb").read(): probs.append(f"{os.path.basename(sealed)} differs from the package pin")
        elif os.path.basename(sealed) not in entries: probs.append(f"{os.path.basename(sealed)} is not sealed in the manifest")
    rep("PINS", f"{len(want)} fixtures + {len(gen)} generated artifacts = {len(want) + len(gen)} expected files", bool(probs)); [rep("PIN_PROBLEM", p, True) for p in probs[:10]]
    if not os.path.isdir(hd):
        rep("FIXTURE_BYTES", "NOT_PRESENT_NOT_REVERIFIED", req)
    else:
        exp = {**want, **gen}; have = sorted(os.listdir(hd))
        nonreg = [n for n in have if os.path.islink(os.path.join(hd, n)) or not os.path.isfile(os.path.join(hd, n))]
        fm = [n for n in exp if n not in have]; fx = [n for n in have if n not in exp]
        bad = [n for n in exp if n in have and n not in nonreg and sha(os.path.join(hd, n)) != exp[n]]
        rep("FIXTURE_BYTES", f"{len(have)} files; {len(exp) - len(fm) - len(bad)}/{len(exp)} match; missing={fm[:3]} mismatched={bad[:3]} unexpected={fx[:3]} non-regular={nonreg[:3]}", bool(fm or bad or fx or nonreg))
        ip = os.path.join(hd, "index.json")
        if "index.json" in gen and os.path.isfile(ip) and "index.json" not in bad:
            try:
                ix = json.load(open(ip)); ents = [e for f in ("family_a", "family_b") for e in ix[f]]
                described = sorted(e["name"] + ".npy" for e in ents); pinned = sorted(want)
                ok = described == pinned and len({e["seed"] for e in ents}) == len(ents) and sorted(ix) == ["family_a", "family_b"]
                rep("INDEX_DESCRIBES_THE_PINNED_FIXTURE_SET", f"{len(described)} described vs {len(pinned)} pinned", not ok)
            except (ValueError, KeyError, TypeError) as exc:
                rep("INDEX_DESCRIBES_THE_PINNED_FIXTURE_SET", f"unreadable index: {exc}", True)

print("\n".join(out))
ver = "FAIL" if fails else "PASS"
print(f"INDEPENDENT_VERIFICATION: {ver} {fails}"); print(f"TERMINAL_STATUS: {terminal}")
accepted = ver == "PASS" and terminal == "DONE"
print("JOB_ACCEPTED: " + ("YES" if accepted else "NO" + (" (verification failed)" if ver == "FAIL" else f" (terminal record is {terminal}; an intact failure record is not an accepted job)")))
sys.exit(0 if ver == "PASS" else 1)
