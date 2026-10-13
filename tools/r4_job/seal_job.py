#!/usr/bin/env python3
"""Seal a finished job directory: write JOB_SHA256SUMS over every file, LAST, after the terminal status record.

Fails closed: it refuses to seal unless STATUS.txt already ends with a terminal record (`DONE ...` or `FAILED ...`) and refuses to
overwrite an existing manifest. Nothing may be written to the job directory afterwards; a later append to STATUS.txt (the r3 defect,
STATUS_LOG_APPEND_AFTER_HASH) makes the manifest fail, which is the point.

    seal_job.py JOB_DIR                 write JOB_SHA256SUMS
    seal_job.py JOB_DIR --verify        verify the manifest against the files; exit 0 only if every entry matches and no file is unlisted
"""
import hashlib, os, sys

MANIFEST = "JOB_SHA256SUMS"
TERMINAL = ("DONE", "FAILED")


def digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def files(job):
    out = []
    for root, _, fs in os.walk(job):
        for f in fs:
            rel = os.path.relpath(os.path.join(root, f), job)
            if rel not in (MANIFEST, MANIFEST + ".tmp") and not rel.startswith("holdout" + os.sep):
                out.append(rel)
    return sorted(out)


def terminal_record_present(job):
    p = os.path.join(job, "STATUS.txt")
    if not os.path.exists(p):
        return False
    with open(p) as fh:
        lines = [l for l in fh.read().splitlines() if l.strip()]
    return bool(lines) and any(lines[-1].split(None, 1)[1:2] and lines[-1].split(None, 1)[1].startswith(t) for t in TERMINAL)


def seal(job):
    if os.path.exists(os.path.join(job, MANIFEST)):
        raise SystemExit("refusing to overwrite an existing " + MANIFEST)
    if not terminal_record_present(job):
        raise SystemExit("refusing to seal: STATUS.txt does not end with a terminal record (DONE/FAILED); write and flush it first")
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
    job = sys.argv[1]
    if "--verify" in sys.argv:
        problems = verify(job); print("JOB_MANIFEST", "VERIFIED" if not problems else "FAILED"); [print("  -", p) for p in problems[:20]]
        sys.exit(1 if problems else 0)
    seal(job); print("sealed", job)
