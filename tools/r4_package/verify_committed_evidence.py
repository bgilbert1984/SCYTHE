#!/usr/bin/env python3
"""Redaction-aware verification of the committed r4 packaging/Gate 3 evidence (stdlib only; shares no code with the package tools).

    verify_committed_evidence.py EVIDENCE_ROOT PROVENANCE_DOC [--original-prefix PREFIX]

EVIDENCE_ROOT is the `docs/evidence` directory of a checkout or a pristine `git archive`. Two layers are kept apart, as in the r3 evidence verification:

  COMMITTED_EVIDENCE_INTEGRITY  every committed evidence file matches the committed digest recorded in REDACTION_MANIFEST.txt (or, for the few verbatim tool copies,
                                the digest published in the provenance document); no evidence file is added or missing; the manifest itself matches the digest anchored in the provenance document;
                                every row's redaction declaration is internally consistent (0 replacements <=> original == committed).
  ORIGINAL_MANIFEST_ENTRIES_VS_RECORDED_ORIGINAL_DIGESTS
                                every original digest in the redaction manifest equals the digest the retained ORIGINAL manifests (job manifests, FINAL_SHA256SUMS,
                                GATE3_R41_SHA256SUMS, record digest lines) recorded for the same file. Rows no original manifest lists are reported as declared-only.
  ORIGINAL_JOB_INTEGRITY        the original job directories and archives are not in a checkout: always NOT_REVERIFIED_FROM_THIS_CHECKOUT.
  REDACTION_TRANSFORM_REVERSAL  only with --original-prefix: reversing the declared substitution ($HOME -> PREFIX) must reproduce the recorded ORIGINAL digest of every redacted file.
                                Otherwise NOT_EVALUATED (the prefix is deliberately not committed).
Nothing is rewritten. Exit 0 iff no check failed; an unhandled exception exits 2 with VERIFIER_CRASH.
"""
import hashlib, os, re, sys, traceback


def _crash(kind, value, tb):
    traceback.print_exception(kind, value, tb); print("VERIFIER_CRASH:", kind.__name__, value); sys.stdout.flush(); os._exit(2)


sys.excepthook = _crash
root, doc_path = sys.argv[1], sys.argv[2]
prefix = sys.argv[sys.argv.index("--original-prefix") + 1] if "--original-prefix" in sys.argv else None
PROV = "r4_1_packaging_provenance_2026-10-13"; G3 = "r4_1_gate3_2026-10-13"
sha = lambda b: hashlib.sha256(b).hexdigest()
fails = []
def bad(msg): fails.append(msg)
def read(rel):
    with open(os.path.join(root, rel), "rb") as fh: return fh.read()

with open(doc_path, "rb") as fh: doc = fh.read().decode()
man_bytes = read(f"{PROV}/REDACTION_MANIFEST.txt")
if sha(man_bytes) not in doc: bad("REDACTION_MANIFEST.txt does not match the digest anchored in the provenance document")

rows = {}
for line in man_bytes.decode().splitlines():
    if not line.strip() or line.startswith("#"): continue
    parts = [p.strip() for p in line.split("|")]
    if len(parts) != 4 or not re.fullmatch(r"[0-9a-f]{64}", parts[1]) or not re.fullmatch(r"[0-9a-f]{64}", parts[2]) or not parts[3].isdigit(): bad(f"malformed manifest row: {line[:80]}"); continue
    if parts[0] in rows: bad(f"duplicate manifest row {parts[0]}")
    rows[parts[0]] = (parts[1], parts[2], int(parts[3]))

# ---- layer 1: committed evidence
on_disk = set()
for d in (PROV, G3):
    for r, _, fs in os.walk(os.path.join(root, d)):
        for f in fs: on_disk.add(os.path.relpath(os.path.join(r, f), root))
verbatim = {p for p in on_disk if p.startswith(f"{PROV}/external_tools/")}                    # verbatim tool copies: digests are published in the provenance document
declared = set(rows) | verbatim | {f"{PROV}/REDACTION_MANIFEST.txt"}
for p in sorted(on_disk - declared): bad(f"undeclared evidence file added: {p}")
for p in sorted(declared - on_disk): bad(f"declared evidence file missing: {p}")
for p, (orig, committed, n) in rows.items():
    if p in on_disk and sha(read(p)) != committed: bad(f"committed file differs from its recorded committed digest: {p}")
    if (n == 0) != (orig == committed): bad(f"redaction declaration inconsistent for {p} (replacements={n}, original {'==' if orig == committed else '!='} committed)")
    if n and p in on_disk and read(p).decode().count("$HOME") < n: bad(f"{p}: fewer $HOME markers than the declared {n} replacements")
for p in sorted(verbatim):
    if sha(read(p)) not in doc: bad(f"verbatim tool copy not matching a digest published in the provenance document: {p}")
n1 = len(fails)

# ---- layer 2: manifest rows vs the retained original manifests
refs = {}                                   # committed path -> [(source, digest)]
def add(path, digest, source): refs.setdefault(path, []).append((source, digest))
def parse_sums(rel, base, source):
    for line in read(rel).decode().splitlines():
        m = re.fullmatch(r"([0-9a-f]{64})  (\S.*)", line.strip())
        if m: add(os.path.normpath(f"{base}/{m.group(2).strip()}"), m.group(1), source)
parse_sums(f"{G3}/GATE3_R41_SHA256SUMS", G3, "GATE3_R41_SHA256SUMS")
for j in ("job_ok", "job_fail"): parse_sums(f"{G3}/{j}/JOB_SHA256SUMS", f"{G3}/{j}", f"{j}/JOB_SHA256SUMS")
parse_sums(f"{PROV}/package_2b2e3251/FINAL_SHA256SUMS", f"{PROV}/package_2b2e3251", "2b2e3251 FINAL_SHA256SUMS")
parse_sums(f"{PROV}/external_tools_r4_for_a7fd10b1/FINAL_SHA256SUMS", f"{PROV}/external_tools_r4_for_a7fd10b1", "a7fd10b1 FINAL_SHA256SUMS")
parse_sums(f"{G3}/GATE3_R41_RECORD.txt", G3, "GATE3_R41_RECORD tool-digest lines")
declared_only = []
for p, (orig, committed, n) in sorted(rows.items()):
    mine = refs.get(p, [])
    if not mine: declared_only.append(p)
    for source, digest in mine:
        if digest != orig: bad(f"original digest for {p} in the redaction manifest differs from the one recorded by {source}")
layer2 = "CONSISTENT" if len(fails) == n1 else "INCONSISTENT"
n2 = len(fails)

# ---- optional: reverse the declared substitution
reversal = "NOT_EVALUATED (no --original-prefix supplied; the prefix is deliberately not committed)"
if prefix is not None:
    wrong = [p for p, (orig, committed, n) in rows.items() if n and sha(read(p).decode().replace("$HOME", prefix).encode()) != orig]
    for p in wrong: bad(f"reversing the declared substitution does not reproduce the recorded original digest: {p}")
    reversal = f"VERIFIED for {sum(1 for r in rows.values() if r[2])} redacted files" if not wrong else "FAILED"

print(f"files: {len(on_disk)} committed evidence files; {len(rows)} manifest rows; {len(verbatim)} verbatim tool copies; {len(declared_only)} rows declared-only (no retained original manifest lists them)")
print(f"COMMITTED_EVIDENCE_INTEGRITY: {'VERIFIED' if n1 == 0 else 'FAILED'}")
print(f"ORIGINAL_MANIFEST_ENTRIES_VS_RECORDED_ORIGINAL_DIGESTS: {layer2}")
print("ORIGINAL_JOB_INTEGRITY: NOT_REVERIFIED_FROM_THIS_CHECKOUT")
print(f"REDACTION_TRANSFORM_REVERSAL: {reversal}")
for p in declared_only: print("  declared-only (no retained original manifest lists it):", p)
for m in fails[:30]: print("  -", m)
print("EVIDENCE_VERIFICATION:", "PASS" if not fails else "FAIL")
sys.exit(1 if fails else 0)
