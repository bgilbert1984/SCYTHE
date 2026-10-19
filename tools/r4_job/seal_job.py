#!/usr/bin/env python3
"""Seal a finished job directory: write JOB_SHA256SUMS over every file, LAST, after the terminal status record.

Fixture custody (approach: bind, then re-verify). The regenerated fixture directory (`holdout/`, ~1.2 GB) is NOT hashed into JOB_SHA256SUMS. Instead two
pin lists are sealed files of the job, so the lists are bound by the manifest:
  * `fixture_hashes.sha256`      the 36 frozen fixture (.npy) hashes, unchanged from the equivalence package;
  * `generated_artifacts.sha256` the separately pinned generated artifacts (r4.1: `index.json`, written by the frozen fixture generator).
`holdout/` must contain EXACTLY the files named by the two lists (37), nothing else. At seal time the actual files are re-verified and the sealer REFUSES to
seal on any missing, modified, duplicate, unexpected or non-regular entry, and when `index.json` contradicts the .npy set it claims to describe. Independent
verification (`--verify`, and the separately implemented verify_job.py) repeats the check. Absent fixture bytes are the explicit NOT_PRESENT_NOT_REVERIFIED;
`--require-fixtures` makes that, and a missing list or pin, a failure. When this script sits in a package, the sealed lists must equal the package's own copies.

Fails closed: it refuses to seal unless STATUS.txt already ends with a terminal record (`DONE ...` or `FAILED ...`) and refuses to
overwrite an existing manifest. Nothing may be written to the job directory afterwards; a later append to STATUS.txt (the r3 defect,
STATUS_LOG_APPEND_AFTER_HASH) makes the manifest fail, which is the point. After writing, the manifest is read back; only then is EVIDENCE_SEALED printed.
EVIDENCE_SEALED is not JOB_ACCEPTED: acceptance needs the separate verify_job.py.

    seal_job.py JOB_DIR                 write JOB_SHA256SUMS
    seal_job.py JOB_DIR --verify        verify the manifest against the files; exit 0 only if every entry matches and no file is unlisted
"""
import hashlib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = "JOB_SHA256SUMS"
FIXTURE_LIST = "fixture_hashes.sha256"
GENERATED_PIN = "generated_artifacts.sha256"
INDEX = "index.json"
# where the authoritative copy of each sealed pin list lives when this script sits in a package (a pin that differs from it is refused)
AUTHORITY = {FIXTURE_LIST: os.path.join(HERE, "..", "..", "fixtures", FIXTURE_LIST), GENERATED_PIN: os.path.join(HERE, GENERATED_PIN)}
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


def parse_list(path):
    """Return ({name: digest}, [problems]); duplicate names and malformed lines are problems."""
    out, problems = {}, []
    with open(path) as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            m = re.fullmatch(r"([0-9a-f]{64})  (\S.*)", line.rstrip("\n"))
            if not m:
                problems.append(f"malformed line {n} in {os.path.basename(path)}"); continue
            if m.group(2) in out:
                problems.append(f"duplicate entry {m.group(2)} in {os.path.basename(path)}")
            out[m.group(2)] = m.group(1)
    return out, problems


def index_problems(index, npy_names):
    """The index must describe exactly the pinned .npy fixtures: every entry named once, none missing, none invented, seeds unique."""
    if not isinstance(index, dict) or sorted(index) != ["family_a", "family_b"]:
        return ["index.json families are not exactly family_a and family_b"]
    names, seeds, problems = [], [], []
    for fam in ("family_a", "family_b"):
        for e in index[fam]:
            names.append(e.get("name")); seeds.append(e.get("seed"))
    problems += [f"index names {n} more than once" for n in sorted({n for n in names if names.count(n) > 1}, key=str)]
    described = {f"{n}.npy" for n in names}
    problems += [f"index does not describe fixture {n}" for n in sorted(npy_names - described)]
    problems += [f"index describes a fixture that is not pinned: {n}" for n in sorted(described - npy_names)]
    if len(set(seeds)) != len(seeds) or any(not isinstance(x, int) for x in seeds):
        problems.append("index seeds are not unique integers")
    return problems


def check_fixtures(job):
    """Re-verify the fixture directory against the job's two sealed pin lists.

    Returns (state, problems); state is one of VERIFIED_AGAINST_SEALED_HASH_LIST, NOT_PRESENT_NOT_REVERIFIED, FIXTURE_LIST_MISSING, GENERATED_PIN_MISSING, FAILED.
    """
    lst = os.path.join(job, FIXTURE_LIST); pin = os.path.join(job, GENERATED_PIN); hd = os.path.join(job, HOLDOUT)
    if not os.path.exists(lst):
        return "FIXTURE_LIST_MISSING", []
    if not os.path.exists(pin):
        return "GENERATED_PIN_MISSING", []
    want, problems = parse_list(lst); gen, p2 = parse_list(pin); problems += p2
    problems += [f"{n} is pinned in both lists" for n in sorted(set(want) & set(gen))]
    for sealed in (lst, pin):
        authority = AUTHORITY.get(os.path.basename(sealed))
        if authority and os.path.exists(authority):
            with open(sealed, "rb") as a, open(authority, "rb") as b:
                if a.read() != b.read():
                    problems.append(f"{os.path.basename(sealed)} differs from the package's own copy")
    if not os.path.isdir(hd):
        return ("FAILED", problems) if problems else ("NOT_PRESENT_NOT_REVERIFIED", [])
    expected = {**want, **gen}
    have = sorted(os.listdir(hd))
    problems += [f"fixture-directory entry is not a regular file {n}" for n in have if os.path.islink(os.path.join(hd, n)) or not os.path.isfile(os.path.join(hd, n))]
    problems += [f"fixture missing {n}" for n in expected if n not in have]
    problems += [f"fixture mismatch {n}" for n in expected if n in have and os.path.isfile(os.path.join(hd, n)) and digest(os.path.join(hd, n)) != expected[n]]
    problems += [f"fixture not in the sealed list {n}" for n in have if n not in expected]
    if INDEX in gen and INDEX in have and not any(INDEX in p for p in problems):
        try:
            with open(os.path.join(hd, INDEX)) as fh:
                problems += index_problems(json.load(fh), set(want))
        except (ValueError, AttributeError, TypeError) as exc:
            problems.append(f"index.json is not a well-formed fixture index: {exc}")
    return ("FAILED" if problems else "VERIFIED_AGAINST_SEALED_HASH_LIST"), problems


def seal(job, require_fixtures=False):
    if os.path.exists(os.path.join(job, MANIFEST)):
        raise SystemExit("refusing to overwrite an existing " + MANIFEST)
    if not terminal_record_present(job):
        raise SystemExit("refusing to seal: STATUS.txt does not end with a terminal record (DONE/FAILED); write and flush it first")
    state, problems = check_fixtures(job)
    if state in ("FAILED", "GENERATED_PIN_MISSING") or (require_fixtures and state != "VERIFIED_AGAINST_SEALED_HASH_LIST"):
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
        bad = bool(problems) or state in ("FAILED", "GENERATED_PIN_MISSING") or (req and state != "VERIFIED_AGAINST_SEALED_HASH_LIST")
        if state == "NOT_PRESENT_NOT_REVERIFIED":
            print("  (the fixture bytes are absent; they were NOT re-verified here. The sealed list binds the expected hashes only.)")
        sys.exit(1 if bad else 0)
    seal(job, require_fixtures=req)
    rb, st = verify(job), check_fixtures(job)[0]                      # read the published manifest back before claiming anything
    if rb or st == "FAILED":
        print("EVIDENCE_NOT_SEALED: read-back failed", rb[:5], st); sys.exit(3)
    print("sealed", job); print("EVIDENCE_SEALED: YES (manifest written and read back; this is not JOB_ACCEPTED)")
