#!/usr/bin/env python3
"""Experiment E: archive raw captures with an immutable manifest, then verify.

Stdlib only, so it can be fed to python3 on scythe-1 over ssh stdin.

  archive_evidence.py manifest SRC_DIR OUT_MANIFEST [--script-repo DIR]
  archive_evidence.py copy     SRC_DIR DEST_DIR            # copies, re-hashes the COPY, writes MANIFEST there
  archive_evidence.py verify   DIR_OR_MANIFEST [--root DIR]

What the manifest records (and what it cannot):
  recorded : sha256, size, mtime of each file; sample format inferred from the Stage 0B doc
             (u8 I/Q interleaved, 2.048 MS/s, rtl_tcp cmd 0x09=2 Q-branch direct sampling);
             git commit of the processing scripts; host name; creation time.
  NOT recorded here (unknowable from the files): the antenna actually connected per file,
             tuner gain, center frequency per file. These are filled from the results doc
             under "declared" and are marked source="EXPERIMENT_E_STAGE0B_RESULTS.md", not "measured".
Provenance of every entry is "recorded" (real receiver capture). Nothing here is synthetic.
"""
import sys, os, json, hashlib, time, socket, subprocess, shutil

DECLARED = {  # from docs/EXPERIMENT_E_STAGE0B_RESULTS.md; human-declared, not machine-verified
    "source": "docs/EXPERIMENT_E_STAGE0B_RESULTS.md",
    "device": "Nooelec SMArt XTR v5, SN 71874552, E4000",
    "sample_format": "u8 interleaved I,Q",
    "sample_rate_hz": 2048000,
    "direct_sampling": "Q-branch (rtl_tcp cmd 0x09 = 2)",
    "gain": "rtl_tcp default",
    "files_per_results_doc": ["q1", "q2", "am", "hf7", "hf5", "am584"],
    "note": "per-file center frequency and antenna are in the results doc; not derivable from bytes",
}


def sha256(path, chunk=1 << 22):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b: break
            h.update(b)
    return h.hexdigest()


def git_rev(d):
    try:
        r = subprocess.run(["git", "-C", d, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "-C", d, "status", "--porcelain", "--", "scripts/experiment_e"],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return {"commit": r.stdout.strip() or None, "scripts_dirty": bool(dirty)}
    except Exception as e:
        return {"commit": None, "error": str(e)}


def build_manifest(src, script_repo=None):
    files = []
    for name in sorted(os.listdir(src)):
        p = os.path.join(src, name)
        if not os.path.isfile(p): continue
        st = os.stat(p)
        files.append({"name": name, "bytes": st.st_size, "sha256": sha256(p),
                      "mtime_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
                      "provenance": "recorded"})
    return {"schema": "exp_e_evidence_manifest/1", "provenance": "recorded",
            "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": socket.gethostname(), "source_dir": os.path.abspath(src),
            "processing_scripts": git_rev(script_repo) if script_repo else None,
            "declared": DECLARED, "files": files}


def verify(manifest, root):
    bad = 0
    for f in manifest["files"]:
        p = os.path.join(root, f["name"])
        if not os.path.isfile(p): print("MISSING", f["name"]); bad += 1; continue
        if os.path.getsize(p) != f["bytes"]: print("SIZE", f["name"]); bad += 1; continue
        if sha256(p) != f["sha256"]: print("HASH", f["name"]); bad += 1; continue
        print("ok", f["name"], f["bytes"])
    print("VERIFIED" if bad == 0 else f"FAILED ({bad})")
    return bad == 0


def main(a):
    if len(a) < 2: sys.exit(__doc__)
    cmd = a[0]
    repo = a[a.index("--script-repo") + 1] if "--script-repo" in a else None
    if cmd == "manifest":
        m = build_manifest(a[1], repo); json.dump(m, open(a[2], "w"), indent=1); print("wrote", a[2], len(m["files"]), "files")
    elif cmd == "copy":
        src, dst = a[1], a[2]
        if os.path.exists(dst) and os.listdir(dst): sys.exit(f"refusing: {dst} is not empty")
        os.makedirs(dst, exist_ok=True)
        m = build_manifest(src, repo)                        # hash the SOURCE first
        for f in m["files"]:
            shutil.copy2(os.path.join(src, f["name"]), os.path.join(dst, f["name"]))
        ok = verify(m, dst)                                  # then hash the COPY against it
        if not ok: sys.exit("copy verification failed; leave the source in place")
        json.dump(m, open(os.path.join(dst, "MANIFEST.json"), "w"), indent=1)
        os.chmod(os.path.join(dst, "MANIFEST.json"), 0o444)
        for f in m["files"]: os.chmod(os.path.join(dst, f["name"]), 0o444)
        print("archived to", dst)
    elif cmd == "verify":
        t = a[1]; mp = t if t.endswith(".json") else os.path.join(t, "MANIFEST.json")
        root = a[a.index("--root") + 1] if "--root" in a else os.path.dirname(mp)
        sys.exit(0 if verify(json.load(open(mp)), root) else 1)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
