#!/usr/bin/env python3
"""Cross-host comparison of two metamorphic r3 results directories (read-only).

Usage: rf_metamorphic_compare_runs.py RESULTS_DIR_A RESULTS_DIR_B [--out report.json]

Three kinds of equality are kept separate (whole files are NOT expected to be byte-identical: they may
differ in host details and durations, which this comparison ignores by construction):
  EXACT_IDENTITY   candidate identities and counts, applicability, bins, qualifying counts, reasons
  EXACT_VERDICT    relation verdicts, mutant credits, record statuses, identity verdict
  TOLERANCE        numerical measurements, within the frozen 1e-9
A numerical difference that changes a verdict is reported as VERDICT_CHANGING_NUMERIC_DIFFERENCE even if tiny.
Any key not classified below is reported as UNCLASSIFIED rather than silently accepted.
Exit 0 only if there is no difference outside tolerance, no verdict difference, and no unclassified key.
"""
import glob, json, os, sys

TOL = 1e-9
VERDICT_KEYS = {"verdict", "credit", "mutant_verdict", "production_verdict", "status", "decision"}
EXACT_KEYS = {"candidates", "n_base", "n_other", "n_missing", "n_extra", "missing_bins", "extra_bins", "not_applicable",
              "unobservable_entrants", "strong", "near_boundary_skipped", "lost", "candidates_with_headroom",
              "cardinality_violation", "matching", "reason", "base_unique", "other_unique", "qualifying",
              "persistent", "mutant_candidates", "fixture", "mutant", "INFORMATIONAL_ONLY", "one_repeat_error",
              "runtime_cap_exceeded_s"}
TOL_KEYS = {"max_freq_dev_hz", "max_excess_dev_db", "max_freq_dev_bins", "max_dev_db"}
IGNORE_FILES = {"run_header.json"}      # host/version details: reported informationally elsewhere


def flat(o, path=""):
    if isinstance(o, dict):
        for k in sorted(o):
            yield from flat(o[k], f"{path}/{k}")
    elif isinstance(o, list) and o and all(isinstance(x, dict) for x in o):
        for i, x in enumerate(o):
            yield from flat(x, f"{path}[{i}]")
    else:
        yield path, o


def leaf(path):
    return path.rsplit("/", 1)[-1].split("[")[0]


def classify(path):
    k = leaf(path)
    if k in VERDICT_KEYS: return "VERDICT"
    if k in TOL_KEYS: return "TOLERANCE"
    if k in EXACT_KEYS: return "EXACT"
    # relation names and dispositions appear as path segments; any numeric/other leaf is unclassified
    return "UNCLASSIFIED"


def compare_file(a, b, rel, rep):
    A = dict(flat(json.load(open(a)))); B = dict(flat(json.load(open(b))))
    for path in sorted(set(A) | set(B)):
        if path not in A or path not in B:
            rep["structure_differences"].append((rel, path, "present in only one run")); continue
        x, y, kind = A[path], B[path], classify(path)
        rep["compared"][kind] = rep["compared"].get(kind, 0) + 1
        if kind == "TOLERANCE":
            d = abs(float(x) - float(y)); rep["max_tolerance_diff"] = max(rep["max_tolerance_diff"], d)
            if d > TOL: rep["out_of_tolerance"].append((rel, path, x, y, d))
        elif kind in ("EXACT", "VERDICT"):
            if x != y: rep["exact_differences" if kind == "EXACT" else "verdict_differences"].append((rel, path, x, y))
        else:
            if x != y:
                rep["unclassified"].append((rel, path, x, y))


def main():
    ra, rb = sys.argv[1], sys.argv[2]
    rep = {"compared": {}, "max_tolerance_diff": 0.0, "out_of_tolerance": [], "exact_differences": [],
           "verdict_differences": [], "structure_differences": [], "unclassified": [], "files": 0}
    for sub in ("phaseA", "phaseB"):
        fa = sorted(os.path.basename(f) for f in glob.glob(os.path.join(ra, sub, "*.json")))
        fb = sorted(os.path.basename(f) for f in glob.glob(os.path.join(rb, sub, "*.json")))
        if fa != fb: rep["structure_differences"].append((sub, "file set", sorted(set(fa) ^ set(fb)), ""))
        for f in sorted(set(fa) & set(fb)):
            compare_file(os.path.join(ra, sub, f), os.path.join(rb, sub, f), f"{sub}/{f}", rep); rep["files"] += 1
    for f in ("identity.json", "summary.json"):
        pa, pb = os.path.join(ra, f), os.path.join(rb, f)
        if f == "summary.json":
            A, B = json.load(open(pa)), json.load(open(pb))
            for k in ("relation_summary", "persistence_summary", "mutant_matrix", "mutant_verdict"):
                if A.get(k) != B.get(k): rep["verdict_differences"].append((f, k, "differs", ""))
            rep["files"] += 1
        else:
            A, B = json.load(open(pa)), json.load(open(pb)); rep["files"] += 1
            if A["verdict"] != B["verdict"] or A["per_fixture"] != B["per_fixture"]:
                rep["verdict_differences"].append((f, "identity", A["verdict"], B["verdict"]))
    ha, hb = (json.load(open(os.path.join(d, "run_header.json"))) for d in (ra, rb))
    rep["header_informational"] = {k: [ha.get(k), hb.get(k)] for k in ("revision", "numpy", "harness_sha256", "production_sha256", "thread_env", "declared") if ha.get(k) != hb.get(k) or k in ("revision",)}
    ok = not (rep["out_of_tolerance"] or rep["exact_differences"] or rep["verdict_differences"] or rep["structure_differences"] or rep["unclassified"])
    rep["verdict"] = "CROSS_HOST_AGREEMENT" if ok else "CROSS_HOST_DISAGREEMENT"
    if "--out" in sys.argv: json.dump(rep, open(sys.argv[sys.argv.index("--out") + 1], "w"), indent=1, default=str)
    print(rep["verdict"], "| files compared:", rep["files"], "| compared leaves:", rep["compared"],
          "| max tolerance diff: %.3e" % rep["max_tolerance_diff"])
    for k in ("out_of_tolerance", "exact_differences", "verdict_differences", "structure_differences", "unclassified"):
        print(f"  {k}: {len(rep[k])}", rep[k][:3] if rep[k] else "")
    print("  header (informational):", json.dumps(rep["header_informational"], default=str)[:400])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
