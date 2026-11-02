#!/usr/bin/env python3
"""Experiment E preregistered evaluator: frozen parameters + the confirmatory tests + the verdict table.

PURPOSE  Implement, as code, the decision rules in prereg_v1.json so that the analysis cannot be tuned after
         seeing data. The evaluator refuses to run unless the SHA-256 of the canonical prereg JSON matches the
         value in a freeze record (PREREG_FREEZE.json, created by the owner at freeze time), or --allow-unfrozen
         is given (simulation only; output is stamped UNFROZEN).

INPUT    sweeps CSV   seq,fc_hz,f_hz,is_ref,p_dbm_dut,dut_rms_db,A_re,A_im,sigma2,hw_valid,alias_verdict
                      one row per dwell (A = complex lock-in amplitude at f_m, sigma2 = mean lock-in power of 20
                      off-frequency bins). is_ref=1 rows are probe-OFF baselines.
         controls CSV control_id,role(c3|sham),fc_hz,A_re,A_im,sigma2[,Ash_re,Ash_im,sigma2_sh]   (>= 10 rows per control)
         estimates    JSON {trial_id: c_hat_hz}      truths JSON {trial_id: fc_hz}   (Stage 4; optional)

OUTPUT   JSON with every statistic, p-value, gate and the verdict. Provenance is "simulated" when the input says so.

usage:   prereg.py verify PREREG.json FREEZE.json
         prereg.py evaluate --prereg P --freeze F --sweeps S.csv [--controls C.csv] [--estimates E.json --truths T.json] [--out R.json]
"""
import sys, os, json, csv, hashlib, argparse
import numpy as np
from statistics import NormalDist

HERE = os.path.dirname(os.path.abspath(__file__))


def canonical(o): return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
def sha256_text(s): return hashlib.sha256(s.encode()).hexdigest()


def load_prereg(path, freeze_path=None, allow_unfrozen=False):
    cfg = json.load(open(path)); h = sha256_text(canonical(cfg)); frozen = False
    if freeze_path and os.path.exists(freeze_path):
        fr = json.load(open(freeze_path))
        if fr.get("prereg_sha256") != h:
            raise SystemExit(f"REFUSED: prereg hash {h} does not match the freeze record {fr.get('prereg_sha256')}")
        frozen = True
    elif not allow_unfrozen:
        raise SystemExit("REFUSED: no freeze record; the decision rule is not frozen (use --allow-unfrozen for simulation only)")
    return cfg, h, frozen


# ---------------- data preparation ----------------
def read_csv(path):
    with open(path, newline="") as fh: rows = list(csv.DictReader(fh))
    cols = {k: [r[k] for r in rows] for k in rows[0]} if rows else {}
    out = {}
    for k, v in cols.items():
        try: out[k] = np.array([float(x) for x in v])
        except ValueError: out[k] = np.array(v)
    return out


def corrected_power(d): return d["A_re"] ** 2 + d["A_im"] ** 2 - d["sigma2"]


def prepare_fc(d, fc, cfg):
    """Apply the preregistered exclusions for one fc. Returns (f, p, stats)."""
    m = d["fc_hz"] == fc; seq = d["seq"][m]; order = np.argsort(seq)
    g = {k: v[m][order] for k, v in d.items()}
    ex = cfg["exclusions"]; probe = g["is_ref"] == 0
    alias_ok = np.isin(g["alias_verdict"], cfg["sets"]["alias_verdicts_allowed"]) | (g["is_ref"] == 1)
    n_alias_excluded = int((probe & ~alias_ok).sum())                  # decided a priori from the alias table
    n_tot = int((probe & alias_ok).sum())                             # alias-eligible dwells: the exclusion denominator
    valid = alias_ok.copy()
    valid &= g["hw_valid"] == 1
    med = np.median(g["p_dbm_dut"][probe & valid]) if (probe & valid).any() else 0.0
    valid &= (np.abs(g["p_dbm_dut"] - med) <= ex["power_tolerance_db"]) | (g["is_ref"] == 1)
    # drift: baselines (is_ref) bound blocks of probe dwells
    ref_idx = np.where(g["is_ref"] == 1)[0]
    drift_bad = np.zeros(len(seq), bool)
    if len(ref_idx):
        base = g["dut_rms_db"][ref_idx[0]]
        bad_ref = np.abs(g["dut_rms_db"][ref_idx] - base) > ex["reference_drift_db"]
        bounds = list(ref_idx)
        for i in np.where(probe)[0]:
            lo = max([b for b in bounds if b < i], default=None); hi = min([b for b in bounds if b > i], default=None)
            for b in (lo, hi):
                if b is not None and bad_ref[bounds.index(b)]: drift_bad[i] = True
        valid &= ~drift_bad
    use = probe & valid
    stats = dict(n_alias_excluded=n_alias_excluded, n_probe=n_tot, n_valid=int(use.sum()), excluded_fraction=1 - int(use.sum()) / max(n_tot, 1),
                 n_ref=int(len(ref_idx)), n_drift_excluded=int((probe & drift_bad).sum()))
    return g["f_hz"][use], corrected_power(g)[use], stats


def holm(pvals, alpha):
    """Holm step-down; pvals dict name->p. Returns dict name->(adjusted_threshold_passed bool)."""
    items = sorted(pvals.items(), key=lambda kv: kv[1]); m = len(items); res = {}; stop = False
    for i, (k, p) in enumerate(items):
        ok = (not stop) and p <= alpha / (m - i)
        if not ok: stop = True
        res[k] = bool(ok)
    return res


# ---------------- tests ----------------
def boot_p_positive(x, B, rng):
    """one-sided percentile bootstrap p for mean(x) <= 0"""
    x = np.asarray(x, float)
    if len(x) < 2: return 1.0
    idx = rng.integers(0, len(x), (B, len(x))); mu = x[idx].mean(1)
    return float((1 + (mu <= 0).sum()) / (B + 1))


def boot_bounds(x, B, rng, level):
    x = np.asarray(x, float); idx = rng.integers(0, len(x), (B, len(x))); mu = x[idx].mean(1)
    return float(np.quantile(mu, 1 - level)), float(np.quantile(mu, level))


def sets_for(f, fc, cfg):
    s = cfg["sets"]; d = np.abs(f - fc)
    return d <= s["in_band_halfwidth_hz"], d >= s["out_band_min_distance_hz"]


def test_c1(f, p, fc, cfg, rng):
    fin, fout = sets_for(f, fc, cfg)
    if fin.sum() < 3 or fout.sum() < 10: return dict(p=1.0, p_nonselective=1.0, T=float("nan"), n_in=int(fin.sum()), n_out=int(fout.sum()), note="too few dwells")
    k = 10 ** (cfg["tests"]["selectivity_db"] / 10); B = cfg["tests"]["bootstrap_B"]
    pin, pout = p[fin], p[fout]
    ii = rng.integers(0, len(pin), (B, len(pin))); oo = rng.integers(0, len(pout), (B, len(pout)))
    Tb = pin[ii].mean(1) - k * pout[oo].mean(1)
    T = float(pin.mean() - k * pout.mean())
    return dict(p=float((1 + (Tb <= 0).sum()) / (B + 1)), p_nonselective=float((1 + (Tb >= 0).sum()) / (B + 1)), T=T, mean_in=float(pin.mean()), mean_out=float(pout.mean()),
                n_in=int(fin.sum()), n_out=int(fout.sum()),
                observed_selectivity_db=float(10 * np.log10(pin.mean() / pout.mean())) if pin.mean() > 0 and pout.mean() > 0 else None)


def center_grid(f, cfg, step):
    h = cfg["design"]["ddc_passband_hz"] / 2; return np.arange(f.min() + h, f.max() - h + 1, step)


def weight_matrix(f, grid, cfg):
    """Matched boxcar of the DDC passband width: +1/n_in inside, -1/n_out outside, no guard band."""
    h = cfg["design"]["ddc_passband_hz"] / 2; d = np.abs(f[None, :] - grid[:, None])
    IN, OUT = d <= h, d > h
    nin, nout = IN.sum(1), OUT.sum(1); good = (nin > 0) & (nout > 0)
    W = np.where(IN, 1.0 / np.maximum(nin, 1)[:, None], 0) - np.where(OUT, 1.0 / np.maximum(nout, 1)[:, None], 0)
    return W[good], grid[good]


def estimate_center(f, p, cfg, step):
    W, g = weight_matrix(f, center_grid(f, cfg, step), cfg)
    if len(g) == 0: return float("nan"), float("nan")
    S = W @ p; i = int(np.argmax(S)); return float(g[i]), float(S[i])


def test_c2_fc(f, p, cfg, rng):
    step = 50_000; W, g = weight_matrix(f, center_grid(f, cfg, step), cfg)
    if len(g) == 0: return dict(p=1.0, c_hat=float("nan"))
    S = W @ p; i = int(np.argmax(S)); B = cfg["tests"]["permutation_B"]
    P = rng.permuted(np.tile(p, (B, 1)), axis=1).T            # F x B
    smax = (W @ P).max(0)
    return dict(p=float((1 + (smax >= S[i]).sum()) / (B + 1)), c_hat=float(g[i]), S_max=float(S[i]))


def test_c5(est, truth, cfg, rng):
    ids = sorted(set(est) & set(truth)); e = np.array([est[i] for i in ids]); t = np.array([truth[i] for i in ids])
    st = cfg["stage4"]; mae = float(np.mean(np.abs(e - t))); within = float(np.mean(np.abs(e - t) <= st["stage4_within_hz"]))
    B = cfg["tests"]["stage4_permutation_B"]
    perm = rng.permuted(np.tile(t, (B, 1)), axis=1)
    mae_b = np.abs(perm - e[None, :]).mean(1)
    p = float((1 + (mae_b <= mae).sum()) / (B + 1))
    return dict(p=p, n=len(ids), mae_hz=mae, within_fraction=within,
                floors_ok=bool(len(ids) >= st["n_trials_min"] and mae <= st["stage4_mae_floor_hz"] and within >= st["stage4_within_fraction"]))


def evaluate(sweeps, controls, est, truth, cfg, cfg_hash, frozen, provenance="unspecified", schedule_ok=True, blinding=None):
    rng = np.random.default_rng(cfg["rng_seed"]); fcs = [float(x) for x in cfg["design"]["fc_hz"]]
    R = dict(prereg_sha256=cfg_hash, frozen=frozen, provenance=provenance, profile=cfg.get("profile", "v1 (unprofiled)"), per_fc={}, gates={}, tests={})
    if not frozen: R["WARNING"] = "UNFROZEN decision rule: simulation only, not a result"
    data = {}
    for fc in fcs:
        if (sweeps["fc_hz"] == fc).any():
            f, p, st = prepare_fc(sweeps, fc, cfg); data[fc] = (f, p); R["per_fc"][fc] = dict(exclusions=st)
    ex = cfg["exclusions"]
    def short(fc):
        f = data[fc][0]; fin, fout = sets_for(f, fc, cfg); return fin.sum() < ex["min_valid_in"] or fout.sum() < ex["min_valid_out"]
    incomplete = (len(data) < len(fcs)) or any(R["per_fc"][fc]["exclusions"]["excluded_fraction"] > ex["max_excluded_fraction"] or short(fc) for fc in data)
    if not schedule_ok: R["verdict"] = "INCONCLUSIVE_PROTOCOL"; return R
    if incomplete: R["verdict"] = "INCONCLUSIVE_INCOMPLETE"; return R
    nfc = len(fcs); a_det = cfg["gates"]["detection_alpha_total"] / nfc; B = cfg["tests"]["bootstrap_B"]
    det = {}; c1 = {}; c2 = {}
    for fc, (f, p) in data.items():
        fin, _ = sets_for(f, fc, cfg); pd = boot_p_positive(p[fin], B, rng)
        det[fc] = pd <= a_det
        nd = NormalDist(); sd_in = float(p[fin].std(ddof=1)) if fin.sum() > 2 else float("nan")
        lim = (nd.inv_cdf(1 - a_det) + nd.inv_cdf(0.8)) * sd_in / np.sqrt(max(int(fin.sum()), 1))     # minimum in-band power detectable with 80 % power
        s2_hat = float(p[fin].mean() / np.mean(sweeps["sigma2"][sweeps["fc_hz"] == fc])) if fin.any() else None
        R["per_fc"][fc]["detection"] = dict(p=pd, detected=bool(det[fc]), mean_in=float(p[fin].mean()) if fin.any() else None,
                                            detection_limit_power=float(lim), s2_hat=s2_hat,
                                            below_sensitivity_requirement=bool(s2_hat is None or s2_hat < cfg["sensitivity"]["min_in_band_power_over_noise"]))
        c1[fc] = test_c1(f, p, fc, cfg, rng); c2[fc] = test_c2_fc(f, p, cfg, rng)
        R["per_fc"][fc]["C1"] = c1[fc]; R["per_fc"][fc]["C2_perm"] = c2[fc]
    R["gates"]["detected_any"] = bool(any(det.values()))
    # --- controls: PASS / FAIL / UNRESOLVED (an unresolved control is never a pass)
    lvl = cfg["gates"]["control_ci_level"]; frac = cfg["gates"]["control_max_fraction_of_signal"]; R["gates"]["controls"] = {}
    if controls is not None:
        for cid in sorted(set(controls["control_id"])):
            m = controls["control_id"] == cid; role = str(controls["role"][m][0]); fc = float(controls["fc_hz"][m][0])
            sub = {k: v[m] for k, v in controls.items()}; pw = corrected_power(sub); lo, hi = boot_bounds(pw, B, rng, lvl)
            sig = R["per_fc"].get(fc, {}).get("detection", {}).get("mean_in") or 0.0
            thr = frac * max(sig, 0.0)
            if role == "c3":
                if det.get(fc): state = "pass" if hi < thr else ("fail" if lo >= thr else "unresolved")
                else: state = "pass" if lo <= 0 else "fail"
                R["gates"]["controls"][cid] = dict(role=role, state=state, lower=lo, upper=hi, threshold=thr, n=int(m.sum()))
            else:
                if not det.get(fc): R["gates"]["controls"][cid] = dict(role=role, state="pass", note="not applicable: no detection at this fc"); continue
                psh = sub["Ash_re"] ** 2 + sub["Ash_im"] ** 2 - sub["sigma2_sh"]; slo, shi = boot_bounds(psh, B, rng, lvl)
                thr_sh = frac * max(psh.mean(), 0.0)
                if lo >= thr_sh and psh.mean() > 0: state = "fail"
                elif slo > 0 and hi < thr_sh: state = "pass"
                else: state = "unresolved"
                R["gates"]["controls"][cid] = dict(role=role, state=state, sham_lower=slo, fm_lower=lo, fm_upper=hi, sham_mean=float(psh.mean()), n=int(m.sum()))
    states = [v["state"] for v in R["gates"]["controls"].values()]
    R["gates"]["controls_ok"] = all(s == "pass" for s in states)
    if "fail" in states: R["verdict"] = "CONTROLS_FAIL"; return R
    if "unresolved" in states: R["verdict"] = "INCONCLUSIVE_CONTROLS"; return R
    if not R["gates"]["detected_any"]: R["verdict"] = "H0a_NO_RESPONSE"; R["note"] = "report the detection limit from the noise floor (see EXPERIMENT_E_PREREGISTRATION.md)"; return R
    # --- confirmatory family (Holm, fixed size 3)
    pC1 = max(c1[fc]["p"] for fc in fcs); pC2 = max(c2[fc]["p"] for fc in fcs)
    chat = {fc: c2[fc]["c_hat"] for fc in fcs}; tol = cfg["tests"]["center_shift_tolerance_hz"]
    pair_err = {f"{a:.0f}->{b:.0f}": float(abs((chat[b] - chat[a]) - (b - a))) for i, a in enumerate(fcs) for b in fcs[i + 1:]}
    c2_shift_ok = all(v <= tol for v in pair_err.values())
    pC5 = 1.0; c5 = None
    if est is not None and truth is not None:
        c5 = test_c5(est, truth, cfg, np.random.default_rng(cfg["rng_seed"] + 1)); pC5 = c5["p"]
    hm = holm({"C1": pC1, "C2": pC2, "C5": pC5}, cfg["tests"]["family_alpha"])
    c1_pass = hm["C1"]; c2_pass = hm["C2"] and c2_shift_ok; c5_pass = bool(c5 and hm["C5"] and c5["floors_ok"])
    blind_ok = bool(blinding and blinding.get("verified"))
    R["stage4_blinding"] = dict(mode=(blinding or {}).get("mode"), verified=blind_ok)
    if c5_pass and not blind_ok:
        R["stage4_blinding"]["note"] = "C5 passed but the Stage 4 blinding was not verified, so it cannot support the blinded verdict (rung 5)"
        c5_pass = False
    R["tests"] = dict(C1=dict(p=pC1, passed=bool(c1_pass)), C2=dict(p=pC2, holm=hm["C2"], center_shift_errors_hz=pair_err, shift_ok=bool(c2_shift_ok), passed=bool(c2_pass)),
                      C5=dict(c5 or {}, passed=c5_pass), holm=hm)
    if c1_pass and c2_pass: R["verdict"] = "H1_SUPPORTED_RUNG5" if c5_pass else "H1_SUPPORTED_RUNG4"
    else:
        dfc = [fc for fc in fcs if det[fc]]
        affirmative = bool(dfc) and all(c1[fc]["p_nonselective"] <= a_det for fc in dfc)
        R["tests"]["H0b_affirmative"] = dict(per_fc={fc: c1[fc]["p_nonselective"] for fc in dfc}, alpha=a_det, ok=affirmative)
        R["verdict"] = "H0b_NOT_SELECTIVE" if affirmative else "INCONCLUSIVE"
    return R


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["verify", "evaluate", "freeze"]); ap.add_argument("--owner"); ap.add_argument("pos", nargs="*")
    ap.add_argument("--prereg", default=os.path.join(HERE, "prereg_v1.json")); ap.add_argument("--freeze", default=os.path.join(HERE, "PREREG_FREEZE.json"))
    ap.add_argument("--allow-unfrozen", action="store_true"); ap.add_argument("--sweeps"); ap.add_argument("--controls")
    ap.add_argument("--schedule"); ap.add_argument("--reveal-record"); ap.add_argument("--commitment"); ap.add_argument("--estimates"); ap.add_argument("--truths"); ap.add_argument("--provenance", default="recorded"); ap.add_argument("--out")
    a = ap.parse_args()
    if a.cmd == "freeze":
        if os.path.exists(a.freeze): sys.exit(f"REFUSED: {a.freeze} already exists; a frozen rule is never re-frozen (write prereg_v2.json instead)")
        if not a.owner: sys.exit("--owner is required: the freeze is the owner's decision")
        import time
        h = sha256_text(canonical(json.load(open(a.prereg))))
        json.dump(dict(prereg_sha256=h, prereg_file=os.path.basename(a.prereg), owner=a.owner,
                       frozen_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())), open(a.freeze, "w"), indent=1)
        print("frozen", h, "-> commit", a.freeze, "BEFORE the first DUT run"); return
    if a.cmd == "verify":
        cfg, h, fr = load_prereg(a.prereg, a.freeze, a.allow_unfrozen); print(("FROZEN " if fr else "UNFROZEN ") + h); return
    cfg, h, fr = load_prereg(a.prereg, a.freeze, a.allow_unfrozen)
    sw = read_csv(a.sweeps); sched_ok = True
    if a.schedule:
        sys.path.insert(0, HERE); import blind
        sched_ok = blind.schedule_matches(sw, json.load(open(a.schedule)))
    blinding = None
    if a.reveal_record:
        sys.path.insert(0, HERE); import blind
        rec = json.load(open(a.reveal_record)); com = json.load(open(a.commitment)) if a.commitment else None
        blinding = dict(mode=rec.get("mode"), verified=bool(a.estimates and a.truths and blind.verify_record(rec, a.estimates, a.truths, com)))
    R = evaluate(sw, read_csv(a.controls) if a.controls else None,
                 json.load(open(a.estimates)) if a.estimates else None, json.load(open(a.truths)) if a.truths else None,
                 cfg, h, fr, a.provenance, schedule_ok=sched_ok, blinding=blinding)
    s = json.dumps(R, indent=1, default=str)
    if a.out: open(a.out, "w").write(s)
    print("verdict:", R["verdict"], "| frozen" if fr else "| UNFROZEN (simulation only)")


if __name__ == "__main__":
    main()
