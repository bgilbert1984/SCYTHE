#!/usr/bin/env python3
"""Build the frozen r4 package from a CLEAN git archive of one commit. Stdlib only. Never reads the working tree.

    build_package.py REPO FULL_SHA OUT_DIR

Writes OUT_DIR/scythe_r4_pkg_<sha12>.tar.gz (deterministic), OUT_DIR/PACKAGE_RECORD.txt, and keeps the staging tree for verification.
Every packaged file comes from `git archive FULL_SHA` (or, for the fixture generator/spec, from a member of the equivalence-package tarball
that is itself a blob at FULL_SHA). The package carries SHA256SUMS (inventory + content digests) and PACKAGE_INVENTORY.json (provenance per file).
"""
import gzip, hashlib, io, json, os, shutil, subprocess, sys, tarfile

repo, full, out = sys.argv[1], sys.argv[2], sys.argv[3]
assert len(full) == 40, "use the complete commit SHA"
EV = "docs/evidence/dedirock-equivalence-2026-10-12"
PKG_TAR = f"{EV}/dedirock_equiv_pkg.tar.gz"
FIXTURE_MEMBERS = {"dedirock_equiv_pkg/generate_holdout.py": "fixtures/generate_holdout.py",
                   "dedirock_equiv_pkg/synth_521_holdout_frozen.json": "fixtures/synth_521_holdout_frozen.json",
                   "dedirock_equiv_pkg/synth521_holdout_index.json": "fixtures/synth521_holdout_index.json"}
FIXTURE_HASH_LIST = (f"{EV}/fixture_hashes.sha256", "fixtures/fixture_hashes.sha256")
TOOLS = ["tools/rf_metamorphic_harness.py", "tools/r4_job/run_job.sh", "tools/r4_job/seal_job.py", "tools/r4_job/gate_results.py",
         "tools/r4_job/CUSTODY.md", "tools/r4_job/PACKAGE_FILES.txt"]
DOCS = ["docs/research/metamorphic_r4_span_boundary_design.md", "docs/research/metamorphic_r4_implementation_notes.md"]


def git(*args, binary=False):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, check=True)
    return r.stdout if binary else r.stdout.decode()


def blob(path):
    return git("show", f"{full}:{path}", binary=True)


sha = lambda b: hashlib.sha256(b).hexdigest()
closure = [l.strip() for l in blob("tools/r4_job/PACKAGE_FILES.txt").decode().splitlines() if l.strip() and not l.startswith("#")]
files, origin = {}, {}
for path in closure + TOOLS + DOCS:                      # source closure + runner + custody + protocol documents
    files[path] = blob(path); origin[path] = f"git:{path}@{full}"
# the fixture generator/spec: members of the equivalence-package tarball blob at the same commit
with tarfile.open(fileobj=io.BytesIO(blob(PKG_TAR)), mode="r:gz") as tf:
    for member, dest in FIXTURE_MEMBERS.items():
        files[dest] = tf.extractfile(member).read(); origin[dest] = f"git:{PKG_TAR}:{member}@{full}"
src, dest = FIXTURE_HASH_LIST
files[dest] = blob(src); origin[dest] = f"git:{src}@{full}"

fixture_digest = sha(files["fixtures/fixture_hashes.sha256"])
fixture_count = len([l for l in files["fixtures/fixture_hashes.sha256"].decode().splitlines() if l.strip()])
identity = (f"EXPERIMENT: SCYTHE_METAMORPHIC_RF_R4\nSTAGE: FROZEN_PACKAGE_PREPARATION\nSOURCE_COMMIT: {full}\n"
            f"FIXTURE_HASH_MANIFEST_SHA256: {fixture_digest}\nFIXTURE_COUNT: {fixture_count}\n"
            "EXECUTION_AUTHORIZED: NO\nDEDIROCK_TRANSFER_AUTHORIZED: NO\n"
            "This archive establishes artifact integrity and the completeness of the source closure. It does not establish that the r4 experiment passes.\n")
files["PACKAGE_IDENTITY.txt"] = identity.encode(); origin["PACKAGE_IDENTITY.txt"] = "generated"
inventory = {"source_commit": full, "files": {p: {"sha256": sha(b), "size": len(b), "origin": origin[p]} for p, b in sorted(files.items())}}
files["PACKAGE_INVENTORY.json"] = (json.dumps(inventory, indent=1, sort_keys=True) + "\n").encode()
# SHA256SUMS lists every other file with its content digest (it cannot list itself)
files["SHA256SUMS"] = "".join(f"{sha(b)}  {p}\n" for p, b in sorted(files.items()) if p != "SHA256SUMS").encode()

mtime = int(git("log", "-1", "--format=%ct", full).strip())
os.makedirs(out, exist_ok=True)
name = f"scythe_r4_pkg_{full[:12]}.tar.gz"; target = os.path.join(out, name)
raw = io.BytesIO()
with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tf:
    for path in sorted(files):
        ti = tarfile.TarInfo(f"scythe_r4_pkg/{path}"); ti.size = len(files[path]); ti.mtime = mtime; ti.uid = ti.gid = 0; ti.uname = ti.gname = ""
        ti.mode = 0o755 if path.endswith(".sh") else 0o644
        tf.addfile(ti, io.BytesIO(files[path]))
with open(target, "wb") as fh:
    with gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0) as gz:
        gz.write(raw.getvalue())
digest = sha(open(target, "rb").read())
with open(target + ".sha256", "w") as fh:
    fh.write(f"{digest}  {name}\n")
json.dump({"archive": name, "archive_sha256": digest, "files": len(files), "fixture_hash_manifest_sha256": fixture_digest,
           "fixture_count": fixture_count, "source_commit": full}, open(os.path.join(out, "build_summary.json"), "w"), indent=1)
print(name, digest, len(files), fixture_digest)
