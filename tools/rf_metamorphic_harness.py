#!/usr/bin/env python3
"""Read-only metamorphic harness r2 for the §5.21 spur detector and persistence estimator.

REVISION r3.  History: r1 was interrupted (comparator O(N^2) in candidate count, stalled on a
139,958-candidate mutant); r2 fixed that and was interrupted on Dedirock because the one-repeat
worst-case check recomputed the whole-spectrum floor (a median over 8x524,288 values, 0.095 s) once
per strong candidate, about 3.9 hours for the same mutant.  r3 computes that floor once per fixture and
evaluates all strong bins in one vectorised pass.  The floor is defined from the ORIGINAL spectra, so it
is independent of both the candidate and the simulated loss of a repeat: caching it is exact, not an
approximation.  The r2 function is retained below as the reference for the equivalence controls only.
Both earlier sources are preserved in the evidence directory.

REVISION r4 adds the span-boundary discrimination protocol specified in docs/research/metamorphic_r4_span_boundary_design.md (PR #164):
  * the span oracle is the PRODUCTION predicate on the COMPUTED bin-centre frequency (abs(np.fft.fftfreq(n, 1/fs)[bin]) <= usable);
    no sub-bin substitution, no idealised frequency, no epsilon.  r3's applicability used the reported (sub-bin) frequency; r4 does not.
  * Layer A (grid/predicate, no detector), Layer B (boundary cases through the production extraction FUNCTION on the pinned fixtures,
    function-level, production length) and Layer B' (synthetic windows at non-production n; function-level) are recorded SEPARATELY.
  * a span-mutation credit requires the attribution gate: the target is present under the span-removed reference and the paired
    controls hold; a span mutant that is indistinguishable at a target is MUTANT_SURVIVED_EQUIVALENT_ON_GRID (no credit, no blame).
  * two new mutants, span_limit_widened and span_limit_strict.
  * no result produced here is production-path coverage; see test_rf_metamorphic_r4_production_path.py for the production-path test.

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

import argparse, bisect, collections, hashlib, inspect, json, math, os, sys, time
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import rf_spur_catalogue_sequencer as seq
from rf_spur_catalogue_sequencer import BIN_HZ, SAMPLE_RATE_HZ, usable_half_span_hz
from rf_spur_retention_estimator import measure_persistence
from rf_promotion_envelope import PLAN_PERSISTENCE_MARGIN_DB

REVISION = "metamorphic-harness.r4"
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
    "span_limit_widened": ("usable = usable_half_span_hz()", "usable = usable_half_span_hz() * 1.01"),
    "span_limit_strict": ("in_span = np.abs(freqs[1:-1]) <= usable", "in_span = np.abs(freqs[1:-1]) < usable"),
}
SPAN_MUTANTS = ("span_limit_removed", "span_limit_widened", "span_limit_strict")


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
        if span_checked and not span_ok(n, bin_map(b)):      # r4: bin-centre predicate (r3 used the reported frequency)
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
            if not span_ok(n, inv_bin_map(b)):                    # pre-image outside span: unobservable
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
    """R2 REFERENCE implementation, retained only for the r2-versus-r3 equivalence controls.
    Not used on the run path (it recomputes the floor on every call)."""
    vals = np.sort(spectra[:, bin_index])
    worst = np.sort(np.concatenate([[0.0], vals[:-1]]))
    floor = float(np.median(np.median(spectra, axis=0)))
    return 10.0 * math.log10(max(0.5 * (worst[3] + worst[4]), 1e-300) / floor)


def spectra_floor(spectra):
    """Median-of-median floor of the ORIGINAL spectra: one number per fixture (candidate-independent)."""
    return float(np.median(np.median(spectra, axis=0)))


def worst_case_excess_batch(spectra, bins, floor=None):
    """Vectorised, floor-once equivalent of the r2 reference for many bins.

    With the repeat powers at a bin sorted ascending (v0..v7), removing the strongest and adding a zero
    gives [0, v0..v6], whose 4th and 5th order statistics are v2 and v3.  The log is taken with math.log10
    per element (not np.log10) so the result, and therefore every >= headroom decision, is bit-identical to r2.
    """
    bins = np.asarray(bins, dtype=np.int64)
    if bins.size == 0:
        return []
    fl = spectra_floor(spectra) if floor is None else floor
    srt = np.sort(spectra[:, bins], axis=0)
    medians = (0.5 * (srt[2] + srt[3])).tolist()
    return [10.0 * math.log10(max(m, 1e-300) / fl) for m in medians]


def tolerant_bins(spectra, strong, floor=None):
    """Strong candidates whose independent worst-case excess stays >= headroom above the margin."""
    if not strong:
        return []
    ex = worst_case_excess_batch(spectra, [b[2] for b in strong], floor)
    return [b for b, e in zip(strong, ex) if e - PLAN_PERSISTENCE_MARGIN_DB >= ONE_REPEAT_HEADROOM_DB]


def one_repeat_relation(windows, base, detector):
    n = len(windows[0])
    hann = 0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(n) / (n - 1)))
    spectra = np.abs(np.fft.fft(np.stack(windows) * hann[None, :], axis=1)) ** 2
    strong = [b for b in base if b[1] >= STRONG_DB]
    tolerant = tolerant_bins(spectra, strong)
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



# ================================================================ r4: span-boundary discrimination
PROD_N = 524288
USABLE = float(usable_half_span_hz())
_GRID = {}


def grid(n):
    """The frequency grid exactly as detect_candidates builds it (same expression, same float arithmetic)."""
    g = _GRID.get(n)
    if g is None:
        g = _GRID[n] = np.fft.fftfreq(n, d=1.0 / SAMPLE_RATE_HZ)
    return g


def span_ok(n, b):
    """THE oracle: the production predicate on the COMPUTED bin-centre frequency. No epsilon, no reported frequency."""
    return bool(abs(grid(n)[b]) <= USABLE)


SPAN_PREDICATES = {                       # per variant, evaluated on the computed bin-centre value only
    "production": lambda f: bool(abs(f) <= USABLE),
    "span_limit_removed": lambda f: True,
    "span_limit_widened": lambda f: bool(abs(f) <= USABLE * 1.01),
    "span_limit_strict": lambda f: bool(abs(f) < USABLE),
}
BOUNDARY_TARGETS = (235929, 235930, 235931, 249037)         # production length; each used with its negative mirror n - T
EDGE_PAIR = (235929, 235930)                                # (last inside, first outside): the paired control
HEADROOM_DB = 6.0                                           # base excess must be >= margin + this for a candidate to anchor a case
NEAR_EDGE = 8                                               # bins kept clear of DC, Nyquist and other mapped candidates
OBSERVATION_BIN_LIST_CAP = 64                               # lists longer than this are recorded by count only (never truncated for judging)
SYNTHETIC_SPEC = {"tone": "integer-modulo exp(2j*pi*((b*m)%n)/n)", "amplitude": 0.5, "sigma": 0.01, "repeats": 8,
                  "seed_re_base": 1000, "seed_im_base": 2000}
SYNTHETIC_SPEC_SHA256 = hashlib.sha256(json.dumps(SYNTHETIC_SPEC, sort_keys=True).encode()).hexdigest()
BPRIME_PLAN = ((500000, (224999, 225000, 225001)),         # the required exact-boundary geometry (function-level, non-production n)
               (524280, (235925, 235926, 235927)))         # the floating-point diagnostic (equivalent for the strict mutant)


def layer_a():
    """Layer A: the grid and the predicate variants, no detector. A unit-level result only."""
    from fractions import Fraction
    out = {"layer": "A_PREDICATE_GRID", "detector_involved": False, "usable_hz": USABLE, "geometries": {}}
    for n in (PROD_N, 524280, 500000):
        f = grid(n); kc = int(round(USABLE * n / SAMPLE_RATE_HZ)); rows = []
        for k in range(kc - 2, kc + 3):
            rows.append({"bin": k, "computed_hz": repr(float(f[k])),
                         "ideal_minus_usable_hz": float(Fraction(k * SAMPLE_RATE_HZ, n) - Fraction(USABLE)),
                         "predicates": {v: p(f[k]) for v, p in SPAN_PREDICATES.items()}})
        ideal = [k for k in range(kc - 2, kc + 3) if Fraction(k * SAMPLE_RATE_HZ, n) == Fraction(USABLE)]
        out["geometries"][str(n)] = {
            "bin_spacing_hz": SAMPLE_RATE_HZ / n, "rows": rows, "ideal_on_boundary_bins": ideal,
            "computed_exactly_on_boundary": [k for k in ideal if float(f[k]) == USABLE],
            "production_length": n == PROD_N}
    return out


def case_preconditions(n, anchor_bin, target, mapped_bins):
    """None if the case is eligible, else the reason it is PRECONDITION_NOT_MET."""
    if (anchor_bin + ((target - anchor_bin) % n)) % n != target:
        return "TARGET_NOT_ACHIEVED"
    if not (NEAR_EDGE <= target <= n // 2 - NEAR_EDGE or n // 2 + NEAR_EDGE <= target <= n - NEAR_EDGE):
        return "TARGET_NEAR_DC_OR_NYQUIST"
    if any(not (1 <= m <= n - 2) for m in mapped_bins):
        return "MAPPED_BIN_OUTSIDE_SEARCH_RANGE"
    if any(min((m - target) % n, (target - m) % n) <= NEAR_EDGE for m in mapped_bins if m != target):
        return "COLLISION_WITH_OTHER_MAPPED_CANDIDATE"
    return None


def make_cases(n, base):
    """Boundary cases for the base candidates of one fixture: one per (eligible anchor, target, sign)."""
    cases = []
    eligible = [c for c in base if c[1] >= PLAN_PERSISTENCE_MARGIN_DB + HEADROOM_DB]
    for anchor in eligible:
        for t0 in BOUNDARY_TARGETS:
            for sign, target in (("+", t0), ("-", n - t0)):
                k = (target - anchor[2]) % n
                mapped = [(c[2] + k) % n for c in base]
                cases.append({"anchor_bin": anchor[2], "anchor_excess_db": anchor[1], "t0": t0, "sign": sign, "target": target,
                              "shift_bins": k, "mapped_bins": mapped,
                              "expected_bins": sorted(m for m in mapped if span_ok(n, m)),
                              "precondition": case_preconditions(n, anchor[2], target, mapped)})
    return cases


def observation(tuples, target):
    bins = sorted(c[2] for c in tuples)
    return {"n_candidates": len(bins), "bins": bins if len(bins) <= OBSERVATION_BIN_LIST_CAP else None,
            "target_present": target in set(bins)}


def verdict_vs_expected(obs, expected_bins):
    """Cardinality first (no matching), then exact bin-set equality."""
    if obs["n_candidates"] != len(expected_bins):
        return V
    return S if obs["bins"] == sorted(expected_bins) else V


def judge(variant, obs_by_variant, expected_bins, target, n, pair_ok):
    """Pure decision logic for one case. Returns (verdict, credit, attribution) for `variant`.

    Attribution gate: the target must be present under the span-removed reference (so every non-span rule admits it) and the paired
    control must hold. Without it NO variant receives credit or blame. A span mutant that is indistinguishable at this target from
    production is MUTANT_SURVIVED_EQUIVALENT_ON_GRID. Resource refusals never reach this function.
    """
    ref = obs_by_variant.get("span_limit_removed")
    attribution_ok = bool(ref is not None and ref["target_present"] and pair_ok)
    vd = verdict_vs_expected(obs_by_variant[variant], expected_bins)
    if variant == "production":
        return vd, None, attribution_ok
    pv = verdict_vs_expected(obs_by_variant["production"], expected_bins)
    if not attribution_ok:
        return vd, "NOT_COMPARABLE", attribution_ok
    f = float(grid(n)[target])
    if variant in SPAN_MUTANTS:
        if SPAN_PREDICATES[variant](f) == SPAN_PREDICATES["production"](f):
            return vd, "MUTANT_SURVIVED_EQUIVALENT_ON_GRID", attribution_ok
        if pv != S:
            return vd, "NOT_COMPARABLE", attribution_ok
        return vd, ("MUTANT_DETECTED" if vd == V else "MUTANT_SURVIVED"), attribution_ok
    return vd, "NOT_COMPARABLE", attribution_ok          # non-span mutants are not credited by the boundary relation


def run_boundary_cases(variant_fns, windows, base, n):
    """Layer B for one fixture. variant_fns maps variant name -> detector(windows)->tuples; must include production and span_limit_removed."""
    cases = make_cases(n, base); results = []
    obs_cache = {}
    for case in cases:
        rec = {k: case[k] for k in ("anchor_bin", "anchor_excess_db", "t0", "sign", "target", "shift_bins", "expected_bins")}
        rec["bin_centre_hz"] = repr(float(grid(n)[case["target"]]))
        rec["predicate_at_target"] = {v: p(float(grid(n)[case["target"]])) for v, p in SPAN_PREDICATES.items()}
        if case["precondition"]:
            rec["status"] = P; rec["reason"] = case["precondition"]; results.append(rec); continue
        shifted = t_shift(case["shift_bins"])(windows)
        obs = {v: observation(fn(shifted), case["target"]) for v, fn in variant_fns.items()}
        rec["observed"] = obs; rec["status"] = "EVALUATED"
        obs_cache[(case["anchor_bin"], case["sign"], case["t0"])] = (obs, case)
        results.append(rec)
    # paired control: the inside case of the same anchor and sign must be present in production AND under the removed reference
    for rec in results:
        if rec["status"] != "EVALUATED":
            continue
        inside = obs_cache.get((rec["anchor_bin"], rec["sign"], EDGE_PAIR[0]))
        is_edge = rec["t0"] in (EDGE_PAIR[0], EDGE_PAIR[1], 235931)
        pair_ok = (not is_edge) or bool(inside and inside[0]["production"]["target_present"]
                                         and inside[0]["span_limit_removed"]["target_present"])
        rec["pair_ok"] = pair_ok
        rec["judgments"] = {}
        for v in variant_fns:
            vd, credit, attr = judge(v, rec["observed"], rec["expected_bins"], rec["target"], n, pair_ok)
            rec["judgments"][v] = {"verdict": vd, "credit": credit}
            rec["attribution_ok"] = attr
        if not rec["attribution_ok"]:
            rec["status"] = P; rec["reason"] = "OTHER_RULE_OR_PAIR_CONTROL_FAILED: no variant receives credit or blame"
    return {"layer": "B_FUNCTION_LEVEL_PRODUCTION_LENGTH", "n": n, "production_path": False, "cases": results}


def synthetic_windows(n, b):
    m = np.arange(n, dtype=np.int64); tone = SYNTHETIC_SPEC["amplitude"] * np.exp(2j * np.pi * ((b * m) % n) / n)
    return [tone + SYNTHETIC_SPEC["sigma"] * (np.random.default_rng(SYNTHETIC_SPEC["seed_re_base"] + i).normal(size=n)
                                              + 1j * np.random.default_rng(SYNTHETIC_SPEC["seed_im_base"] + i).normal(size=n))
            for i in range(SYNTHETIC_SPEC["repeats"])]


def layer_b_prime(variant_fns, plan=BPRIME_PLAN):
    """Layer B': synthetic one-tone windows at NON-production n. Function-level only; never production-path coverage."""
    out = {"layer": "B_PRIME_FUNCTION_LEVEL_NON_PRODUCTION_LENGTH", "production_path": False,
           "synthetic_spec": SYNTHETIC_SPEC, "synthetic_spec_sha256": SYNTHETIC_SPEC_SHA256,
           "note": "acquisition refuses any window that is not byte-exact 524,288 samples; reported sub-bin frequencies at other n are not production values",
           "cases": []}
    for n, bins in plan:
        for b0 in bins:
            for sign, b in (("+", b0), ("-", n - b0)):
                w = synthetic_windows(n, b); f = float(grid(n)[b])
                expected = [b] if SPAN_PREDICATES["production"](f) else []
                obs = {v: observation(fn(w), b) for v, fn in variant_fns.items()}
                rec = {"n": n, "bin": b, "sign": sign, "bin_centre_hz": repr(f), "expected_bins": expected, "observed": obs,
                       "predicate_at_target": {v: p(f) for v, p in SPAN_PREDICATES.items()}, "judgments": {}, "production_length": n == PROD_N}
                for v in variant_fns:
                    vd, credit, attr = judge(v, obs, expected, b, n, True)
                    rec["judgments"][v] = {"verdict": vd, "credit": credit}; rec["attribution_ok"] = attr
                out["cases"].append(rec)
    return out


def span_variant_fns():
    """Detector callables (tuples out) for production and the three span mutants, each a single exact edit of the production source."""
    fns = {"production": prod}
    for name in SPAN_MUTANTS:
        fn = build_variant(MUTATIONS[name])
        if fn is None:
            raise SystemExit(f"mutation {name} does not match the production source exactly once")
        fns[name] = (lambda w, fn=fn: as_tuples(fn(w)))
    return fns


def boundary_matrix(layer_records):
    """Plain-text matrix: expected versus observed admission, and the credit each span variant earned, per case."""
    lines = []
    for lr in layer_records:
        lines.append(f"[{lr['layer']}]  production_path={lr.get('production_path')}")
        lines.append(f"{'n':>7s} {'target':>7s} {'sign':>4s} {'bin-centre Hz':>20s} {'expected':>8s}  " + "  ".join(f"{v[:18]:>26s}" for v in SPAN_PREDICATES))
        for c in lr["cases"]:
            n = c.get("n", lr.get("n"))
            tgt = c.get("target", c.get("bin"))
            if c.get("status") == P:
                lines.append(f"{n:7d} {tgt:7d} {c.get('sign', ''):>4s} {c['bin_centre_hz']:>20s}   PRECONDITION_NOT_MET: {c.get('reason')}")
                continue
            exp = "P" if tgt in c["expected_bins"] else "-"
            cells = []
            for v in SPAN_PREDICATES:
                if v not in c["observed"]:
                    cells.append(f"{'n/a':>26s}"); continue
                o = c["observed"][v]["target_present"]; j = c["judgments"][v]
                cells.append(f"{('P' if o else '-') + ' ' + j['verdict'][9:12] + ' ' + str(j['credit'] or '')[:18]:>26s}")
            lines.append(f"{n:7d} {tgt:7d} {c.get('sign', ''):>4s} {c['bin_centre_hz']:>20s} {exp:>8s}  " + "  ".join(cells))
        lines.append("")
    return "\n".join(lines)


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
        raise SystemExit(f"results dir {rd} is not empty; the harness never overwrites earlier evidence")
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
                           "mutations": {m: list(e) for m, e in MUTATIONS.items()},
                           "r4": {"oracle": "production predicate on computed bin-centre frequency (no epsilon, no reported frequency)",
                                  "usable_hz": USABLE, "boundary_targets": list(BOUNDARY_TARGETS), "edge_pair": list(EDGE_PAIR),
                                  "headroom_db": HEADROOM_DB, "near_edge_bins": NEAR_EDGE, "span_mutants": list(SPAN_MUTANTS),
                                  "synthetic_spec_sha256": SYNTHETIC_SPEC_SHA256, "bprime_plan": [[n, list(b)] for n, b in BPRIME_PLAN],
                                  "layers_reported_separately": ["A", "B", "B_PRIME"], "boundary_only": bool(args.boundary_only)}}}
    durable_write(os.path.join(rd, "run_header.json"), header, mp)

    if not identity_phase(names, lambda n: load_windows(args.holdout, n), mp, rd):
        return 3
    print(f"(t={time.time()-t0:.0f}s)", flush=True)

    # ---- r4 Layer A (grid/predicate; no detector) and Layer B' (synthetic, function-level, non-production n). Separate, durable files.
    la = layer_a()
    durable_write(os.path.join(rd, "layerA.json"), la, mp)
    fns = span_variant_fns()
    lb_prime = layer_b_prime(fns)
    durable_write(os.path.join(rd, "layerBprime.json"), lb_prime, mp)
    print(boundary_matrix([lb_prime]), flush=True)
    os.makedirs(os.path.join(rd, "layerB"), exist_ok=True)

    # ---- r4 Layer B: boundary cases on the pinned fixtures (function-level, production length), one durable file per fixture
    layer_b_records = []
    for name in names:
        w = load_windows(args.holdout, name)
        base = prod(w)
        rec = run_boundary_cases(fns, w, base, len(w[0]))
        rec["fixture"] = name; rec["base_candidates"] = len(base)
        durable_write(os.path.join(rd, "layerB", f"{name}.json"), rec, mp)
        layer_b_records.append(rec)
        ev = [c for c in rec["cases"] if c["status"] == "EVALUATED"]
        print(f"B {name}: cases={len(rec['cases'])} evaluated={len(ev)} "
              f"production_violations={sum(1 for c in ev if c['judgments']['production']['verdict'] == V)} t={time.time()-t0:.0f}s", flush=True)
    if layer_b_records:
        print(boundary_matrix(layer_b_records[:1]), flush=True)
    if args.boundary_only:
        summarize(rd)
        return 0

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
    bd = {"layer_A": None, "layer_B_prime": None, "layer_B": {"fixtures": 0, "cases_evaluated": 0, "cases_precondition_not_met": 0,
                                                         "production_violations": 0, "credits": {}}}
    if os.path.exists(os.path.join(rd, "layerA.json")):
        la = json.load(open(os.path.join(rd, "layerA.json")))
        bd["layer_A"] = {n: {"computed_exactly_on_boundary": g["computed_exactly_on_boundary"],
                             "ideal_on_boundary_bins": g["ideal_on_boundary_bins"]} for n, g in la["geometries"].items()}
    if os.path.exists(os.path.join(rd, "layerBprime.json")):
        lp = json.load(open(os.path.join(rd, "layerBprime.json")))
        cr = collections.Counter()
        for c in lp["cases"]:
            for v, j in c["judgments"].items():
                if j["credit"]: cr[f"{v}:{j['credit']}"] += 1
        bd["layer_B_prime"] = {"production_path": False, "cases": len(lp["cases"]),
                               "production_violations": sum(1 for c in lp["cases"] if c["judgments"]["production"]["verdict"] == V),
                               "credits": dict(cr)}
    ldir = os.path.join(rd, "layerB")
    for fn in sorted(os.listdir(ldir)) if os.path.isdir(ldir) else []:
        e = json.load(open(os.path.join(ldir, fn))); bd["layer_B"]["fixtures"] += 1
        for c in e["cases"]:
            if c["status"] == "EVALUATED":
                bd["layer_B"]["cases_evaluated"] += 1
                bd["layer_B"]["production_violations"] += c["judgments"]["production"]["verdict"] == V
                for v, j in c["judgments"].items():
                    if j["credit"]: bd["layer_B"]["credits"][f"{v}:{j['credit']}"] = bd["layer_B"]["credits"].get(f"{v}:{j['credit']}", 0) + 1
            else:
                bd["layer_B"]["cases_precondition_not_met"] += 1
        bd["layer_B"]["production_path"] = False
    out["boundary"] = bd
    with open(os.path.join(rd, "summary.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(out, indent=1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout"); ap.add_argument("--hashes"); ap.add_argument("--results-dir")
    ap.add_argument("--limit", type=int, default=0); ap.add_argument("--no-persistence", action="store_true")
    ap.add_argument("--mutant-fixtures", type=int, default=6)
    ap.add_argument("--boundary-only", action="store_true",
                    help="run identity, Layer A, Layer B' and Layer B only (bounded validation); skips the r3 relation phases")
    ap.add_argument("--summarize")
    a = ap.parse_args()
    if a.summarize:
        summarize(a.summarize); return
    if not (a.holdout and a.hashes and a.results_dir):
        ap.error("--holdout, --hashes and --results-dir are required")
    raise SystemExit(run(a))


if __name__ == "__main__":
    main()
