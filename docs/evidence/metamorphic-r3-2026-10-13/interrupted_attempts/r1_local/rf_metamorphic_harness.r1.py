#!/usr/bin/env python3
"""Read-only metamorphic harness for the §5.21 spur detector and persistence estimator.

Three layers are kept distinct and reported separately:

  1. EXECUTION FIDELITY   the harness's way of running production code (including the
                          source-level mutation mechanism) reproduces production output on
                          the unmodified fixtures.  There is no re-implementation of the
                          detector anywhere in this file.
  2. METAMORPHIC VALIDITY each transform is mathematically justified, with its preconditions
                          stated; a case outside its preconditions is PRECONDITION_NOT_MET,
                          never a violation.
  3. MUTATION SENSITIVITY single-fault mutants of the PRODUCTION SOURCE (one exact textual
                          edit of `detect_candidates`, asserted to match exactly once) are run
                          through the same relations; reported as a mutant-by-relation matrix.

Verdict vocabulary (a failure is a finding to investigate, not automatically a defect):

  RELATION_SATISFIED          held within the declared tolerance
  RELATION_VIOLATED           outside tolerance with preconditions met
  PRECONDITION_NOT_MET        the relation does not apply (e.g. candidate shifted out of span)
  NUMERICAL_AMBIGUITY         deviation above 1e-9 but at or below 1e-6 (declared, see below)
  TRANSFORM_OR_ORACLE_UNVERIFIED  the transform or a mutation edit could not be validated
  MUTANT_DETECTED / MUTANT_SURVIVED   per mutant and relation

Declared before any run (not tuned): freq 1e-9 Hz, excess 1e-9 dB; ambiguity band up to 1e-6;
weak-noise stability 0.05 bin / 0.5 dB on candidates >= 15 dB and >= 1 dB from the margin;
one-repeat-replaced stability 0.05 bin / 1.5 dB on candidates >= 15 dB.

    python3 tools/rf_metamorphic_harness.py --holdout DIR --hashes FILE --out out.json
"""
from __future__ import annotations

import argparse, hashlib, inspect, json, math, os, sys, time
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import rf_spur_catalogue_sequencer as seq
from rf_spur_catalogue_sequencer import BIN_HZ, SAMPLE_RATE_HZ, usable_half_span_hz
from rf_spur_retention_estimator import measure_persistence
from rf_promotion_envelope import PLAN_PERSISTENCE_MARGIN_DB

S, V, P, A, U = ("RELATION_SATISFIED", "RELATION_VIOLATED", "PRECONDITION_NOT_MET",
                 "NUMERICAL_AMBIGUITY", "TRANSFORM_OR_ORACLE_UNVERIFIED")
TOL_HZ = TOL_DB = 1e-9
AMBIG = 1e-6
FS = float(SAMPLE_RATE_HZ)
NOISE_REL, NOISE_BINS, NOISE_DB = 0.1, 0.05, 0.5
ONE_REPEAT_BINS, ONE_REPEAT_DB = 0.05, 1.5
STRONG_DB = 15.0
SCALES, PHASES, SHIFTS = (1e-3, 0.37, 1e3), (math.pi / 7, math.pi / 2), (1000, -2500, 100000)


# ------------------------------------------------------------------ production access
def as_tuples(cands):
    return [(c.baseband_hz, c.excess_db, c.peak_bin) for c in cands]


def raw_outcome(fn, windows):
    """Complete output of a detect function, including how it fails.  Candidate objects are
    compared whole (dataclass equality over every field), not through a projection."""
    try:
        return ("OK", list(fn(windows)))
    except Exception as exc:  # parity of failure is part of fidelity
        return ("EXC", type(exc).__name__, getattr(exc, "code", None), str(exc))


def prod(windows):
    return as_tuples(seq.detect_candidates(windows))


MUTATIONS = {  # name -> (exact production source line, replacement)
    "absolute_floor": ("floor = float(np.median(median_spec))", "floor = 1.0"),
    "sign_dropped": ("freqs = np.fft.fftfreq(n, d=1.0 / SAMPLE_RATE_HZ)",
                     "freqs = np.abs(np.fft.fftfreq(n, d=1.0 / SAMPLE_RATE_HZ))"),
    "first_repeat_only": ("median_spec = np.median(spectra, axis=0)", "median_spec = spectra[0]"),
    "span_limit_removed": ("in_span = np.abs(freqs[1:-1]) <= usable", "in_span = np.ones(n - 2, dtype=bool)"),
}


def build_variant(edit):
    """Production `detect_candidates` with at most one exact textual edit; None edit = identity."""
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
    """Every global the function can resolve must be the very object production resolves."""
    prod_g = seq.detect_candidates.__globals__
    names = sorted(n for n in variant.__code__.co_names if n in prod_g or n in variant.__globals__)
    mismatched = [n for n in names if variant.__globals__.get(n, object()) is not prod_g.get(n, object())]
    builtin_only = [n for n in names if n not in prod_g]
    return {"names_checked": names, "identity_mismatches": mismatched, "not_module_globals": builtin_only,
            "snapshot_note": "globals are a snapshot of the module dict at harness start; "
                             "same objects, not copies, for every name above"}


# ------------------------------------------------------------------ transforms
def t_scale(c):  return lambda w: [x * c for x in w]
def t_phase(th): return lambda w: [x * np.exp(1j * th) for x in w]
def t_conj(w):   return [np.conj(x) for x in w]
def t_perm(order): return lambda w: [w[i] for i in order]
def t_roll(m):   return lambda w: [np.roll(x, m) for x in w]


def t_shift(k):
    def f(w):
        n = len(w[0])
        # integer modulo first: k*i reaches ~5e10, where a float phase would lose ~1e-5 rad
        ph = np.exp(2j * np.pi * ((k * np.arange(n, dtype=np.int64)) % n) / n)
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
    """Replace repeat 0 with tone-free noise of the fixture's own level."""
    def f(w):
        rng = np.random.default_rng(seed)
        sd = noise_sd(w)
        bad = sd * (rng.normal(size=w[0].size) + 1j * rng.normal(size=w[0].size))
        return [bad] + list(w[1:])
    return f


def wrap(f):
    return (f + FS / 2) % FS - FS / 2


# ------------------------------------------------------------------ comparison
def grade(dev_f, dev_e):
    d = max(dev_f / 1.0, dev_e)  # Hz and dB share the same 1e-9 declaration
    if d <= TOL_HZ:
        return S
    return A if d <= AMBIG else V


def compare(base, other, f_map, *, bin_map=None, shift_bins=None):
    """Match each base candidate to the nearest transformed candidate under `f_map`."""
    usable = usable_half_span_hz()
    pending, dev_f, dev_e, na, viol = list(other), 0.0, 0.0, 0, []
    for f, e, b in base:
        ef = f_map(f)
        if abs(ef) > usable:
            na += 1
            continue
        if not pending:
            viol.append(("missing", round(f, 4), round(e, 3)))
            continue
        j = min(range(len(pending)), key=lambda i: abs(pending[i][0] - ef))
        g = pending.pop(j)
        if abs(g[0] - ef) > 1e-3:
            viol.append(("missing", round(f, 4), round(e, 3)))
            pending.append(g)
            continue
        if bin_map is not None and g[2] != bin_map(b):
            viol.append(("bin", b, g[2]))
        dev_f, dev_e = max(dev_f, abs(g[0] - ef)), max(dev_e, abs(g[1] - e))
    # leftovers: legitimate only if their pre-image lies outside the span (unobservable in base)
    extras = []
    for g in pending:
        pre = wrap(g[0] - shift_bins * BIN_HZ) if shift_bins is not None else None
        if pre is not None and abs(pre) > usable:
            na += 1
        else:
            extras.append((round(g[0], 4), round(g[1], 3)))
    if viol or extras:
        return V, {"violations": viol[:5], "extra": extras[:5], "n_base": len(base),
                   "n_other": len(other), "max_freq_dev_hz": dev_f, "max_excess_dev_db": dev_e}
    if not base or na == len(base):
        return P, {"reason": "no comparable candidate", "n_base": len(base), "not_applicable": na}
    return grade(dev_f, dev_e), {"n_base": len(base), "n_other": len(other), "not_applicable": na,
                                 "max_freq_dev_hz": dev_f, "max_excess_dev_db": dev_e}


def relations(detector, windows, base, *, with_stochastic):
    n = len(windows[0]); out = {}
    for c in SCALES:
        out[f"SCALE x{c:g}"] = compare(base, detector(t_scale(c)(windows)), lambda f: f)
    for th in PHASES:
        out[f"PHASE {th:.3f}"] = compare(base, detector(t_phase(th)(windows)), lambda f: f)
    out["CONJ"] = compare(base, detector(t_conj(windows)), lambda f: -f, bin_map=lambda b: n - b)
    for name, order in (("reverse", list(range(7, -1, -1))), ("rot3", [3, 4, 5, 6, 7, 0, 1, 2])):
        out[f"PERMUTE {name}"] = compare(base, detector(t_perm(order)(windows)), lambda f: f)
    for k in SHIFTS:
        out[f"SHIFT {k:+d} bins"] = compare(base, detector(t_shift(k)(windows)),
                                            lambda f, k=k: wrap(f + k * BIN_HZ), shift_bins=k)
    if with_stochastic:
        strong = [b for b in base if b[1] >= STRONG_DB]
        out["NOISE weak"] = stability(strong, detector(t_noise(5)(windows)), NOISE_BINS, NOISE_DB, base)
    return out


def stability(strong, other, bins_tol, db_tol, base):
    if not strong:
        return P, {"reason": "no strong candidate"}
    lost, dev_f, dev_e = [], 0.0, 0.0
    boundary = 0
    for f, e, _ in strong:
        if abs(e - PLAN_PERSISTENCE_MARGIN_DB) < 1.0:
            boundary += 1
            continue
        near = [o for o in other if abs(o[0] - f) <= 2 * BIN_HZ]
        if not near:
            lost.append((round(f, 2), round(e, 2)))
            continue
        o = min(near, key=lambda o: abs(o[0] - f))
        dev_f, dev_e = max(dev_f, abs(o[0] - f) / BIN_HZ), max(dev_e, abs(o[1] - e))
    if lost or dev_f > bins_tol or dev_e > db_tol:
        return V, {"lost": lost, "max_freq_dev_bins": dev_f, "max_excess_dev_db": dev_e}
    return S, {"strong": len(strong), "near_boundary_skipped": boundary,
               "max_freq_dev_bins": dev_f, "max_excess_dev_db": dev_e}


ONE_REPEAT_HEADROOM_DB = 3.0   # declared: worst-case excess after losing one repeat must stay this far above the margin


def worst_case_excess_after_loss(spectra, bin_index):
    """Independent of the detector's candidate logic: the median power at `bin_index` if one
    repeat's power there were as low as possible (zero), against the fixture's median-of-median floor."""
    vals = np.sort(spectra[:, bin_index])
    # drop the strongest-contributing repeat's effect conservatively: lose the repeat that
    # moves the median most, i.e. replace a high value by zero
    worst = np.sort(np.concatenate([[0.0], vals[:-1]]))
    new_median = 0.5 * (worst[3] + worst[4])
    floor = float(np.median(np.median(spectra, axis=0)))
    return 10.0 * math.log10(max(new_median, 1e-300) / floor)


def one_repeat_relations(windows, base, detector=None):
    detector = detector or prod
    out = {}
    n = len(windows[0])
    hann = 0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(n) / (n - 1)))
    spectra = np.abs(np.fft.fft(np.stack(windows) * hann[None, :], axis=1)) ** 2
    strong = [b for b in base if b[1] >= STRONG_DB]
    tolerant = [b for b in strong
                if worst_case_excess_after_loss(spectra, b[2]) - PLAN_PERSISTENCE_MARGIN_DB >= ONE_REPEAT_HEADROOM_DB]
    if not tolerant:
        out["ONE_REPEAT_REPLACED (detector)"] = (P, {"reason": "no candidate with independent worst-case headroom",
                                                      "strong": len(strong)})
    else:
        v, d = stability(tolerant, detector(t_replace_repeat0(6)(windows)), ONE_REPEAT_BINS, ONE_REPEAT_DB, base)
        d["candidates_with_headroom"] = len(tolerant); d["strong"] = len(strong)
        out["ONE_REPEAT_REPLACED (detector)"] = (v, d)
    return out


def roll_info(windows, base):
    other = prod(t_roll(1000)(windows))
    dev = [min((abs(o[0] - f) for o in other), default=float("inf")) / BIN_HZ for f, _, _ in base]
    return {"INFORMATIONAL_ONLY": True, "n_base": len(base), "n_other": len(other),
            "max_freq_dev_bins": max(dev) if dev else 0.0}


def persistence_relations(windows, base):
    def pers(ws, ff):
        o = measure_persistence(tuning_id="metamorphic", windows=ws, sample_rate_hz=FS,
                                feature_baseband_hz=ff)
        return o.repeat_excess_db, o.qualifying()
    out = {}
    for i, (f, e, b) in enumerate(base):
        ex0, q0 = pers(windows, f)
        r = {}
        for name, ws, ff in (("SCALE x0.37", t_scale(0.37)(windows), f),
                             ("PHASE 0.449", t_phase(PHASES[0])(windows), f),
                             ("CONJ", t_conj(windows), -f)):
            ex, q = pers(ws, ff)
            d = max(abs(a - c) for a, c in zip(ex0, ex))
            r[name] = (S if (d <= TOL_DB and q == q0) else (A if (d <= AMBIG and q == q0) else V),
                       {"max_dev_db": d, "qualifying": [q0, q]})
        ex, q = pers(t_perm(list(range(7, -1, -1)))(windows), f)
        d = max(abs(a - c) for a, c in zip(sorted(ex0), sorted(ex)))
        r["PERMUTE (qualifying count, multiset of excesses)"] = (
            S if (q == q0 and d <= TOL_DB) else V, {"qualifying": [q0, q], "max_dev_db": d})
        if q0 == 8:   # headroom: losing one repeat leaves 7 qualifying
            ex1, q1 = pers(t_replace_repeat0(6)(windows), f)
            o0 = measure_persistence(tuning_id="metamorphic", windows=windows, sample_rate_hz=FS, feature_baseband_hz=f)
            o1 = measure_persistence(tuning_id="metamorphic", windows=t_replace_repeat0(6)(windows),
                                     sample_rate_hz=FS, feature_baseband_hz=f)
            r["ONE_REPEAT_REPLACED (persistence)"] = (
                S if (q1 >= q0 - 1 and o0.persistent() == o1.persistent()) else V,
                {"qualifying": [q0, q1], "persistent": [o0.persistent(), o1.persistent()]})
        else:
            r["ONE_REPEAT_REPLACED (persistence)"] = (P, {"reason": "no headroom: original qualifying != 8", "qualifying": q0})
        out[i] = r
    return out


# ------------------------------------------------------------------ driver
def load_fixtures(holdout, hashes):
    names = []
    for line in open(hashes):
        if line.strip():
            digest, name = line.split()
            if hashlib.sha256(open(os.path.join(holdout, name), "rb").read()).hexdigest() != digest:
                raise SystemExit(f"FIXTURE HASH MISMATCH {name}; refusing to run")
            names.append(name)
    return names


def tally(counter, key, verdict):
    counter.setdefault(key, {}).setdefault(verdict, 0)
    counter[key][verdict] += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", required=True); ap.add_argument("--hashes", required=True)
    ap.add_argument("--out"); ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-persistence", action="store_true")
    ap.add_argument("--mutant-fixtures", type=int, default=6)
    a = ap.parse_args()
    names = load_fixtures(a.holdout, a.hashes)
    if a.limit:
        names = names[:a.limit]
    identity_fn = build_variant(None)
    raw_variants = {m: build_variant(e) for m, e in MUTATIONS.items()}
    variants = {m: (None if v is None else (lambda w, v=v: as_tuples(v(w)))) for m, v in raw_variants.items()}
    unverified = [m for m, v in raw_variants.items() if v is None]
    t0 = time.time()
    rep = {"execution_fidelity": {"identity_variant_equals_production_on": 0, "of": 0, "mismatch": [],
                                  "comparison": "whole CandidateFeature objects + failure parity",
                                  "namespace": namespace_report(identity_fn),
                                  "scope": "evidence of fidelity on these fixtures, not a universal guarantee"},
           "mutation_edits_unverified": unverified, "fixtures": {}, "relation_summary": {},
           "persistence_summary": {}, "mutant_matrix": {}, "mutant_verdict": {}}
    rel_sum, pers_sum = {}, {}
    mat = {m: {} for m in MUTATIONS}
    mut_fix = 0
    for name in names:
        arr = np.load(os.path.join(a.holdout, name)).astype(np.complex128)
        w = [arr[i] for i in range(arr.shape[0])]
        base = prod(w)
        rep["execution_fidelity"]["of"] += 1
        if raw_outcome(identity_fn, w) == raw_outcome(seq.detect_candidates, w):
            rep["execution_fidelity"]["identity_variant_equals_production_on"] += 1
        else:
            rep["execution_fidelity"]["mismatch"].append(name)
        if rep["execution_fidelity"]["mismatch"] or rep["execution_fidelity"]["namespace"]["identity_mismatches"]:
            rep["STOPPED"] = "IDENTITY CONTROL FAILED; transformed results are not interpreted (execution-fidelity problem)"
            print(rep["STOPPED"], flush=True)
            break
        rels = relations(prod, w, base, with_stochastic=True)
        rels.update(one_repeat_relations(w, base))
        for k, (v, d) in rels.items():
            tally(rel_sum, k, v)
        entry = {"candidates": len(base),
                 "relations": {k: {"verdict": v, **d} for k, (v, d) in rels.items()},
                 "ROLL": roll_info(w, base)}
        if not a.no_persistence:
            pr = persistence_relations(w, base)
            entry["persistence"] = {str(i): {k: {"verdict": v, **d} for k, (v, d) in c.items()}
                                    for i, c in pr.items()}
            for c in pr.values():
                for k, (v, _) in c.items():
                    tally(pers_sum, k, v)
        if mut_fix < a.mutant_fixtures:
            mut_fix += 1
            for m, fn in variants.items():
                if fn is None:
                    continue
                mb = fn(w)
                mrels = relations(fn, w, mb, with_stochastic=True)
                mrels.update(one_repeat_relations(w, mb, fn))
                for k, (v, _) in mrels.items():
                    prod_v = rels[k][0]
                    # a relation can only "detect" a mutant if production satisfies it here
                    tally(mat[m], k, "MUTANT_DETECTED" if (v == V and prod_v == S) else
                          ("MUTANT_SURVIVED" if v in (S, A) else "NOT_COMPARABLE"))
        rep["fixtures"][name] = entry
        print(f"{name}: cands={len(base)} violated={[k for k, (v, _) in rels.items() if v == V]} "
              f"t={time.time()-t0:.0f}s", flush=True)
    rep["relation_summary"], rep["persistence_summary"], rep["mutant_matrix"] = rel_sum, pers_sum, mat
    rep["mutant_verdict"] = {
        m: ("MUTANT_DETECTED" if any(c.get("MUTANT_DETECTED") for c in mat[m].values())
            else "MUTANT_SURVIVED") for m in mat if variants[m] is not None}
    rep["protocol"] = {"tol_hz": TOL_HZ, "tol_db": TOL_DB, "ambiguity_upper": AMBIG,
                       "noise": [NOISE_REL, NOISE_BINS, NOISE_DB],
                       "one_repeat_replaced": [ONE_REPEAT_BINS, ONE_REPEAT_DB], "strong_db": STRONG_DB,
                       "scales": SCALES, "phases": PHASES, "shifts_bins": SHIFTS,
                       "mutations": {m: list(e) for m, e in MUTATIONS.items()}}
    rep["sequencer_revision"] = seq.SEQUENCER_REVISION
    rep["numpy"] = np.__version__
    rep["harness_sha256"] = hashlib.sha256(open(__file__, "rb").read()).hexdigest()
    rep["production_sha256"] = {f: hashlib.sha256(open(os.path.join(os.path.dirname(__file__), "..", f), "rb").read()).hexdigest()
                                for f in ("rf_spur_catalogue_sequencer.py", "rf_spur_retention_estimator.py")}
    text = json.dumps(rep, indent=1, default=str)
    if a.out:
        open(a.out, "w").write(text)
    print(json.dumps({k: rep[k] for k in ("execution_fidelity", "mutation_edits_unverified",
                                          "relation_summary", "persistence_summary",
                                          "mutant_verdict")}, indent=1))


if __name__ == "__main__":
    main()
