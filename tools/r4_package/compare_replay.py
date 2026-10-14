#!/usr/bin/env python3
"""Cross-host replay comparator for the frozen r4.1 package (Gate B'): implements docs/research/metamorphic_r4_cross_host_replay_protocol.md sections 3-5. Stdlib only.

    compare_replay.py REF_JOB REPLAY_JOB --ref-preflight JSON --replay-preflight JSON --ref-disposition TXT --replay-disposition TXT [--report REPORT.json]

Result (precedence order): COMPARISON_REFUSED_PROVENANCE_MISMATCH > INCOMPARABLE > SEMANTIC_DIVERGENCE > NUMERICAL_DIVERGENCE > EQUIVALENT_WITHIN_DECLARED_TOLERANCE > EQUIVALENT_BITWISE.
A crash exits 2 and prints COMPARATOR_FAILURE: it is never equivalence and never a divergence. Exit 0 iff the result is one of the two EQUIVALENT_* values.

Reading of the protocol where its wording admits two (recorded here, not tuned to data): an int-vs-float difference at a leaf is a class-E value/type difference (SEMANTIC, section 4.2 and 8);
a difference in the KIND of node (object / array / scalar / null), a key set, a list length or the file set is STRUCTURAL (INCOMPARABLE, section 4.4 and 5.2).
A deviation residual (a float leaf whose key contains "dev") that lies on opposite sides of any verdict threshold (1e-9, 1e-6 and the positive numbers in the declared noise / one_repeat lists) is a class-E difference
even when the two values agree within tolerance (section 4.3).
"""
import hashlib, json, math, os, re, sys, traceback

A, RR, CAP = 1e-9, 1e-12, 1e-8                      # protocol section 4.3 (frozen before any second-host result)
FROZEN = {"package_sha256": "2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a", "source_commit": "8d34de9ac0b1f4758cda3412b364582b4271ffe1",
          "fixture_list_sha256": "d42892ec15932b1883e8c59391625e1cdcd7d2171f588a275521b2b0d0b63e41", "index_pin_text": "f52adb1974dce642bc62bf748add6733036db1cbc7bcba482691a221ad88eca3  index.json\n",
          "harness_sha256": "1ac4fe34260f6ba63056ed7dbf13f604d9b3f53f8ac77908f4c1ad052eac6687"}
RESULT_DIRS = ("results_full", "results_smoke")
E_FLOAT_CONSTANTS = (("layerA.json", re.compile(r"^usable_hz$")), ("layerA.json", re.compile(r"^geometries/[^/]+/bin_spacing_hz$")), ("layerBprime.json", re.compile(r"^synthetic_spec/")))
X_PATHS = {("run_header.json", "argv"), ("run_header.json", "numpy")}                 # environmental: recorded, never compared
sha = lambda b: hashlib.sha256(b).hexdigest()


def _crash(kind, value, tb):
    traceback.print_exception(kind, value, tb); print("COMPARATOR_FAILURE:", kind.__name__, value); sys.stdout.flush(); os._exit(2)


sys.excepthook = _crash


def arg(name):
    return sys.argv[sys.argv.index(name) + 1]


def read(path):
    with open(path, "rb") as fh: return fh.read()


# ---------------------------------------------------------------- provenance
def verify_sealed(job, label, why):
    man = os.path.join(job, "JOB_SHA256SUMS")
    if not os.path.isfile(man): why.append(f"{label}: no JOB_SHA256SUMS (not sealed)"); return
    listed = {}
    for n, line in enumerate(read(man).decode().split("\n"), 1):
        if not line: continue
        m = re.fullmatch(r"([0-9a-f]{64})  (\S.*)", line)
        if not m or m.group(2).startswith("/") or ".." in m.group(2).split("/") or m.group(2) in listed: why.append(f"{label}: malformed or duplicate manifest line {n}"); continue
        listed[m.group(2)] = m.group(1)
    for rel, d in listed.items():
        p = os.path.join(job, rel)
        if not os.path.isfile(p): why.append(f"{label}: listed file missing: {rel}")
        elif sha(read(p)) != d: why.append(f"{label}: digest mismatch: {rel}")
    for root, dirs, fs in os.walk(job):
        if os.path.relpath(root, job) == ".": dirs[:] = [d for d in dirs if d != "holdout"]
        for f in fs:
            rel = os.path.relpath(os.path.join(root, f), job)
            if rel != "JOB_SHA256SUMS" and rel not in listed: why.append(f"{label}: unlisted file: {rel}")
    st = [l for l in read(os.path.join(job, "STATUS.txt")).decode().splitlines() if l.strip()] if os.path.isfile(os.path.join(job, "STATUS.txt")) else []
    if not (st and re.match(r"\S+ DONE\b", st[-1])): why.append(f"{label}: terminal record is not DONE")


def provenance(ref, rep, a):
    why = []
    for job, label, pre, disp in ((ref, "reference", a["--ref-preflight"], a["--ref-disposition"]), (rep, "replay", a["--replay-preflight"], a["--replay-disposition"])):
        verify_sealed(job, label, why)
        if "JOB_ACCEPTED: YES" not in read(disp).decode().split("--- sealer")[0]: why.append(f"{label}: disposition does not state JOB_ACCEPTED: YES")
        rec = json.loads(read(pre))
        if rec.get("package_sha256") != FROZEN["package_sha256"]: why.append(f"{label}: package digest differs from the frozen one")
        if rec.get("declaration", {}).get("source_commit") != FROZEN["source_commit"]: why.append(f"{label}: source commit differs from the frozen one")
        fl = os.path.join(job, "fixture_hashes.sha256"); gp = os.path.join(job, "generated_artifacts.sha256")
        if not os.path.isfile(fl) or sha(read(fl)) != FROZEN["fixture_list_sha256"]: why.append(f"{label}: fixture hash list differs from the frozen one")
        if not os.path.isfile(gp) or read(gp).decode().split("  ")[0:1] != [FROZEN["index_pin_text"].split("  ")[0]]: why.append(f"{label}: generated index pin differs from the frozen one")
        for d in RESULT_DIRS:
            h = os.path.join(job, d, "run_header.json")
            if not os.path.isfile(h): why.append(f"{label}: {d}/run_header.json missing"); continue
            hd = json.loads(read(h))
            if hd.get("harness_sha256") != FROZEN["harness_sha256"]: why.append(f"{label}: {d} harness digest differs from the frozen one")
            if any(v != "1" for v in hd.get("thread_env", {}).values()) or len(hd.get("thread_env", {})) != 4: why.append(f"{label}: {d} thread limits are not all 1")
    for d in RESULT_DIRS:
        try: h1, h2 = (json.loads(read(os.path.join(j, d, "run_header.json"))) for j in (ref, rep))
        except OSError: continue
        for k in ("declared", "production_sha256", "harness_sha256", "sequencer_revision", "revision", "fixtures", "thread_env"):
            if h1.get(k) != h2.get(k): why.append(f"{d}/run_header.json: frozen item '{k}' differs between the jobs")
    for f in ("fixture_hashes.sha256", "generated_artifacts.sha256"):
        if read(os.path.join(ref, f)) != read(os.path.join(rep, f)): why.append(f"{f} differs between the jobs")
    return why


# ---------------------------------------------------------------- leaf comparison
def kind(v):
    return "null" if v is None else "object" if isinstance(v, dict) else "array" if isinstance(v, list) else "scalar"


def generic_path(path):
    return "/".join("*" if (p.isdigit() or p.endswith(".npy")) else p for p in path.split("/"))


class Cmp:
    def __init__(self, thresholds):
        self.counts = {"IDENTICAL": 0, "WITHIN_TOLERANCE": 0, "NUMERICAL_DIFFERENCE": 0, "CATEGORICAL_DIFFERENCE": 0, "STRUCTURAL_DIFFERENCE": 0}
        self.stats, self.diffs, self.env, self.thr = {}, [], {}, thresholds

    def note(self, outcome, where, detail):
        self.counts[outcome] += 1
        if outcome != "IDENTICAL" and len(self.diffs) < 300: self.diffs.append({"outcome": outcome, "where": where, "detail": detail})

    def walk(self, group, base, a, b, path=""):
        where = f"{group}:{path}"
        if (base, path) in X_PATHS: self.env.setdefault(f"{base}:{path}", []).append([str(a)[:200], str(b)[:200]]); return
        if kind(a) != kind(b): return self.note("STRUCTURAL_DIFFERENCE", where, f"node kind {kind(a)} vs {kind(b)}")
        if isinstance(a, dict):
            if set(a) != set(b): self.note("STRUCTURAL_DIFFERENCE", where, f"key sets differ: -{sorted(set(a) - set(b))[:3]} +{sorted(set(b) - set(a))[:3]}")
            for k in sorted(set(a) & set(b)): self.walk(group, base, a[k], b[k], f"{path}/{k}" if path else k)
        elif isinstance(a, list):
            if len(a) != len(b): self.note("STRUCTURAL_DIFFERENCE", where, f"list length {len(a)} vs {len(b)}")
            for i, (x, y) in enumerate(zip(a, b)): self.walk(group, base, x, y, f"{path}/{i}")
        else: self.leaf(group, base, path, a, b)

    def leaf(self, group, base, path, a, b):
        where = f"{group}:{path}"
        exact = not (type(a) is float and type(b) is float) or any(f == base and r.search(path) for f, r in E_FLOAT_CONSTANTS) or path.startswith("declared/")
        if type(a) is not type(b) or (exact and not (a == b or (a != a and b != b))): return self.note("CATEGORICAL_DIFFERENCE", where, f"{a!r} vs {b!r}")
        if not (type(a) is float and not exact): return self.note("IDENTICAL", where, "")
        gp = (group, generic_path(path)); st = self.stats.setdefault(gp, {"leaves": 0, "max_abs": 0.0, "max_rel": 0.0, "differing": 0}); st["leaves"] += 1
        if a == b or (a != a and b != b): return self.note("IDENTICAL", where, "")
        if a != a or b != b or math.isinf(a) or math.isinf(b): return self.note("NUMERICAL_DIFFERENCE", where, f"{a!r} vs {b!r}")
        d = abs(a - b); m = max(abs(a), abs(b)); st["differing"] += 1; st["max_abs"] = max(st["max_abs"], d); st["max_rel"] = max(st["max_rel"], d / m if m else 0.0)
        if "dev" in path.rsplit("/", 1)[-1] and any((abs(a) <= t) != (abs(b) <= t) for t in self.thr):
            return self.note("CATEGORICAL_DIFFERENCE", where, f"deviation residual straddles a verdict threshold: {a!r} vs {b!r}")
        if d <= min(A + RR * m, CAP): self.note("WITHIN_TOLERANCE", where, f"|diff|={d:.3g}")
        else: self.note("NUMERICAL_DIFFERENCE", where, f"{a!r} vs {b!r} |diff|={d:.3g} > {min(A + RR * m, CAP):.3g}")


def collect_json(job, d):
    root = os.path.join(job, d); out = {}
    for r, _, fs in os.walk(root):
        for f in fs:
            rel = os.path.relpath(os.path.join(r, f), root)
            if f != "MANIFEST.sha256": out[rel] = os.path.join(r, f)           # MANIFEST.sha256 hashes host-specific fields: class X
    return out


def main():
    pos = [x for x in sys.argv[1:] if not x.startswith("--")]
    a = {k: arg(k) for k in ("--ref-preflight", "--replay-preflight", "--ref-disposition", "--replay-disposition")}
    ref, rep = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
    report = {"protocol": "docs/research/metamorphic_r4_cross_host_replay_protocol.md", "frozen": FROZEN, "tolerances": {"A": A, "Rr": RR, "cap": CAP}}
    why = provenance(ref, rep, a); report["provenance_failures"] = why
    if why:
        result = "COMPARISON_REFUSED_PROVENANCE_MISMATCH"; c = None
    else:
        decl = json.loads(read(os.path.join(ref, "results_full", "run_header.json")))["declared"]
        thr = [1e-9, 1e-6] + [x for key in ("noise", "one_repeat") for x in decl.get(key, []) if isinstance(x, float) and x > 0]
        c = Cmp(sorted(set(thr))); structural = []
        for d in RESULT_DIRS:
            f1, f2 = collect_json(ref, d), collect_json(rep, d)
            if set(f1) != set(f2): c.note("STRUCTURAL_DIFFERENCE", d, f"file sets differ: -{sorted(set(f1) - set(f2))[:3]} +{sorted(set(f2) - set(f1))[:3]}")
            for rel in sorted(set(f1) & set(f2)):
                top = rel.split(os.sep)[0] if os.sep in rel else rel
                group = f"{d}/{top}/*" if os.sep in rel else f"{d}/{rel}"
                c.walk(group, os.path.basename(rel), json.loads(read(f1[rel])), json.loads(read(f2[rel])))
        for name, rx in (("logs/controls.txt", r"^Ran \d+ tests"), ("logs/smoke_gate.txt", None), ("logs/full_gate.txt", None), ("exit_codes.txt", None)):
            t1, t2 = (read(os.path.join(j, name)).decode() for j in (ref, rep))
            if rx: t1 = "\n".join(l for l in t1.splitlines() if re.match(rx, l) or l == "OK"); t2 = "\n".join(l for l in t2.splitlines() if re.match(rx, l) or l == "OK")
            c.note("IDENTICAL" if t1 == t2 else "CATEGORICAL_DIFFERENCE", name, "" if t1 == t2 else "text differs")
        n = c.counts
        result = ("INCOMPARABLE" if n["STRUCTURAL_DIFFERENCE"] else "SEMANTIC_DIVERGENCE" if n["CATEGORICAL_DIFFERENCE"] else "NUMERICAL_DIVERGENCE" if n["NUMERICAL_DIFFERENCE"]
                  else "EQUIVALENT_WITHIN_DECLARED_TOLERANCE" if n["WITHIN_TOLERANCE"] else "EQUIVALENT_BITWISE")
        report.update({"counts": n, "numeric_field_statistics": [{"group": g, "path": p, **v} for (g, p), v in sorted(c.stats.items())], "differences": c.diffs, "environment_class_X": c.env})
    equiv = result.startswith("EQUIVALENT")
    report["result"] = result; report["overall"] = "R4_1_CROSS_HOST_REPLAY_EQUIVALENT" if equiv else "R4_1_CROSS_HOST_REPLAY_NOT_ESTABLISHED"
    if "--report" in sys.argv:
        with open(arg("--report"), "w") as fh: json.dump(report, fh, indent=1, sort_keys=True)
    print(f"RESULT: {result}"); print(f"OVERALL: {report['overall']}")
    if c: print("LEAF_COUNTS:", c.counts)
    for w in why[:20]: print("  provenance:", w)
    for d in (c.diffs[:20] if c else []): print("  diff:", d["outcome"], d["where"], d["detail"][:120])
    sys.exit(0 if equiv else 1)


main()
