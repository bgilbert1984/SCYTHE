import hashlib, io, json, os, shutil, subprocess, sys, tarfile
PY, FULL, REPO = sys.argv[1:4]; base = "out1/scythe_r4_pkg_f2b92f6fbe07.tar.gz"
sha = lambda b: hashlib.sha256(b).hexdigest()
def load():
    with tarfile.open(base) as tf: return {m.name.split("/", 1)[1]: tf.extractfile(m).read() for m in tf.getmembers() if m.isfile()}
def pack(files, name, refresh_manifests):
    if refresh_manifests:   # attacker/mistake that also "fixes" the manifests so only content-level checks can object
        inv = json.loads(files["PACKAGE_INVENTORY.json"]); inv["files"] = {p: {**m, "sha256": sha(files[p]), "size": len(files[p])} for p, m in inv["files"].items() if p in files}
        for p in list(files):
            if p not in ("SHA256SUMS", "PACKAGE_INVENTORY.json") and p not in inv["files"]:
                inv["files"][p] = {"sha256": sha(files[p]), "size": len(files[p]), "origin": "git:added"}
        files["PACKAGE_INVENTORY.json"] = (json.dumps(inv, indent=1, sort_keys=True) + "\n").encode()
        files["SHA256SUMS"] = "".join(f"{sha(b)}  {p}\n" for p, b in sorted(files.items()) if p != "SHA256SUMS").encode()
    out = f"neg/{name}.tar.gz"; raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        for p in sorted(files):
            ti = tarfile.TarInfo(f"scythe_r4_pkg/{p}"); ti.size = len(files[p]); ti.mode = 0o644; tf.addfile(ti, io.BytesIO(files[p]))
    import gzip
    with open(out, "wb") as fh, gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0) as gz: gz.write(raw.getvalue())
    open(out + ".sha256", "w").write(f"{sha(open(out,'rb').read())}  {os.path.basename(out)}\n")
    return out
cases = {}
f = load(); f["rf_spur_retention_estimator.py"] += b"\n# tampered\n"; cases["A_byte_tampered_manifests_untouched"] = pack(f, "A", False)
f = load(); f["rf_spur_retention_estimator.py"] += b"\n# tampered\n"; cases["B_byte_tampered_manifests_refreshed"] = pack(f, "B", True)
f = load(); del f["rf_promotion_geometry.py"]; cases["C_closure_module_removed_manifests_refreshed"] = pack(f, "C", True)
f = load(); f["extra_notes.txt"] = b"scratch\n"; cases["D_undeclared_file_manifests_refreshed"] = pack(f, "D", True)
f = load(); f["tools/r4_job/CUSTODY.md"] += b"\nsee /home/someone/private/data\n"; cases["E_absolute_path_manifests_refreshed"] = pack(f, "E", True)
f = load(); f["__pycache__/x.cpython-312.pyc"] = b"\0\0"; cases["F_bytecode_cache_manifests_refreshed"] = pack(f, "F", True)
f = load(); f["tools/r4_job/CUSTODY.md"] += b"\npassword = 'hunter2hunter2'\n"; cases["G_credential_pattern_manifests_refreshed"] = pack(f, "G", True)
rows = []
for name, arc in cases.items():
    r = subprocess.run([PY, "verify_package.py", REPO, FULL, arc, f"neg/x_{name}", "--python", PY], capture_output=True, text=True)
    failed = [l.split("  [")[0] for l in r.stdout.splitlines() if l.startswith("FAIL")]
    rows.append((name, r.returncode, failed))
for name, rc, failed in rows:
    print(f"{name:55s} exit={rc} -> {'REJECTED' if rc else 'ACCEPTED (BAD)'}   failed checks: {[x[:34] for x in failed]}")
sys.exit(1 if any(rc == 0 for _, rc, _ in rows) else 0)
