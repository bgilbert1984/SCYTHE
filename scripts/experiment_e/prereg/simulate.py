#!/usr/bin/env python3
"""Synthetic lock-in sweeps for characterising the preregistered procedure. PROVENANCE: simulated.

Model per dwell: complex lock-in amplitude A = g * sqrt(S(f)) * e^{j phi} + n, n ~ CN(0, 1) (E|n|^2 = 1), and a
noise-floor estimate sigma2 ~ Gamma(20, 1/20) (mean 1, from 20 off-frequency bins). "s2" below is the in-band signal
power in units of the per-dwell noise power (corrected power S/sigma^2); it is the one hardware-dependent number the
bench will have to supply, which is why the characterisation is reported against it.

Scenarios   H1   in-band response s2 (|f-fc| <= passband/2), out-of-band leak at -leak_db
            H0a  no response anywhere
            H0b  response flat in frequency (ADC front-end leaks regardless of the DDC)
            H0c  fixed-in-frequency bump independent of fc (coupling into the measurement chain)
Nuisances   alias-excluded out-of-band dwells (15 %), 1 % invalid dwells, +-0.2 dB power jitter, optional slow
            gain drift on baselines and signal, randomised order with a probe-off baseline every 10 dwells.
"""
import sys, os, zlib, numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import prereg as pr

PASSBAND = 2_048_000


def _signal(scn, f, fc, s2, leak_db):
    if scn == "H0a": return np.zeros_like(f)
    if scn == "H0b": return np.full_like(f, s2)
    if scn == "H0c": return s2 * np.exp(-((f - 7.0e6) / 2.5e6) ** 2)
    return np.where(np.abs(f - fc) <= PASSBAND / 2, s2, s2 * 10 ** (-leak_db / 10))


def make_sweeps(rng, scn, s2=4.0, leak_db=25.0, fcs=(3.0e6, 7.5e6, 12.0e6), sweep=(200_000, 14_000_000, 100_000),
                drift_db=0.0, p_alias_bad=0.15, p_invalid=0.01, schedule=None, p_hw_bad=None):
    f_all = np.arange(sweep[0], sweep[1] + 1, sweep[2], dtype=float)
    cols = {k: [] for k in ("seq", "fc_hz", "f_hz", "is_ref", "p_dbm_dut", "dut_rms_db", "A_re", "A_im", "sigma2", "hw_valid", "alias_verdict")}
    for fc in fcs:
        if schedule is not None:
            plan = [("ref" if d["is_ref"] else "probe", d["f_hz"]) for d in sorted((d for d in schedule["dwells"] if d["fc_hz"] == fc), key=lambda d: d["seq"])]
        else:
            order = rng.permutation(len(f_all)); plan = [("ref", fc)]
            for i, k in enumerate(order):
                plan.append(("probe", f_all[k]))
                if (i + 1) % 10 == 0: plan.append(("ref", fc))
            plan.append(("ref", fc))
        T = len(plan)
        for seq, (kind, f) in enumerate(plan):
            drift = drift_db * seq / T; gain = 10 ** (drift / 20)
            S = 0.0 if kind == "ref" else float(_signal(scn, np.array([f]), fc, s2, leak_db)[0])
            amp = gain * np.sqrt(S) * np.exp(1j * rng.uniform(0, 2 * np.pi)); n = (rng.normal() + 1j * rng.normal()) / np.sqrt(2)
            A = amp + n
            near = abs(f - fc) <= PASSBAND / 2
            av = "IN_BAND" if near else ("CONTAMINATED" if (kind == "probe" and rng.random() < p_alias_bad) else "CLEAN_OUT")
            for k, v in (("seq", seq), ("fc_hz", fc), ("f_hz", f), ("is_ref", 1 if kind == "ref" else 0),
                         ("p_dbm_dut", -40.0 + rng.normal(0, 0.2)), ("dut_rms_db", drift + rng.normal(0, 0.01)),
                         ("A_re", A.real), ("A_im", A.imag), ("sigma2", rng.gamma(20, 1 / 20)),
                         ("hw_valid", 0 if rng.random() < (p_invalid if p_hw_bad is None else p_hw_bad) else 1), ("alias_verdict", av)):
                cols[k].append(v)
    return {k: np.array(v) for k, v in cols.items()}


def make_controls(rng, kind="clean", s2=4.0, fc=7.5e6, n=12, coupling_db=-35.0, sham_ok=True, k=40):
    """kind clean: controls respond at coupling_db below the in-band signal (default -35 dB); leaky: coupling -10 dB.
    k = control dwell length / sweep dwell length: noise power per dwell falls as 1/k (k=30 means 30 min dwells)."""
    rows = {k: [] for k in ("control_id", "role", "fc_hz", "A_re", "A_im", "sigma2", "Ash_re", "Ash_im", "sigma2_sh")}
    cdb = coupling_db if kind == "clean" else -10.0
    for cid, role in (("ctlA", "c3"), ("ctlB", "c3"), ("ctlS", "sham")):
        for _ in range(n):
            A = np.sqrt(s2 * 10 ** (cdb / 10)) * np.exp(1j * rng.uniform(0, 6.28)) + (rng.normal() + 1j * rng.normal()) / np.sqrt(2 * k)
            sh = (np.sqrt(s2) if (role == "sham" and sham_ok) else 0.0) * np.exp(1j * rng.uniform(0, 6.28)) + (rng.normal() + 1j * rng.normal()) / np.sqrt(2 * k)
            for kk, v in (("control_id", cid), ("role", role), ("fc_hz", fc), ("A_re", A.real), ("A_im", A.imag), ("sigma2", rng.gamma(20, 1 / (20 * k))),
                         ("Ash_re", sh.real), ("Ash_im", sh.imag), ("sigma2_sh", rng.gamma(20, 1 / (20 * k)))):
                rows[kk].append(v)
    return {k: np.array(v) for k, v in rows.items()}


def make_trials(rng, cfg, n=20, s2=4.0, scn="H1", leak_db=25.0):
    """Stage 4: secret fc per trial, coarse sweep; returns (estimates, truths). s2 is the 60 s figure; 30 s dwells give s2/2."""
    st = cfg["stage4"]; lo, hi = st["fc_range_hz"]; g = st["fc_granularity_hz"]
    sw = tuple(st["sweep_hz"]); est, truth = {}, {}
    for j in range(n):
        fc = float(rng.integers(int(lo // g), int(hi // g) + 1) * g)
        f = np.arange(sw[0], sw[1] + 1, sw[2], dtype=float)
        S = _signal(scn, f, fc, s2 / 2, leak_db)
        A = np.sqrt(S) * np.exp(1j * rng.uniform(0, 6.28, len(f))) + (rng.normal(size=len(f)) + 1j * rng.normal(size=len(f))) / np.sqrt(2)
        p = np.abs(A) ** 2 - rng.gamma(20, 1 / 20, len(f))
        c, _ = pr.estimate_center(f, p, cfg, cfg["estimators"]["stage4_center_grid_hz"])
        est[f"t{j:02d}"], truth[f"t{j:02d}"] = c, fc
    return est, truth


def characterise(n_sims=100, s2_list=(0.25, 0.5, 1, 2, 4, 8), log=print, cfg_path=None):
    cfg, h, fr = pr.load_prereg(cfg_path or os.path.join(HERE, "prereg_v1.json"), None, True)
    rows = []
    for scn in ("H0a", "H0b", "H0c", "H1"):
        for s2 in ((0.0,) if scn == "H0a" else s2_list):
            cnt = {}
            for i in range(n_sims):
                rng = np.random.default_rng(1000 * (1 + zlib.crc32(scn.encode()) % 97) + int(s2 * 100) + i * 7919)
                sw = make_sweeps(rng, scn, s2=s2); ct = make_controls(rng, "clean", s2=max(s2, 1e-9))
                est, tr = make_trials(rng, cfg, cfg["stage4"]["n_trials_min"], s2, scn)
                r = pr.evaluate(sw, ct, est, tr, cfg, h, False, "simulated", blinding=dict(mode="simulated", verified=True))
                cnt[r["verdict"]] = cnt.get(r["verdict"], 0) + 1
            rows.append((scn, s2, cnt)); log(f"{scn:4s} s2={s2:<5} " + "  ".join(f"{k} {v}/{n_sims}" for k, v in sorted(cnt.items())))
    return rows


def _verdict(rng, cfg, h, scn, s2, leak_db=25.0, trials=False, ctl=None):
    sw = make_sweeps(rng, scn, s2=s2, leak_db=leak_db); ct = make_controls(rng, "clean", s2=max(s2, 1e-9), **(ctl or {}))
    est, tr = make_trials(rng, cfg, cfg["stage4"]["n_trials_min"], s2, scn, leak_db) if trials else (None, None)
    return pr.evaluate(sw, ct, est, tr, cfg, h, False, "simulated", blinding=dict(mode="simulated", verified=True))


def characterise_extra(n=60, log=print):
    cfg, h, fr = pr.load_prereg(os.path.join(HERE, "prereg_v1.json"), None, True)
    log("\nB. P(C1 passes under Holm) vs TRUE selectivity (H1, in-band s2=8, controls ample)")
    for leak in (3, 6, 8, 10, 12, 15, 25):
        c = sum(_verdict(np.random.default_rng(9000 + leak * 100 + i), cfg, h, "H1", 8.0, leak)["verdict"] == "H1_SUPPORTED_RUNG4" for i in range(n))
        log(f"  true selectivity {leak:>2} dB: H1_SUPPORTED_RUNG4 in {c}/{n}")
    log("\nC. P(all controls resolve, i.e. not INCONCLUSIVE_CONTROLS) vs control budget (clean controls at -35 dB; n dwells of k x the sweep dwell)")
    for s2 in (4.0, 8.0):
        for nn, kk in ((4, 10), (8, 20), (12, 40), (12, 80), (20, 80)):
            c = sum(_verdict(np.random.default_rng(7000 + i), cfg, h, "H1", s2, ctl=dict(n=nn, k=kk))["verdict"] != "INCONCLUSIVE_CONTROLS" for i in range(n))
            log(f"  s2={s2:<4} n={nn:<2} k={kk:<3} (control time {nn * kk / 60:.1f} h each): resolved in {c}/{n}")
    log("\nD. Stage 4 (20 blind trials, 30 s dwells): P(C5 floors + permutation pass) vs s2 at the 60 s figure")
    for s2 in (0.5, 1, 2, 4, 8):
        c = 0
        for i in range(n):
            est, tr = make_trials(np.random.default_rng(5000 + i), cfg, cfg["stage4"]["n_trials_min"], s2, "H1")
            r = pr.test_c5(est, tr, cfg, np.random.default_rng(1)); c += bool(r["floors_ok"] and r["p"] <= cfg["tests"]["family_alpha"] / 3)
        log(f"  s2={s2:<4}: C5 passes in {c}/{n}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    print("A. verdict frequencies by scenario and in-band s2 (leak 25 dB, ample controls, Stage 4 included)", flush=True)
    characterise(n, log=lambda *a: print(*a, flush=True))
    characterise_extra(max(30, n // 2), log=lambda *a: print(*a, flush=True))


def characterise_profile(name, n=40, log=print, fc_set="A"):
    import profiles as pf
    cfg = pf.build(name, fc_set); h = pr.sha256_text(pr.canonical(cfg)); ample = dict(n=12, k=400)
    def ev(rng, scn, s2, leak=25.0, trials=False):
        sw = make_sweeps(rng, scn, s2=s2, leak_db=leak, fcs=tuple(float(x) for x in cfg["design"]["fc_hz"]))
        ct = make_controls(rng, "clean", s2=max(s2, 1e-9), fc=float(cfg["design"]["fc_hz"][1]), **ample)
        est, tr = make_trials(rng, cfg, cfg["stage4"]["n_trials_min"], s2, scn, leak) if trials else (None, None)
        return pr.evaluate(sw, ct, est, tr, cfg, h, False, "simulated", blinding=dict(mode="simulated", verified=True))
    k, hrs, hrs3 = pf.control_budget(cfg)
    log(f"\n== profile {name} (fc set {fc_set}); requires s2 >= {cfg['sensitivity']['min_in_band_power_over_noise']:g}; "
        f"controls {k}x dwell = {hrs:.1f} h each ({hrs3:.0f} h for 3); {n} simulations per cell ==")
    c = sum(ev(np.random.default_rng(31000 + i), scn, 8.0 if scn != "H0a" else 0.0).get("verdict", "").startswith("H1") for scn in ("H0a", "H0b", "H0c") for i in range(n))
    log(f"  false H1 verdicts under the three nulls: {c}/{3 * n}")
    for s2 in (4, 8, 16, 32):
        cnt = {}
        for i in range(n):
            v = ev(np.random.default_rng(32000 + s2 * 100 + i), "H1", float(s2), trials=True)["verdict"]; cnt[v] = cnt.get(v, 0) + 1
        log(f"  H1 (25 dB selective), s2={s2:<3}: " + ", ".join(f"{a} {b}" for a, b in sorted(cnt.items())))
    for sel in (6, 10, 15, 25):
        c = sum(ev(np.random.default_rng(33000 + sel * 100 + i), "H1", 16.0, leak=sel)["verdict"].startswith("H1") for i in range(n))
        log(f"  true selectivity {sel:>2} dB at s2=16: H1 verdict in {c}/{n}")
