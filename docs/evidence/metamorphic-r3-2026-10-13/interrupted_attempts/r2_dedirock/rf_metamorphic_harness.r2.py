#!/usr/bin/env python3
"""Read-only metamorphic harness r2 for the §5.21 spur detector and persistence estimator.

REVISION r2 (replaces the interrupted r1, whose comparator was O(N^2) in candidate count and
stalled on a 139,958-candidate mutant; the r1 source is preserved in the evidence directory).

Three layers, reported separately: execution fidelity, metamorphic validity, mutation
sensitivity.  There is no re-implementation of the detector; mutants are single exact textual
edits of the PRODUCTION source of `detect_candidates`, asserted to match exactly once.

Execution order and durability
  0. IDENTITY  unedited variant vs production, whole objects + failure parity, plus a namespace
               identity check, for EVERY fixture; written and fsynced before anything else.
               A failure stops the run (STOPPED.json); no transform is interpreted.
  A. PRODUCTION INVARIANCE   relations on production for all fixtures; one durable file per fixture.
  B. MUTATION SENSITIVITY    four mutants on the first K fixtures; Phase A files are the comparison
               authority and are never rewritten.  Each (mutant, fixture) is its own durable file.
Every durable file is written temp+fsync+rename and its sha256 appended to MANIFEST.sha256.

Matching (deterministic, no candidate list is ever truncated)
  * Relations with an exact bin map (SCALE, PHASE, PERMUTE, CONJ, SHIFT) match candidates by
    `peak_bin` through a dictionary: O(n).  Duplicate bins in either list => the comparison
    refuses (TRANSFORM_OR_ORACLE_UNVERIFIED, AMBIGUOUS_CANDIDATE_IDENTITY).
  * Relations that require the candidate COUNT to be preserved (SCALE, PHASE, PERMUTE, CONJ) check
    the counts first; a different count is RELATION_VIOLATED without any matching.  Relations that do
    not require it (SHIFT, NOISE, ONE_REPEAT) never turn a count difference into a violation.
  * Approximate stability relations (NOISE, ONE_REPEAT) look up each strong candidate in the sorted
    transformed list by bisect within +/-2 bins; ties go to the smaller |delta|, then the smaller bin.

Verdicts: RELATION_SATISFIED, RELATION_VIOLATED, PRECONDITION_NOT_MET, NUMERICAL_AMBIGUITY,
TRANSFORM_OR_ORACLE_UNVERIFIED; per mutant and relation: MUTANT_DETECTED, MUTANT_SURVIVED,
NOT_COMPARABLE, RESOURCE_LIMIT_REFUSAL (the last is not detection credit for any relation).

    python3 tools/rf_metamorphic_harness.py --holdout DIR --hashes FILE --results-dir NEWDIR
    python3 tools/rf_metamorphic_harness.py --summarize RESULTS_DIR
"""
from __future__ import annotations

import argparse, bisect, hashlib, inspect, json, math, os, sys, time
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import rf_spur_catalogue_sequencer as seq
from rf_spur_catalogue_sequencer import BIN_HZ, SAMPLE_RATE_HZ, usable_half_span_hz
from rf_spur_retention_estimator import measure_persistence
from rf_promotion_envelope import PLAN_PERSISTENCE_MARGIN_DB

REVISION = "metamorphic-harness.r2"
S, V, P, A, U = ("RELATION_SATISFIED", "RELATION_VIOLATED", "PRECONDITION_NOT_MET",
                 "NUMERICAL_AMBIGUITY", "TRANSFORM_OR_ORACLE_UNVERIFIED")
TOL_HZ = TOL_DB = 1e-9
AMBIG = 1e-6
FS = float(SAMPLE_RATE_HZ)
NOISE_REL, NOISE_BINS, NOISE_DB = 0.1, 0.05, 0.5
ONE_REPEAT_BINS, ONE_REPEAT_DB, ONE_REPEAT_HEADROOM_DB = 0.05, 1.5, 3.0
STRONG_DB = 15.0
SCALES, PHASES, SHIFTS = (1e-3, 0.37, 1e3), (math.pi / 7, math.pi / 2), (1000, -2500, 100000)
CANDIDATE_CAP = 1_000_000          # declared resource caps for mutant runs
MUTANT_RUNTIME_CAP_S = 900.0


# ---------------------------------------------------------------- production access
def as_tuples(cands):
    return [(c.baseband_hz, c.excess_db, c.peak_bin) for c in cands]


def raw_outcome(fn, windows):
    """Complete output of a detect function, including how it fails; candidate objects whole."""
    try:
        return ("OK", list(fn(windows)))
    except Exception as exc:
        return ("EXC", type(exc).__name__, getattr(exc, "code", None), str(exc))


def prod(windows):
    return as_tuples(seq.detect_candidates(windows))


MUTATIONS = {
    "absolute_floor": ("floor = float(np.median(median_spec))", "floor = 1.0"),
    "sign_dropped": ("freqs = np.fft.fftfreq(n, d=1.0 / SAMPLE_RATE_HZ)",
                     "freqs = np.abs(np.fft.fftfreq(n, d=1.0 / SAMPLE_RATE_HZ))"),
    "first_repeat_only": ("median_spec = np.median(spectra, axis=0)", "median_spec = spectra[0]"),
    "span_limit_removed": ("in_span = np.abs(freqs[1:-1]) <= usable", "in_span = np.ones(n - 2, dtype=bool)"),
}


def build_variant(edit):
    """Production `detect_candidates` with at most one exact textual edit; None => identity.
    Returns None if the edit does not match exactly once (the mutant is then UNVERIFIED)."""
    src = inspect.getsource(seq.detect_candidates)
    if edit is not None:
        old, new = edit
        if src.count(old) != 1:
            return None
        src = src.replace(old, new)
    ns = dict(seq.__dict__)
    exec(compile(src, "<production-variant>", "exec"), ns)
    return ns["detect_candidates"]


def namespace_report(variant):
    prod_g = seq.detect_candidates.__globals__
    names = sorted(n for n in variant.__code__.co_names if n in prod_g or n in variant.__globals__)
    mismatched = [n for n in names if variant.__globals__.get(n, object()) is not prod_g.get(n, object())]
    return {"names_checked": names, "identity_mismatches": mismatched,
            "not_module_globals": [n for n in names if n not in prod_g]}


# ---------------------------------------------------------------- transforms
def t_scale(c):    return lambda w: [x * c for x in w]
def t_phase(th):   return lambda w: [x * np.exp(1j * th) for x in w]
def t_conj(w):     return [np.conj(x) for x in w]
def t_perm(order): return lambda w: [w[i] for i in order]
def t_roll(m):     return lambda w: [np.roll(x, m) for x in w]


def t_shift(k):
    def f(w):
        n = len(w[0])
        ph = np.exp(2j * np.pi * ((k * np.arange(n, dtype=np.int64)) % n) / n)  # integer modulo first
        return [x * ph for x in w]
    return f


def noise_sd(w):
    return float(np.median(np.abs(w[0]))) / 0.6745 / math.sqrt(2)


def t_noise(seed):
    def f(w):
        rng = np.random.default_rng(seed)
        sd = noise_sd(w) * NOISE_REL
        return [x + sd * (rng.normal(size=x.size) + 1j * rng.normal(size=x.size)) for x in w]
    return f


def t_replace_repeat0(seed):
    def f(w):
        rng = np.random.default_rng(seed)
        sd = noise_sd(w)
        return [sd * (rng.normal(size=w[0].size) + 1j * rng.normal(size=w[0].size))] + list(w[1:])
    return f


def wrap(f):
    return (f + FS / 2) % FS - FS / 2


def freq_of_bin(b, n):
    return (b if b < n // 2 else b - n) * FS / n


# ---------------------------------------------------------------- comparators
def grade(dev_f, dev_e):
    d = max(dev_f, dev_e)          # Hz and dB share the same declared 1e-9
    return S if d <= TOL_HZ else (A if d <= AMBIG else V)


def _unique(cands):
    bins = [c[2] for c in cands]
    return len(set(bins)) == len(bins)


def keyed_compare(base, other, *, n, bin_map, inv_bin_map, f_map, preserves_cardinality, span_checked):
    """Exact-bin-map comparison.  Deterministic, O(n), never truncates either list."""
    if not _unique(base) or not _unique(other):
        return U, {"reason": "AMBIGUOUS_CANDIDATE_IDENTITY",
                   "base_unique": _unique(base), "other_unique": _unique(other)}
    detail = {"n_base": len(base), "n_other": len(other)}
    if preserves_cardinality and len(base) != len(other):
        detail.update({"cardinality_violation": True, "matching": "NOT_ATTEMPTED"})
        return V, detail
    usable = usable_half_span_hz()
    by_bin = {c[2]: c for c in other}
    matched, na, missing, dev_f, dev_e = set(), 0, [], 0.0, 0.0
    for f, e, b in sorted(base, key=lambda c: c[2]):
        if span_checked and abs(f_map(f)) > usable:
            na += 1
            continue
        mb = bin_map(b)
        g = by_bin.get(mb)
        if g is None:
            missing.append(b)
            continue
        matched.add(mb)
        dev_f = max(dev_f, abs(g[0] - f_map(f)))
        dev_e = max(dev_e, abs(g[1] - e))
    extra = sorted(b for b in by_bin if b not in matched)
    legit = []
    if span_checked:
        for b in extra:
            if abs(freq_of_bin(inv_bin_map(b), n)) > usable:      # pre-image outside span: unobservable
                legit.append(b)
        legit_set = set(legit)
        extra = [b for b in extra if b not in legit_set]
    detail.update({"not_applicable": na, "unobservable_entrants": len(legit), "missing_bins": missing[:5],
                   "n_missing": len(missing), "extra_bins": extra[:5], "n_extra": len(extra),
                   "max_freq_dev_hz": dev_f, "max_excess_dev_db": dev_e})
    if missing or extra:
        return V, detail
    if not base or na == len(base):
        return P, detail
    return grade(dev_f, dev_e), detail


def stability_compare(strong, other, bins_tol, db_tol):
    """Each strong base candidate located in `other` by bisect on sorted frequency (+/-2 bins)."""
    if not strong:
        return P, {"reason": "no strong candidate"}
    srt = sorted(other, key=lambda c: (c[0], c[2]))
    freqs = [c[0] for c in srt]
    lost, dev_f, dev_e, boundary = [], 0.0, 0.0, 0
    for f, e, b in strong:
        if abs(e - PLAN_PERSISTENCE_MARGIN_DB) < 1.0:
            boundary += 1
            continue
        lo = bisect.bisect_left(freqs, f - 2 * BIN_HZ)
        hi = bisect.bisect_right(freqs, f + 2 * BIN_HZ)
        near = srt[lo:hi]
        if not near:
            lost.append((round(f, 2), round(e, 2)))
            continue
        g = min(near, key=lambda c: (abs(c[0] - f), c[2]))
        dev_f, dev_e = max(dev_f, abs(g[0] - f) / BIN_HZ), max(dev_e, abs(g[1] - e))
    detail = {"strong": len(strong), "near_boundary_skipped": boundary, "lost": lost,
              "max_freq_dev_bins": dev_f, "max_excess_dev_db": dev_e}
    return (V if (lost or dev_f > bins_tol or dev_e > db_tol) else S), detail


# ---------------------------------------------------------------- relations
def relations(detector, windows, base):
    n = len(windows[0]); out = {}
    ident = dict(bin_map=lambda b: b, inv_bin_map=lambda b: b, f_map=lambda f: f)
    for c in SCALES:
        out[f"SCALE x{c:g}"] = keyed_compare(base, detector(t_scale(c)(windows)), n=n,
                                             preserves_cardinality=True, span_checked=False, **ident)
    for th in PHASES:
        out[f"PHASE {th:.3f}"] = keyed_compare(base, detector(t_phase(th)(windows)), n=n,
                                               preserves_cardinality=True, span_checked=False, **ident)
    out["CONJ"] = keyed_compare(base, detector(t_conj(windows)), n=n, preserves_cardinality=True,
                                span_checked=False, bin_map=lambda b: n - b, inv_bin_map=lambda b: n - b,
                                f_map=lambda f: -f)
    for name, order in (("reverse", list(range(7, -1, -1))), ("rot3", [3, 4, 5, 6, 7, 0, 1, 2])):
        out[f"PERMUTE {name}"] = keyed_compare(base, detector(t_perm(order)(windows)), n=n,
                                               preserves_cardinality=True, span_checked=False, **ident)
    for k in SHIFTS:
        out[f"SHIFT {k:+d} bins"] = keyed_compare(
            base, detector(t_shift(k)(windows)), n=n, preserves_cardinality=False, span_checked=True,
            bin_map=lambda b, k=k: (b + k) % n, inv_bin_map=lambda b, k=k: (b - k) % n,
            f_map=lambda f, k=k: wrap(f + k * BIN_HZ))
    strong = [b for b in base if b[1] >= STRONG_DB]
    out["NOISE weak"] = stability_compare(strong, detector(t_noise(5)(windows)), NOISE_BINS, NOISE_DB)
    return out


def worst_case_excess_after_loss(spectra, bin_index):
    """Independent of the detector's candidate logic: median power at `bin_index` if one repeat's
    power there were as low as possible, against the median-of-median floor."""
    vals = np.sort(spectra[:, bin_index])
    worst = np.sort(np.concatenate([[0.0], vals[:-1]]))
    floor = float(np.median(np.median(spectra, axis=0)))
    return 10.0 * math.log10(max(0.5 * (worst[3] + worst[4]), 1e-300) / floor)


def one_repeat_relation(windows, base, detector):
    n = len(windows[0])
    hann = 0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(n) / (n - 1)))
    spectra = np.abs(np.fft.fft(np.stack(windows) * hann[None, :], axis=1)) ** 2
    strong = [b for b in base if b[1] >= STRONG_DB]
    tolerant = [b for b in strong if worst_case_excess_after_loss(spectra, b[2])
                - PLAN_PERSISTENCE_MARGIN_DB >= ONE_REPEAT_HEADROOM_DB]
    if not tolerant:
        return {"ONE_REPEAT_REPLACED (detector)": (P, {"reason": "no candidate with independent "
                                                       "worst-case headroom", "strong": len(strong)})}
    v, d = stability_compare(tolerant, detector(t_replace_repeat0(6)(windows)), ONE_REPEAT_BINS, ONE_REPEAT_DB)
    d["candidates_with_headroom"] = len(tolerant)
    return {"ONE_REPEAT_REPLACED (detector)": (v, d)}


def roll_info(windows, base):
    other = prod(t_roll(1000)(windows))
    osrt = sorted(c[0] for c in other)
    dev = []
    for f, _, _ in base:
        i = bisect.bisect_left(osrt, f)
        cand = [osrt[j] for j in (i - 1, i) if 0 <= j < len(osrt)]
        dev.append(min((abs(x - f) for x in cand), default=float("inf")) / BIN_HZ)
    return {"INFORMATIONAL_ONLY": True, "n_base": len(base), "n_other": len(other),
            "max_freq_dev_bins": max(dev) if dev else 0.0}


def persistence_relations(windows, base):
    def pers(ws, ff):
        o = measure_persistence(tuning_id="metamorphic", windows=ws, sample_rate_hz=FS, feature_baseband_hz=ff)
        return o, o.repeat_excess_db, o.qualifying()
    out = {}
    for i, (f, e, b) in enumerate(base):
        o0, ex0, q0 = pers(windows, f)
        r = {}
        for name, ws, ff in (("SCALE x0.37", t_scale(0.37)(windows), f),
                             ("PHASE 0.449", t_phase(PHASES[0])(windows), f),
                             ("CONJ", t_conj(windows), -f)):
            _, ex, q = pers(ws, ff)
            d = max(abs(a - c) for a, c in zip(ex0, ex))
            r[name] = (S if (d <= TOL_DB and q == q0) else (A if (d <= AMBIG and q == q0) else V),
                       {"max_dev_db": d, "qualifying": [q0, q]})
        _, ex, q = pers(t_perm(list(range(7, -1, -1)))(windows), f)
        d = max(abs(a - c) for a, c in zip(sorted(ex0), sorted(ex)))
        r["PERMUTE (qualifying count, multiset of excesses)"] = (S if (q == q0 and d <= TOL_DB) else V,
                                                                 {"qualifying": [q0, q], "max_dev_db": d})
        if q0 == 8:
            o1, _, q1 = pers(t_replace_repeat0(6)(windows), f)
            r["ONE_REPEAT_REPLACED (persistence)"] = (
                S if (q1 >= q0 - 1 and o0.persistent() == o1.persistent()) else V,
                {"qualifying": [q0, q1], "persistent": [o0.persistent(), o1.persistent()]})
        else:
            r["ONE_REPEAT_REPLACED (persistence)"] = (P, {"reason": "no headroom: original qualifying != 8",
                                                          "qualifying": q0})
        out[i] = r
    return out


# ---------------------------------------------------------------- durable output
def durable_write(path, obj, manifest_path):
    data = json.dumps(obj, indent=1, default=str, sort_keys=True).encode()
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data); fh.flush(); os.fsync(fh.fileno())
    os.replace(tmp, path)
    with open(manifest_path, "a") as mf:
        mf.write(f"{hashlib.sha256(data).hexdigest()}  {os.path.relpath(path, os.path.dirname(manifest_path))}\n")
        mf.flush(); os.fsync(mf.fileno())


def verify_manifest(results_dir):
    """Return (ok_files, bad_files, missing_files) according to MANIFEST.sha256."""
    ok, bad, missing = [], [], []
    mp = os.path.join(results_dir, "MANIFEST.sha256")
    if not os.path.exists(mp):
        return ok, bad, ["MANIFEST.sha256"]
    with open(mp) as fh:
        lines = fh.read().splitlines()
    for line in lines:
        if not line.strip():
            continue
        digest, rel = line.split(None, 1)
        rel = rel.strip(); p = os.path.join(results_dir, rel)
        if not os.path.exists(p):
            missing.append(rel)
            continue
        with open(p, "rb") as fh:
            good = hashlib.sha256(fh.read()).hexdigest() == digest
        (ok if good else bad).append(rel)
    return ok, bad, missing


def jsonable(rels):
    return {k: {"verdict": v, **d} for k, (v, d) in rels.items()}


# ---------------------------------------------------------------- driver
def load_fixtures(holdout, hashes):
    names = []
    for line in open(hashes):
        if line.strip():
            digest, name = line.split()
            if hashlib.sha256(open(os.path.join(holdout, name), "rb").read()).hexdigest() != digest:
                raise SystemExit(f"FIXTURE HASH MISMATCH {name}; refusing to run")
            names.append(name)
    return names


def load_windows(holdout, name):
    arr = np.load(os.path.join(holdout, name)).astype(np.complex128)
    return [arr[i] for i in range(arr.shape[0])]


def identity_phase(names, loader, mp, rd, stop_on_fail=True):
    """Phase 0: whole-output identity + namespace identity. Durable; returns True if verified."""
    identity_fn = build_variant(None)
    ns = namespace_report(identity_fn)
    ident = {"comparison": "whole CandidateFeature objects + failure parity", "namespace": ns,
             "per_fixture": {}, "scope": "fidelity on these fixtures, not a universal guarantee"}
    failed = bool(ns["identity_mismatches"])
    for name in names:
        w = loader(name)
        same = raw_outcome(identity_fn, w) == raw_outcome(seq.detect_candidates, w)
        ident["per_fixture"][name] = same
        failed |= not same
        print(f"IDENTITY {name}: {'EQUAL' if same else 'MISMATCH'}", flush=True)
    ident["verdict"] = "IDENTITY_FAILED" if failed else "IDENTITY_AND_NAMESPACE_VERIFIED"
    durable_write(os.path.join(rd, "identity.json"), ident, mp)
    print(f"IDENTITY VERDICT: {ident['verdict']}", flush=True)
    if failed:
        durable_write(os.path.join(rd, "STOPPED.json"),
                      {"reason": "IDENTITY CONTROL FAILED; transformed results are not interpreted",
                       "classification": "EXECUTION_FIDELITY_FAILURE"}, mp)
    return not failed


def run(args):
    rd = args.results_dir
    if os.path.exists(rd) and os.listdir(rd):
        raise SystemExit(f"results dir {rd} is not empty; r2 never overwrites earlier evidence")
    for sub in ("phaseA", "phaseB"):
        os.makedirs(os.path.join(rd, sub), exist_ok=True)
    mp = os.path.join(rd, "MANIFEST.sha256")
    names = load_fixtures(args.holdout, args.hashes)
    if args.limit:
        names = names[:args.limit]
    t0 = time.time()
    header = {"revision": REVISION, "argv": sys.argv, "numpy": np.__version__,
              "sequencer_revision": seq.SEQUENCER_REVISION, "fixtures": names,
              "thread_env": {k: os.environ.get(k) for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS",
                                                            "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")},
              "harness_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
              "production_sha256": {f: hashlib.sha256(open(os.path.join(os.path.dirname(__file__), "..", f), "rb").read()).hexdigest()
                                    for f in ("rf_spur_catalogue_sequencer.py", "rf_spur_retention_estimator.py")},
              "declared": {"tol_hz": TOL_HZ, "tol_db": TOL_DB, "ambiguity_upper": AMBIG,
                           "noise": [NOISE_REL, NOISE_BINS, NOISE_DB],
                           "one_repeat": [ONE_REPEAT_BINS, ONE_REPEAT_DB, ONE_REPEAT_HEADROOM_DB],
                           "strong_db": STRONG_DB, "scales": SCALES, "phases": PHASES, "shifts_bins": SHIFTS,
                           "candidate_cap": CANDIDATE_CAP, "mutant_runtime_cap_s": MUTANT_RUNTIME_CAP_S,
                           "mutations": {m: list(e) for m, e in MUTATIONS.items()}}}
    durable_write(os.path.join(rd, "run_header.json"), header, mp)

    if not identity_phase(names, lambda n: load_windows(args.holdout, n), mp, rd):
        return 3
    print(f"(t={time.time()-t0:.0f}s)", flush=True)

    # ---- A. production invariance
    for name in names:
        w = load_windows(args.holdout, name)
        base = prod(w)
        rels = relations(prod, w, base)
        rels.update(one_repeat_relation(w, base, prod))
        entry = {"fixture": name, "candidates": len(base), "relations": jsonable(rels),
                 "ROLL": roll_info(w, base)}
        if not args.no_persistence:
            pr = persistence_relations(w, base)
            entry["persistence"] = {str(i): jsonable(c) for i, c in pr.items()}
        durable_write(os.path.join(rd, "phaseA", f"{name}.json"), entry, mp)
        print(f"A {name}: cands={len(base)} violated={[k for k, (v, _) in rels.items() if v == V]} "
              f"t={time.time()-t0:.0f}s", flush=True)

    # ---- B. mutation sensitivity (Phase A files are authority; never rewritten)
    for mname, edit in MUTATIONS.items():
        fn = build_variant(edit)
        for name in names[:args.mutant_fixtures]:
            path = os.path.join(rd, "phaseB", f"{mname}__{name}.json")
            if fn is None:
                durable_write(path, {"mutant": mname, "fixture": name, "status": U,
                                     "reason": "edit did not match production source exactly once"}, mp)
                continue
            w = load_windows(args.holdout, name)
            authority = json.load(open(os.path.join(rd, "phaseA", f"{name}.json")))["relations"]
            t_m = time.time()
            mb = as_tuples(fn(w))
            rec = {"mutant": mname, "fixture": name, "mutant_candidates": len(mb), "relations": {}}
            if len(mb) > CANDIDATE_CAP:
                rec["status"] = "RESOURCE_LIMIT_REFUSAL"
                rec["reason"] = f"mutant produced {len(mb)} candidates > cap {CANDIDATE_CAP}; not detection credit"
            else:
                rec["status"] = "COMPLETED"
                mdet = lambda ww, fn=fn: as_tuples(fn(ww))
                mrels = relations(mdet, w, mb)
                try:
                    mrels.update(one_repeat_relation(w, mb, mdet))
                except Exception as exc:
                    rec["one_repeat_error"] = f"{type(exc).__name__}: {exc}"
                for k, (v, d) in mrels.items():
                    pv = authority.get(k, {}).get("verdict")
                    if v == V and pv == S:
                        credit = "MUTANT_DETECTED"
                    elif v in (S, A) and pv == S:
                        credit = "MUTANT_SURVIVED"
                    else:
                        credit = "NOT_COMPARABLE"
                    rec["relations"][k] = {"mutant_verdict": v, "production_verdict": pv, "credit": credit, **d}
                if time.time() - t_m > MUTANT_RUNTIME_CAP_S:
                    rec["runtime_cap_exceeded_s"] = time.time() - t_m
            durable_write(path, rec, mp)
            print(f"B {mname} {name}: cands={len(mb)} {rec['status']} t={time.time()-t0:.0f}s", flush=True)
    summarize(rd)
    return 0


def summarize(rd):
    ok, bad, missing = verify_manifest(rd)
    out = {"manifest": {"ok": len(ok), "bad": bad, "missing": missing}, "relation_summary": {},
           "persistence_summary": {}, "mutant_matrix": {}, "mutant_verdict": {}}
    ident_path = os.path.join(rd, "identity.json")
    if os.path.exists(ident_path):
        out["identity_verdict"] = json.load(open(ident_path))["verdict"]
    adir = os.path.join(rd, "phaseA")
    for fn in sorted(os.listdir(adir)) if os.path.isdir(adir) else []:
        if not fn.endswith(".json"):
            continue
        e = json.load(open(os.path.join(adir, fn)))
        for k, d in e["relations"].items():
            out["relation_summary"].setdefault(k, {}).setdefault(d["verdict"], 0)
            out["relation_summary"][k][d["verdict"]] += 1
        for c in e.get("persistence", {}).values():
            for k, d in c.items():
                out["persistence_summary"].setdefault(k, {}).setdefault(d["verdict"], 0)
                out["persistence_summary"][k][d["verdict"]] += 1
    bdir = os.path.join(rd, "phaseB")
    for fn in sorted(os.listdir(bdir)) if os.path.isdir(bdir) else []:
        if not fn.endswith(".json"):
            continue
        e = json.load(open(os.path.join(bdir, fn)))
        cell = out["mutant_matrix"].setdefault(e["mutant"], {})
        if e.get("status") != "COMPLETED":
            cell.setdefault("_status", {}).setdefault(e.get("status"), 0)
            cell["_status"][e.get("status")] += 1
            continue
        for k, d in e["relations"].items():
            cell.setdefault(k, {}).setdefault(d["credit"], 0)
            cell[k][d["credit"]] += 1
    for m, cell in out["mutant_matrix"].items():
        det = any(c.get("MUTANT_DETECTED") for k, c in cell.items() if k != "_status")
        out["mutant_verdict"][m] = "MUTANT_DETECTED" if det else "MUTANT_SURVIVED_OR_NOT_COMPARABLE"
    with open(os.path.join(rd, "summary.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(out, indent=1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout"); ap.add_argument("--hashes"); ap.add_argument("--results-dir")
    ap.add_argument("--limit", type=int, default=0); ap.add_argument("--no-persistence", action="store_true")
    ap.add_argument("--mutant-fixtures", type=int, default=6)
    ap.add_argument("--summarize")
    a = ap.parse_args()
    if a.summarize:
        summarize(a.summarize); return
    if not (a.holdout and a.hashes and a.results_dir):
        ap.error("--holdout, --hashes and --results-dir are required")
    raise SystemExit(run(a))


if __name__ == "__main__":
    main()
