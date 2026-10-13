#!/usr/bin/env python3
"""Seal a finished job directory: write JOB_SHA256SUMS over every file, LAST, after the terminal status record.

Fixture custody (approach: bind, then re-verify). The regenerated fixture set (`holdout/`, ~1.2 GB) is NOT hashed into JOB_SHA256SUMS. Instead the
fixture hash LIST (`fixture_hashes.sha256`) is a sealed file of the job, so the list itself is bound by the manifest, and:
  * at seal time the actual files under holdout/ are re-verified against that list and the sealer REFUSES to seal on any mismatch, missing or extra file;
  * independent verification (`--verify`) re-verifies the actual fixture files against the sealed list. If the fixture bytes are absent the outcome is
    NOT_PRESENT_NOT_REVERIFIED (an explicit non-claim); `--require-fixtures` turns that, and a missing list, into a failure.
JOB_SHA256SUMS therefore proves the list; the re-verification step proves the bytes. A fixture changed after sealing cannot pass verification with the bytes present.

Fails closed: it refuses to seal unless STATUS.txt already ends with a terminal record (`DONE ...` or `FAILED ...`) and refuses to
overwrite an existing manifest. Nothing may be written to the job directory afterwards; a later append to STATUS.txt (the r3 defect,
STATUS_LOG_APPEND_AFTER_HASH) makes the manifest fail, which is the point.

    seal_job.py JOB_DIR                 write JOB_SHA256SUMS
    seal_job.py JOB_DIR --verify        verify the manifest against the files; exit 0 only if every entry matches and no file is unlisted
"""
import hashlib, os, sys

MANIFEST = "JOB_SHA256SUMS"
FIXTURE_LIST = "fixture_hashes.sha256"
HOLDOUT = "holdout"
TERMINAL = ("DONE", "FAILED")


def digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def files(job):
    out = []
    for root, _, fs in os.walk(job):
        for f in fs:
            rel = os.path.relpath(os.path.join(root, f), job)
            if rel not in (MANIFEST, MANIFEST + ".tmp") and not rel.startswith(HOLDOUT + os.sep):
                out.append(rel)
    return sorted(out)


def terminal_record_present(job):
    p = os.path.join(job, "STATUS.txt")
    if not os.path.exists(p):
        return False
    with open(p) as fh:
        lines = [l for l in fh.read().splitlines() if l.strip()]
    return bool(lines) and any(lines[-1].split(None, 1)[1:2] and lines[-1].split(None, 1)[1].startswith(t) for t in TERMINAL)


def check_fixtures(job):
    """Re-verify the actual fixture files against the job's fixture hash list.

    Returns (state, problems); state is one of VERIFIED_AGAINST_SEALED_HASH_LIST, NOT_PRESENT_NOT_REVERIFIED, FIXTURE_LIST_MISSING, FAILED.
    """
    lst = os.path.join(job, FIXTURE_LIST); hd = os.path.join(job, HOLDOUT)
    if not os.path.exists(lst):
        return "FIXTURE_LIST_MISSING", []
    if not os.path.isdir(hd):
        return "NOT_PRESENT_NOT_REVERIFIED", []
    want = {}
    with open(lst) as fh:
        for line in fh:
            if line.strip():
                d, name = line.split(None, 1); want[name.strip()] = d
    have = sorted(f for f in os.listdir(hd) if os.path.isfile(os.path.join(hd, f)))
    problems = [f"fixture missing {n}" for n in want if n not in have]
    problems += [f"fixture mismatch {n}" for n in want if n in have and digest(os.path.join(hd, n)) != want[n]]
    problems += [f"fixture not in the sealed list {n}" for n in have if n not in want]
    return ("FAILED" if problems else "VERIFIED_AGAINST_SEALED_HASH_LIST"), problems


def seal(job, require_fixtures=False):
    if os.path.exists(os.path.join(job, MANIFEST)):
        raise SystemExit("refusing to overwrite an existing " + MANIFEST)
    if not terminal_record_present(job):
        raise SystemExit("refusing to seal: STATUS.txt does not end with a terminal record (DONE/FAILED); write and flush it first")
    state, problems = check_fixtures(job)
    if state == "FAILED" or (require_fixtures and state != "VERIFIED_AGAINST_SEALED_HASH_LIST"):
        raise SystemExit(f"refusing to seal: fixture custody {state} {problems[:5]}")
    tmp = os.path.join(job, MANIFEST + ".tmp")
    with open(tmp, "w") as fh:
        for rel in files(job):
            fh.write(f"{digest(os.path.join(job, rel))}  {rel}\n")
        fh.flush(); os.fsync(fh.fileno())
    os.replace(tmp, os.path.join(job, MANIFEST))


def verify(job):
    bad, listed = [], set()
    with open(os.path.join(job, MANIFEST)) as fh:
        manifest_lines = fh.read().splitlines()
    for line in manifest_lines:
        if line.strip():
            d, rel = line.split(None, 1); rel = rel.strip(); listed.add(rel)
            p = os.path.join(job, rel)
            if not os.path.exists(p): bad.append(f"missing {rel}")
            elif digest(p) != d: bad.append(f"mismatch {rel}")
    bad += [f"unlisted {r}" for r in files(job) if r not in listed]
    return bad


if __name__ == "__main__":
    job = sys.argv[1]; req = "--require-fixtures" in sys.argv
    if "--verify" in sys.argv:
        problems = verify(job); print("JOB_MANIFEST:", "VERIFIED" if not problems else "FAILED"); [print("  -", p) for p in problems[:20]]
        state, fprob = check_fixtures(job)
        print("FIXTURE_BYTES:", state); [print("  -", p) for p in fprob[:20]]
        bad = bool(problems) or state == "FAILED" or (req and state != "VERIFIED_AGAINST_SEALED_HASH_LIST")
        if state == "NOT_PRESENT_NOT_REVERIFIED":
            print("  (the fixture bytes are absent; they were NOT re-verified here. The sealed list binds the expected hashes only.)")
        sys.exit(1 if bad else 0)
    seal(job, require_fixtures=req); print("sealed", job)
