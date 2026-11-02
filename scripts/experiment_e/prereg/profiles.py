#!/usr/bin/env python3
"""Generic option sets for the open preregistration decisions, built into validated rule files.

A profile is a named bundle of choices for the decisions the owner has to make before freezing. Building one writes a
complete rule file (same schema as prereg_v1.json) that prereg.py can freeze and evaluate. The default is `standard`.
Nothing here freezes anything; freezing is `prereg.py freeze` and stays the owner's act.

  standard    the default. Confirmatory-grade: family alpha 0.01, 10 dB selectivity, controls shown below -20 dB.
  pragmatic   cheaper first look: alpha 0.05, 6 dB, controls below -15 dB, looser tolerances, lower sensitivity bar.
              A pragmatic result is a preregistered pilot-grade result and must be reported as such.
  stringent   for a claim to be relied on or published: alpha 0.001, tighter everything, more SNR, 30 trials.

Single decisions can be overridden on top of a profile (--set path=value, JSON values), and the centre frequencies have
named alternatives (--fc-set). Every build is validated: an internally inconsistent rule (plateau outside the sweep,
overlapping fc, p-value resolution too coarse for the alpha, sets that cannot be filled, ...) is refused.

usage: profiles.py list
       profiles.py build NAME [--fc-set A|B|C] [--set tests.selectivity_db=8 ...] [--out prereg_NAME.json]
       profiles.py budget NAME [--n 12]
"""
import sys, os, json, copy, math, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, "prereg_v1.json")

FC_SETS = {
    "A": ([3000000, 7500000, 12000000], "default: 4.5 MHz apart, each plateau fully inside the 0.2-14 MHz sweep with room to spare"),
    "B": ([4000000, 8000000, 12000000], "equal 4 MHz spacing; the 12 MHz plateau ends at 13.02 MHz, close to the 14 MHz sweep end"),
    "C": ([2500000, 7000000, 11500000], "4.5 MHz apart, shifted lower; keeps the highest plateau well inside the sweep"),
}

PROFILES = {
    "standard": dict(
        use="Confirmatory-grade default.",
        set={}),
    "pragmatic": dict(
        use="Cheaper first look. Preregistered, but pilot-grade: do not cite as confirmation.",
        set={"tests.family_alpha": 0.05, "gates.detection_alpha_total": 0.05, "tests.selectivity_db": 6.0,
             "gates.control_max_fraction_of_signal": 0.03, "gates.control_ci_level": 0.95,
             "tests.center_shift_tolerance_hz": 500000, "exclusions.reference_drift_db": 3.0, "exclusions.power_tolerance_db": 2.0,
             "sensitivity.min_in_band_power_over_noise": 4.0,
             "stage4.stage4_mae_floor_hz": 750000, "stage4.stage4_within_fraction": 0.70}),
    "stringent": dict(
        use="For a claim to be relied on or published. Needs more SNR and more control time.",
        set={"tests.family_alpha": 0.001, "gates.detection_alpha_total": 0.001, "gates.control_max_fraction_of_signal": 0.003,
             "gates.control_ci_level": 0.995, "exclusions.reference_drift_db": 1.0,
             "exclusions.power_tolerance_db": 0.5, "sensitivity.min_in_band_power_over_noise": 16.0,
             "stage4.n_trials_min": 30, "stage4.stage4_mae_floor_hz": 250000, "stage4.stage4_within_fraction": 0.90,
             "tests.bootstrap_B": 50000, "tests.permutation_B": 20000, "tests.stage4_permutation_B": 300000}),
}

# one line per decision: what the options mean, so the owner can mix a profile with single overrides
DECISIONS = [
    ("design.fc_hz", "DDC centres", "A 3.0/7.5/12.0 MHz (default); B 4/8/12; C 2.5/7/11.5. Rule: >= 3 MHz apart, plateau inside the sweep"),
    ("tests.selectivity_db", "selectivity to certify", "6 (pragmatic) / 10 (default) / 15. It is a margin test: the true value must exceed it clearly"),
    ("tests.family_alpha", "family-wise alpha", "0.05 (pragmatic) / 0.01 (default) / 0.001 (stringent)"),
    ("gates.control_max_fraction_of_signal", "control must be below", "0.03 = -15 dB / 0.01 = -20 dB (default) / 0.003 = -25 dB; cost scales as 1/value"),
    ("tests.center_shift_tolerance_hz", "C2 centre-shift tolerance", "500 kHz / 250 kHz (default). Must be >= 2.5x the sweep step; going tighter needs a finer step (50 kHz doubles the sweep time)"),
    ("stage4.stage4_mae_floor_hz", "Stage 4 MAE floor", "750 kHz / 500 kHz (default) / 250 kHz, with 70% / 80% / 90% within half a passband"),
    ("exclusions.reference_drift_db", "baseline drift tolerance", "3.0 / 1.5 (default) / 1.0 dB"),
    ("sensitivity.min_in_band_power_over_noise", "required in-band power / noise", "4 (pilot-grade, ~27% power) / 8 (default, ~95%) / 16"),
]


def _get(c, path):
    for k in path.split("."): c = c[k]
    return c


def _set(c, path, v):
    ks = path.split(".")
    for k in ks[:-1]: c = c[k]
    if ks[-1] not in c: raise KeyError(f"unknown parameter {path}")
    c[ks[-1]] = v


def validate(c):
    """Return a list of problems; empty means internally consistent."""
    P = []; d = c["design"]; fcs = d["fc_hz"]; bw = d["ddc_passband_hz"]; lo, hi, step = d["sweep_hz"]
    s = c["sets"]; t = c["tests"]; g = c["gates"]; e = c["exclusions"]; st = c["stage4"]
    if len(fcs) != 3: P.append("exactly 3 centre frequencies are required (the Holm family and the movement test assume 3)")
    for a, b in zip(sorted(fcs), sorted(fcs)[1:]):
        if b - a < 3e6: P.append(f"fc {a/1e6:g} and {b/1e6:g} MHz are less than 3 MHz apart; the plateaus would overlap with their guard bands")
    for f in fcs:
        if f - bw / 2 < lo or f + bw / 2 > hi: P.append(f"fc {f/1e6:g} MHz: the {bw/1e6:g} MHz plateau leaves the swept range")
    if not (0 < s["in_band_halfwidth_hz"] < bw / 2): P.append("in-band half-width must be inside the plateau")
    if s["out_band_min_distance_hz"] <= bw / 2: P.append("out-of-band distance must be beyond the plateau edge")
    n_in = int(2 * s["in_band_halfwidth_hz"] // step) + 1
    if n_in < e["min_valid_in"]: P.append(f"the in-band set has only {n_in} dwells, below min_valid_in {e['min_valid_in']}")
    for fc in fcs:
        n_out = int(((fc - s["out_band_min_distance_hz"]) - lo) // step + 1) + int((hi - (fc + s["out_band_min_distance_hz"])) // step + 1)
        if n_out < e["min_valid_out"] / (1 - 0.3): P.append(f"fc {fc/1e6:g} MHz: only {n_out} out-of-band dwells, too few for min_valid_out {e['min_valid_out']} after alias exclusions")
    if t["center_shift_tolerance_hz"] < 2.5 * step: P.append(f"C2 tolerance {t['center_shift_tolerance_hz']/1e3:g} kHz is below 2.5x the sweep step ({step/1e3:g} kHz): centre estimates are quantised, so it could not be met even without noise")
    if not (0 < t["family_alpha"] <= 0.1): P.append("family_alpha must be in (0, 0.1]")
    if not (0 < g["detection_alpha_total"] <= 0.1): P.append("detection_alpha_total must be in (0, 0.1]")
    if t["selectivity_db"] <= 0: P.append("selectivity_db must be positive")
    if not (0 < g["control_max_fraction_of_signal"] <= 0.5): P.append("control fraction must be in (0, 0.5]")
    if not (0.5 < g["control_ci_level"] < 1): P.append("control_ci_level must be in (0.5, 1)")
    fam = t["holm_family_size"]
    for name, B, a in (("bootstrap_B", t["bootstrap_B"], t["family_alpha"] / fam), ("permutation_B", t["permutation_B"], t["family_alpha"] / fam),
                       ("stage4_permutation_B", t["stage4_permutation_B"], t["family_alpha"] / fam),
                       ("bootstrap_B (detection)", t["bootstrap_B"], g["detection_alpha_total"] / len(fcs))):
        if 1 / (B + 1) > a / 3: P.append(f"{name}={B}: p-value resolution {1/(B+1):.1e} is too coarse for the Holm-adjusted alpha {a:.1e} (need at least 3 steps below it)")
    if st["n_trials_min"] < 12: P.append("Stage 4 needs at least 12 trials for any permutation test to reach small alpha")
    if st["sweep_hz"][2] > bw / 4: P.append("Stage 4 step must be no coarser than a quarter of the passband or the plateau is under-sampled")
    if st["stage4_within_hz"] <= 0 or not (0 < st["stage4_within_fraction"] <= 1): P.append("Stage 4 within-fraction floors are malformed")
    for k in ("power_tolerance_db", "reference_drift_db"):
        if e[k] <= 0: P.append(f"{k} must be positive")
    if c["sensitivity"]["min_in_band_power_over_noise"] <= 0: P.append("sensitivity requirement must be positive")
    return P


def build(name, fc_set="A", overrides=None):
    if name not in PROFILES: raise SystemExit(f"unknown profile {name!r}; choose from {sorted(PROFILES)}")
    c = json.load(open(BASE)); c = copy.deepcopy(c)
    for path, v in PROFILES[name]["set"].items(): _set(c, path, v)
    c["design"]["fc_hz"] = FC_SETS[fc_set][0]
    for path, v in (overrides or {}).items(): _set(c, path, v)
    c["profile"] = name; c["profile_use"] = PROFILES[name]["use"]; c["fc_set"] = fc_set
    if overrides: c["profile_overrides"] = overrides
    probs = validate(c)
    if probs: raise SystemExit("REFUSED, inconsistent rule:\n  - " + "\n  - ".join(probs))
    return c


def control_budget(c, n=12, margin=11.0):
    """Control dwell length k (x the sweep dwell) so that k*sqrt(n) >= margin / (fraction * s2). margin=11 resolved 100% of
    clean controls in simulation (5.5 resolved about 80%). Returns (k, hours per control, hours for 3 controls)."""
    frac = c["gates"]["control_max_fraction_of_signal"]; s2 = c["sensitivity"]["min_in_band_power_over_noise"]
    k = math.ceil(margin / (frac * s2 * math.sqrt(n))); hours = n * k * c["design"]["dwell_s"] / 3600
    return k, hours, 3 * hours


def sweep_hours(c):
    lo, hi, step = c["design"]["sweep_hz"]; n = int((hi - lo) // step) + 1
    return n * c["design"]["dwell_s"] * (1 + 1 / c["design"]["reference_every_n_steps"]) / 3600


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["list", "build", "budget"]); ap.add_argument("name", nargs="?", default="standard")
    ap.add_argument("--fc-set", default="A", choices=sorted(FC_SETS)); ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--out"); ap.add_argument("--n", type=int, default=12)
    a = ap.parse_args()
    if a.cmd == "list":
        for k, v in PROFILES.items(): print(f"{k:10s} {v['use']}")
        print("\nfc sets:"); [print(f"  {k}: {' / '.join(f'{x/1e6:g}' for x in v[0])} MHz  {v[1]}") for k, v in FC_SETS.items()]
        print("\ndecisions you can override with --set:"); [print(f"  {p:42s} {d}: {o}") for p, d, o in DECISIONS]
        return
    ov = {}
    for s in a.set:
        k, v = s.split("=", 1); ov[k] = json.loads(v)
    c = build(a.name, a.fc_set, ov)
    if a.cmd == "budget":
        k, h, h3 = control_budget(c, a.n)
        print(f"{a.name}: sweep {sweep_hours(c):.1f} h per fc ({3*sweep_hours(c):.1f} h for 3); controls need {a.n} dwells of {k}x the sweep dwell = {h:.1f} h each, {h3:.1f} h for 3")
        return
    out = a.out or os.path.join(HERE, f"prereg_{a.name}.json")
    json.dump(c, open(out, "w"), indent=1, sort_keys=True); open(out, "a").write("\n")
    print("wrote", out, "| sha256 of canonical rule:", __import__("hashlib").sha256(json.dumps(c, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16], "...")


if __name__ == "__main__":
    main()
