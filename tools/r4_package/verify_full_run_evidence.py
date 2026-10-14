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
rows = {}
for line in man.decode().splitlines():
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

# ---- original manifests vs recorded original digests
checked = {"unredacted equal to sealed original": 0, "redacted tied to sealed original": 0}; absent = []; listed_by_job = set()
def sums(rel, base):
    for line in read(rel).decode().splitlines():
        m = re.fullmatch(r"([0-9a-f]{64})  (\S.*)", line.strip())
        if not m: continue
        path = os.path.normpath(os.path.join(base, m.group(2).strip())); digest = m.group(1)
        if path not in rows: absent.append(path); continue
        orig = rows[path][0]
        if orig != digest: bad(f"original digest for {path} differs from the one recorded by {rel}")
        else: checked["redacted tied to sealed original" if rows[path][2] else "unredacted equal to sealed original"] += 1
        if rel == "job/JOB_SHA256SUMS": listed_by_job.add(path)
sums("job/JOB_SHA256SUMS", "job")
for p in sorted(rows):
    if p.startswith("job/") and p != "job/JOB_SHA256SUMS" and p not in listed_by_job: bad(f"job file not listed by the job manifest: {p}")
for d in ("job/results_full", "job/results_smoke"): sums(f"{d}/MANIFEST.sha256", d)
sums("execution/EXEC_SHA256SUMS", "execution")
for rec in sorted(p for p in rows if p.endswith("/PREFLIGHT_SHA256SUMS")): sums(rec, os.path.dirname(rec))
sums("operator/OPERATOR_CONFIRMATION_5b.sha256", "operator")
layer2 = "CONSISTENT" if len(fails) == n1 else "INCONSISTENT"; n2 = len(fails)

reversal = "NOT_EVALUATED (no --original-prefix supplied; the prefix is deliberately not committed)"
if prefix is not None:
    wrong = [p for p, (o, c, n) in rows.items() if n and sha(read(p).replace(b"$HOME", prefix.encode())) != o]
    for p in wrong: bad(f"reversing the declared substitution does not reproduce the recorded original digest: {p}")
    reversal = f"VERIFIED for {sum(1 for r in rows.values() if r[2])} redacted files" if not wrong else "FAILED"

print(f"files: {len(on_disk)} committed evidence files; {len(rows)} manifest rows ({sum(1 for r in rows.values() if r[2])} redacted)")
print(f"original-manifest cross-references: {checked}; entries for files not committed (fixture corpus, archive copies): {len(set(absent))}")
print(f"COMMITTED_EVIDENCE_INTEGRITY: {'VERIFIED' if n1 == 0 else 'FAILED'}")
print(f"ORIGINAL_MANIFEST_ENTRIES_VS_RECORDED_ORIGINAL_DIGESTS: {layer2}")
print("ORIGINAL_JOB_INTEGRITY: NOT_REVERIFIED_FROM_THIS_CHECKOUT")
print(f"REDACTION_TRANSFORM_REVERSAL: {reversal}")
for m in fails[:30]: print("  -", m)
print("EVIDENCE_VERIFICATION:", "PASS" if not fails else "FAIL")
sys.exit(1 if fails else 0)
