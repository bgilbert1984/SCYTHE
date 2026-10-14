#!/usr/bin/env python3
"""Redaction-aware verification of the committed r4 full-run evidence (stdlib only; shares no code with the harness or the package tools).

    verify_full_run_evidence.py EVIDENCE_DIR ANCHOR_DOC [--original-prefix PREFIX]

EVIDENCE_DIR is docs/evidence/r4_full_local_2026-10-13 in a checkout or a pristine `git archive`. Layers kept apart:

  COMMITTED_EVIDENCE_INTEGRITY   every committed file matches the committed digest in REDACTION_MANIFEST.txt; the file set equals the manifest (nothing added or missing);
                                 each row's redaction declaration is internally consistent; the manifest's digest is anchored in ANCHOR_DOC.
  ORIGINAL_MANIFEST_ENTRIES_VS_RECORDED_ORIGINAL_DIGESTS
                                 every ORIGINAL manifest the run produced (JOB_SHA256SUMS, results MANIFEST.sha256, EXEC_SHA256SUMS, each PREFLIGHT_SHA256SUMS, the operator-confirmation
                                 digest) agrees with the original digest recorded for the same file; every job file is listed by the job manifest. Unredacted files are therefore equal to the
                                 sealed originals byte for byte; redacted ones are tied to the sealed original digest through the declared transformation.
  ORIGINAL_JOB_INTEGRITY         the sealed job directory (including the 1.2 GB fixture corpus) and the archive copies are not in a checkout: NOT_REVERIFIED_FROM_THIS_CHECKOUT, always.
  REDACTION_TRANSFORM_REVERSAL   with --original-prefix only: reversing $HOME -> PREFIX must reproduce each redacted file's recorded original digest; otherwise NOT_EVALUATED.
Retained original manifests are parsed strictly: a malformed line, a duplicate path, an absolute or '..' path, a reference to a file that is neither committed nor DECLARED omitted in the anchored
manifest (OMITTED rows carry the expected digest and a reason), an OMITTED digest that disagrees with the reference, and a committed file that no original manifest lists (other than the manifests themselves and the
anchored UNMANIFESTED prefixes) are all failures. Declared omissions (the fixture corpus and the package archive copies) are reported, never silently skipped.
Nothing is rewritten. Exit 0 iff no check failed; an unhandled exception exits 2 with VERIFIER_CRASH.
"""
import hashlib, os, re, sys, traceback


def _crash(kind, value, tb):
    traceback.print_exception(kind, value, tb); print("VERIFIER_CRASH:", kind.__name__, value); sys.stdout.flush(); os._exit(2)


sys.excepthook = _crash
root, doc_path = sys.argv[1], sys.argv[2]
prefix = sys.argv[sys.argv.index("--original-prefix") + 1] if "--original-prefix" in sys.argv else None
sha = lambda b: hashlib.sha256(b).hexdigest()
fails = []
def bad(m): fails.append(m)
def read(rel):
    with open(os.path.join(root, rel), "rb") as fh: return fh.read()

doc = open(doc_path, "rb").read().decode()
man = read("REDACTION_MANIFEST.txt")
if sha(man) not in doc: bad("REDACTION_MANIFEST.txt does not match the digest anchored in the results document")
rows, omitted, unmanifested = {}, {}, []
for line in man.decode().splitlines():
    if line.startswith("# UNMANIFESTED_PREFIXES:"): unmanifested = line.split(":", 1)[1].split(); continue
    if line.startswith("OMITTED | "):
        q = [x.strip() for x in line.split(" | ")]
        if len(q) != 4 or not re.fullmatch(r"[0-9a-f]{64}", q[2]) or q[1] in omitted: bad(f"malformed or duplicate OMITTED declaration: {line[:80]}")
        else: omitted[q[1]] = (q[2], q[3])
        continue
    if not line.strip() or line.startswith("#"): continue
    p = [x.strip() for x in line.split(" | ")]
    if len(p) != 4 or not re.fullmatch(r"[0-9a-f]{64}", p[1]) or not re.fullmatch(r"[0-9a-f]{64}", p[2]) or not p[3].isdigit(): bad(f"malformed manifest row: {line[:80]}"); continue
    if p[0] in rows: bad(f"duplicate manifest row {p[0]}")
    rows[p[0]] = (p[1], p[2], int(p[3]))

on_disk = {os.path.relpath(os.path.join(r, f), root) for r, _, fs in os.walk(root) for f in fs} - {"REDACTION_MANIFEST.txt"}
for p in sorted(on_disk - set(rows)): bad(f"undeclared evidence file added: {p}")
for p in sorted(set(rows) - on_disk): bad(f"declared evidence file missing: {p}")
for p, (orig, committed, n) in rows.items():
    if p not in on_disk: continue
    data = read(p)
    if sha(data) != committed: bad(f"committed file differs from its recorded committed digest: {p}")
    if (n == 0) != (orig == committed): bad(f"redaction declaration inconsistent for {p} (replacements={n}, original {'==' if orig == committed else '!='} committed)")
    if n and data.count(b"$HOME") < n: bad(f"{p}: fewer $HOME markers than the declared {n} replacements")
n1 = len(fails)

# ---- original manifests vs recorded original digests (strict)
checked = {"unredacted equal to sealed original": 0, "redacted tied to sealed original": 0}; referenced, omitted_hit = set(), set(); manifest_files = []
def sums(rel, base):
    manifest_files.append(rel); seen = set(); lines = read(rel).decode().split("\n")
    if lines and lines[-1] == "": lines.pop()
    for n, line in enumerate(lines, 1):
        m = re.fullmatch(r"([0-9a-f]{64})  (\S(?:.*\S)?)", line)
        if not m or m.group(2).startswith("/") or ".." in m.group(2).split("/"): bad(f"malformed checksum line {n} in {rel}: {line[:60]!r}"); continue
        digest, name = m.group(1), m.group(2); path = os.path.normpath(os.path.join(base, name))
        if path in seen: bad(f"duplicate checksum entry for {path} in {rel}"); continue
        seen.add(path)
        if path in rows:
            referenced.add(path)
            if rows[path][0] != digest: bad(f"original digest for {path} differs from the one recorded by {rel}")
            else: checked["redacted tied to sealed original" if rows[path][2] else "unredacted equal to sealed original"] += 1
        elif path in omitted:
            omitted_hit.add(path)
            if omitted[path][0] != digest: bad(f"{rel} records a different digest for the declared-omitted {path}")
        else: bad(f"unexplained reference in {rel}: {path} is neither committed nor declared omitted")
sums("job/JOB_SHA256SUMS", "job")
for d in ("job/results_full", "job/results_smoke"): sums(f"{d}/MANIFEST.sha256", d)
sums("execution/EXEC_SHA256SUMS", "execution")
for rec in sorted(p for p in rows if p.endswith("/PREFLIGHT_SHA256SUMS")): sums(rec, os.path.dirname(rec))
sums("operator/OPERATOR_CONFIRMATION_5b.sha256", "operator")
for p in sorted(omitted):
    if p not in omitted_hit: bad(f"declared omission {p} is not referenced by any original manifest")
    if os.path.exists(os.path.join(root, p)): bad(f"declared-omitted file is present: {p}")
selfs = set(manifest_files) - {m for m in manifest_files if m.endswith("MANIFEST.sha256")} | {"job/JOB_SHA256SUMS"}
for p in sorted(rows):
    if p in referenced or p in selfs or p.startswith(tuple(unmanifested)): continue
    bad(f"committed file not listed by any original manifest and not declared UNMANIFESTED: {p}")
# the omitted fixture corpus is pinned by the two sealed pin lists: exactly 36 + 1 well-formed, distinct entries
pins = []
for rel in ("job/fixture_hashes.sha256", "job/generated_artifacts.sha256"):
    for n, line in enumerate(read(rel).decode().splitlines(), 1):
        m = re.fullmatch(r"([0-9a-f]{64})  (\S+)", line)
        if not m: bad(f"malformed pin line {n} in {rel}")
        else: pins.append(m.group(2))
if len(pins) != 37 or len(set(pins)) != 37: bad(f"omitted fixture corpus is not pinned by exactly 37 distinct entries ({len(pins)} entries, {len(set(pins))} distinct)")
layer2 = "CONSISTENT" if len(fails) == n1 else "INCONSISTENT"; n2 = len(fails)

reversal = "NOT_EVALUATED (no --original-prefix supplied; the prefix is deliberately not committed)"
if prefix is not None:
    wrong = [p for p, (o, c, n) in rows.items() if n and sha(read(p).replace(b"$HOME", prefix.encode())) != o]
    for p in wrong: bad(f"reversing the declared substitution does not reproduce the recorded original digest: {p}")
    reversal = f"VERIFIED for {sum(1 for r in rows.values() if r[2])} redacted files" if not wrong else "FAILED"

print(f"files: {len(on_disk)} committed evidence files; {len(rows)} manifest rows ({sum(1 for r in rows.values() if r[2])} redacted)")
print(f"original-manifest cross-references: {checked}; declared omissions referenced: {len(omitted_hit)}/{len(omitted)} (package archive copies) plus the pinned fixture corpus ({len(pins)} files); unexplained references: 0 if none listed below")
print(f"COMMITTED_EVIDENCE_INTEGRITY: {'VERIFIED' if n1 == 0 else 'FAILED'}")
print(f"ORIGINAL_MANIFEST_ENTRIES_VS_RECORDED_ORIGINAL_DIGESTS: {layer2}")
print("ORIGINAL_JOB_INTEGRITY: NOT_REVERIFIED_FROM_THIS_CHECKOUT")
print(f"REDACTION_TRANSFORM_REVERSAL: {reversal}")
for m in fails[:30]: print("  -", m)
print("EVIDENCE_VERIFICATION:", "PASS" if not fails else "FAIL")
sys.exit(1 if fails else 0)
