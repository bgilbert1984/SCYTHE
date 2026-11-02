#!/usr/bin/env python3
"""Experiment E: synthetic null / confound suite for the Stage 0B byte-toggle statistics.

PROVENANCE: everything this script produces is SYNTHETIC. No receiver, no RF, no
Stage 0B capture is read. Results are evidence about the ANALYSIS, never about the
hypothesis. Every JSON output carries "provenance": "synthetic".

Question answered: does the Stage 0B analysis (per-window log power vs. inter-sample
toggle rate, circular-shift null) report "significant" correlations on receivers that
have NO external emission at all?

Stage 0B regressor logP is the variance of the SAME bytes the toggle statistics are
computed from (stage0b_regress.py feats()), so it is a function of the output. This
suite keeps those definitions byte for byte so the numbers are comparable.

Scenarios (each yields uint8 I and Q arrays shaped [windows, samples]):
  iid_gain        independent Gaussian samples, only the amplitude drifts. NULL.
  spectral_fixed  constant power, spectral tilt drifts (power regressor is flat). NEG CTRL.
  spectral_coupled tilt drifts WITH power (narrowband AM raises in-band correlation). NULL.
  clipping        iid_gain driven into the rails.
  gain_step       iid_gain with an abrupt gain change (AGC/gain-register confound).
  crosstalk       I branch picks up a fraction of the Q analog value.
  usb_burst       host activity flips I-byte LSBs, independent of power. NEG CTRL.
  quadrature_colored  I/Q are quadrature parts of ONE coloured complex signal (what the RTL2832U digital mixer
                  produces in direct sampling), operating point matched to the Stage 0B re-capture (sd 2.37 LSB,
                  lag-1 autocorrelation about -0.7). NULL.
  leak_positive   POSITIVE CONTROL: repeated I samples driven by a hidden emission envelope,
                  with the analog input power held constant.

Design check (the discriminating test): the same scenarios with receiver input held
at constant power while the independent reference (commanded stimulus) is varied.
A byte-statistic-vs-own-power regression cannot separate iid_gain from leak_positive;
the held-input design can.

Spectral surrogate (real data, no model): --surrogate FILE.u8 keeps each real window's exact complex power
spectrum (including I/Q quadrature structure and the DC bin), randomises the phases, re-quantises to bytes and
compares the byte statistics with the real ones. If a statistic follows from the per-window spectrum plus
quantisation alone, the surrogate reproduces it and the statistic carries no evidence of anything else.

usage: confound_suite.py [--seeds 20] [--json out.json] | --selftest
       confound_suite.py --surrogate CAPTURE.u8 [--samples 204800]
"""
import sys, json, argparse
import numpy as np

POP = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)
N_WIN, W_SAMP = 300, 204800        # real windows are 204800 samples (0.1 s at 2.048 MS/s); 300 vs 600/1200
# NOTE: shrinking W_SAMP raises per-window sampling noise ~sqrt(W) and HIDES null effects (8192 showed r~0
# where 204800 shows p=0.002). --fast uses small windows for the selftest only.
MEAN_BYTE, BASE_SD = 127.37, 8.0   # observed mean byte; observed std range 3.8-17.7


# ---- Stage 0B statistics (same definitions as stage0b_regress.py / stage0b_partial.py)
def feats(I, Q):
    z = I.astype(np.float32); zq = Q.astype(np.float32)
    P = ((z - z.mean(1, keepdims=True)) ** 2 + (zq - zq.mean(1, keepdims=True)) ** 2).mean(1)
    return dict(logP=np.log10(P + 1e-9),
                hw=POP[np.concatenate([I, Q], 1)].mean(1),
                tog_qi=POP[Q[:, :-1] ^ I[:, 1:]].mean(1) / 8,
                tog_ii=POP[I[:, :-1] ^ I[:, 1:]].mean(1) / 8)


def rank(x):
    r = np.empty(len(x)); r[np.argsort(x)] = np.arange(len(x)); return r


def spearman(x, y):
    a, b = rank(x), rank(y); a = a - a.mean(); b = b - b.mean()
    return float((a * b).sum() / np.sqrt((a * a).sum() * (b * b).sum()))


def detr(x):
    t = np.arange(len(x)); return x - np.polyval(np.polyfit(t, x, 1), t)


def shift_test(x, y, rng, nperm=500):
    """Circular-shift null in multiples of 50 windows (5 s), as in Stage 0B."""
    n = len(x); r = spearman(x, y)
    step = 50 if n >= 300 else max(2, n // 10)          # 50 windows = 5 s as in Stage 0B; shorter series scale down
    ks = rng.integers(2, n // step - 1, nperm) * step
    null = np.array([abs(spearman(x, np.roll(y, int(k)))) for k in ks])
    return r, (1 + (null >= abs(r)).sum()) / (nperm + 1)


# ---- generators
def _envelope(rng, n, power_range):
    """Slow AR(1) in log-power with the requested max/min power ratio (Stage 0B: x1.08-1.95)."""
    e = np.zeros(n)
    for k in range(1, n):
        e[k] = 0.97 * e[k - 1] + rng.normal()
    e = (e - e.min()) / (np.ptp(e) + 1e-12)
    return 10 ** (e * np.log10(power_range))            # power multiplier in [1, range]


def _to_u8(a):
    return np.clip(np.rint(a), 0, 255).astype(np.uint8)


def _lowpass(g, rho):
    """Per-window AR(1) spectral shaping, variance-normalised so power is unchanged."""
    from scipy.signal import lfilter
    out = np.empty_like(g)
    for k in range(g.shape[0]):
        out[k] = lfilter([np.sqrt(1 - rho[k] ** 2)], [1, -rho[k]], g[k])
    return out


def _edge_psd(w, target_lag1=-0.7):
    """Edge-heavy PSD S(u)=eps+|u|^p on u in [-1,1) (u=2f/fs) with p chosen so the real part has lag-1
    autocorrelation near target (the Stage 0B re-capture at 1.0 MHz measured -0.71)."""
    u = np.fft.fftfreq(w) * 2
    grid = np.linspace(-1, 1, 4001); best = None
    for p in (2, 4, 6, 8, 12, 16, 24, 32, 48):
        sg = 0.002 + np.abs(grid) ** p
        r = float((sg * np.cos(np.pi * grid)).sum() / sg.sum())
        if best is None or abs(r - target_lag1) < abs(best[1] - target_lag1): best = (p, r)
    return 0.002 + np.abs(u) ** best[0]


def gen(name, rng, n=None, w=None, power_range=1.95):
    n = n or N_WIN; w = w or W_SAMP
    env = _envelope(rng, n, power_range)
    sd = BASE_SD * np.sqrt(env)[:, None]
    g = rng.normal(size=(n, w)); gi = rng.normal(size=(n, w))
    I_noise = 0.8 * gi
    hidden = None
    if name == "iid_gain":
        Qa = sd * g
    elif name == "spectral_fixed":
        rho = 0.2 + 0.7 * (env - 1) / (power_range - 1 + 1e-12)
        Qa = BASE_SD * _lowpass(g, rho)
    elif name == "spectral_coupled":
        rho = 0.2 + 0.7 * (env - 1) / (power_range - 1 + 1e-12)
        Qa = sd * _lowpass(g, rho)
    elif name == "clipping":
        Qa = sd * 6.0 * g
    elif name == "gain_step":
        Qa = sd * g * np.where(np.arange(n) < n // 2, 1.0, 1.6)[:, None]
    elif name == "crosstalk":
        Qa = sd * g; I_noise = 0.8 * gi + 0.12 * Qa
    elif name == "usb_burst":
        Qa = BASE_SD * g
    elif name == "quadrature_colored":
        from scipy import fft as sfft
        sdq = 2.37 * np.sqrt(env / env.mean())[:, None]
        amp = np.sqrt(_edge_psd(w)); Qa = np.empty((n, w)); I_noise = np.empty((n, w))
        for k in range(n):                                # one window at a time: whole-array complex FFTs ran out of memory
            zk = sfft.ifft(sfft.fft(g[k] + 1j * gi[k], workers=4) * amp, workers=4)
            Qa[k], I_noise[k] = zk.real, zk.imag
        s0 = Qa.std(); Qa *= sdq / s0; I_noise *= sdq / s0
    elif name == "leak_positive":
        Qa = BASE_SD * g                                  # input power constant
    else:
        raise ValueError(name)
    I = _to_u8(MEAN_BYTE + I_noise); Q = _to_u8(MEAN_BYTE + Qa)
    if name == "usb_burst":
        busy = _envelope(rng, n, 4.0) > 2.0               # host activity, unrelated to power
        flips = rng.random((n, w)) < (0.08 * busy[:, None])
        I = I ^ flips.astype(np.uint8)
    if name == "leak_positive":
        hidden = _envelope(rng, n, 4.0)                   # hidden emission envelope
        p = 0.10 * (hidden[:, None] - 1)             # up to 30% repeated I samples
        hold = rng.random((n, w)) < p                 # emission makes adjacent I bytes correlate
        I[:, 1:] = np.where(hold[:, 1:], I[:, :-1], I[:, 1:])
    return I, Q, env, hidden


SCEN = ["iid_gain", "spectral_fixed", "spectral_coupled", "clipping",
        "gain_step", "crosstalk", "usb_burst", "quadrature_colored", "leak_positive"]
ROLE = dict(iid_gain="null", spectral_fixed="neg_ctrl", spectral_coupled="null",
            clipping="null", gain_step="null", crosstalk="null", quadrature_colored="null",
            usb_burst="neg_ctrl", leak_positive="positive")


def stage0b_analysis(I, Q, rng):
    """Exactly what Stage 0B did: toggle rate vs own log power, detrended, shift test."""
    f = feats(I, Q); x = detr(f["logP"]); out = {}
    for s in ("tog_qi", "tog_ii", "hw"):
        r, p = shift_test(x, detr(f[s]), rng)
        out[s] = dict(r=r, p=p, flag=bool(p <= 0.01 and abs(r) >= 0.2))
    return out


def held_input_analysis(scen, rng):
    # uses module N_WIN for the reference length
    """Discriminating design: analog input power held constant, independent reference varied.
    The reference is the commanded stimulus level (known to the experimenter), NOT derived
    from the receiver bytes. For a receiver with no leak the byte stats do not follow it."""
    I, Q, _, hidden = gen("leak_positive" if scen == "leak_positive" else "usb_burst"
                          if scen == "usb_burst" else "iid_gain", rng, power_range=1.0001)
    ref = hidden if hidden is not None else _envelope(rng, N_WIN, 4.0)
    f = feats(I, Q); out = {}
    for s in ("tog_qi", "tog_ii"):
        r, p = shift_test(detr(np.log10(ref)), detr(f[s]), rng)
        out[s] = dict(r=r, p=p, flag=bool(p <= 0.01 and abs(r) >= 0.2))
    return out


def run(seeds=20, log=print):
    res = {"provenance": "synthetic", "n_windows": N_WIN, "samples_per_window": W_SAMP,
           "seeds": seeds, "stage0b_style": {}, "held_input": {}}
    for sc in SCEN:
        rows = []
        for sd in range(seeds):
            rng = np.random.default_rng(1000 * (SCEN.index(sc) + 1) + sd)
            I, Q, _, _ = gen(sc, rng)
            rows.append(stage0b_analysis(I, Q, rng))
        agg = {}
        for s in ("tog_qi", "tog_ii", "hw"):
            rs = np.array([r[s]["r"] for r in rows])
            agg[s] = dict(median_r=float(np.median(rs)), min_r=float(rs.min()), max_r=float(rs.max()),
                          flag_rate=float(np.mean([r[s]["flag"] for r in rows])))
        res["stage0b_style"][sc] = dict(role=ROLE[sc], **agg)
    for sc in ("iid_gain", "usb_burst", "leak_positive"):
        rows = []
        for sd in range(seeds):
            rng = np.random.default_rng(777000 + 1000 * SCEN.index(sc) + sd)
            rows.append(held_input_analysis(sc, rng))
        agg = {}
        for s in ("tog_qi", "tog_ii"):
            rs = np.array([r[s]["r"] for r in rows])
            agg[s] = dict(median_r=float(np.median(rs)), flag_rate=float(np.mean([r[s]["flag"] for r in rows])))
        res["held_input"][sc] = dict(role=ROLE[sc], **agg)
    return res


def table(res):
    L = ["Stage 0B-style analysis (toggle rate vs OWN log power; flag = p<=0.01 and |r|>=0.2)",
         f"{'scenario':19s}{'role':9s}{'tog_qi r (med)':>16s}{'flag':>6s}{'tog_ii r (med)':>16s}{'flag':>6s}{'hw r (med)':>12s}{'flag':>6s}"]
    for sc, v in res["stage0b_style"].items():
        L.append(f"{sc:19s}{v['role']:9s}" + "".join(
            f"{v[s]['median_r']:>+16.2f}{v[s]['flag_rate']:>6.2f}" if s != "hw" else
            f"{v[s]['median_r']:>+12.2f}{v[s]['flag_rate']:>6.2f}" for s in ("tog_qi", "tog_ii", "hw")))
    L += ["", "Held-input design (analog power constant; reference = commanded stimulus)",
          f"{'scenario':19s}{'role':9s}{'tog_qi r (med)':>16s}{'flag':>6s}{'tog_ii r (med)':>16s}{'flag':>6s}"]
    for sc, v in res["held_input"].items():
        L.append(f"{sc:19s}{v['role']:9s}" + "".join(
            f"{v[s]['median_r']:>+16.2f}{v[s]['flag_rate']:>6.2f}" for s in ("tog_qi", "tog_ii")))
    return "\n".join(L)


# ---------------- spectral surrogate (works on real captures) ----------------
def spectral_surrogate(I, Q, rng, mean=MEAN_BYTE):
    """Per window: keep |FFT| of z=I+jQ (so the complex power spectrum, hence I/Q structure) and the DC bin,
    randomise every other phase, round to bytes. Rounding adds ~1/12 LSB^2 of noise and removes the ADC's own
    code structure, so levels differ slightly from the real data; slopes and window-to-window tracking are
    the meaningful outputs."""
    from scipy import fft as sfft
    oi, oq = np.empty_like(I), np.empty_like(Q)
    for k in range(len(I)):
        z = (I[k].astype(np.float64) - mean) + 1j * (Q[k].astype(np.float64) - mean)
        Z = sfft.fft(z, workers=4); ph = rng.uniform(0, 2 * np.pi, len(Z)); ph[0] = np.angle(Z[0])
        zs = sfft.ifft(np.abs(Z) * np.exp(1j * ph), workers=4)
        oi[k] = _to_u8(zs.real + mean); oq[k] = _to_u8(zs.imag + mean)
    return oi, oq


def load_capture(path, w):
    a = np.fromfile(path, dtype=np.uint8); n = len(a) // (2 * w)
    a = a[:n * 2 * w].reshape(n, w, 2); return a[:, :, 0], a[:, :, 1]


def surrogate_compare(I, Q, seed=11, log=print):
    rng = np.random.default_rng(seed); Is, Qs = spectral_surrogate(I, Q, rng)
    fr, fs = feats(I, Q), feats(Is, Qs); out = {"provenance": "recorded-vs-surrogate", "n_windows": int(len(I)),
        "logP_corr": float(np.corrcoef(fr["logP"], fs["logP"])[0, 1]), "stats": {}}
    for k in ("hw", "tog_qi", "tog_ii"):
        r1, _ = shift_test(detr(fr["logP"]), detr(fr[k]), np.random.default_rng(3))
        r2, _ = shift_test(detr(fs["logP"]), detr(fs[k]), np.random.default_rng(3))
        out["stats"][k] = dict(real_r=r1, real_slope=float(np.polyfit(fr["logP"], fr[k], 1)[0]), surr_r=r2,
                               surr_slope=float(np.polyfit(fs["logP"], fs[k], 1)[0]),
                               window_corr=float(np.corrcoef(detr(fr[k]), detr(fs[k]))[0, 1]),
                               level_real=float(fr[k].mean()), level_surr=float(fs[k].mean()))
    log(f"windows {out['n_windows']}; logP real-vs-surrogate corr {out['logP_corr']:.4f}")
    log(f"{'stat':7s}{'real r':>8s}{'real slope':>11s}{'surr r':>8s}{'surr slope':>11s}{'win corr':>9s}{'level real/surr':>18s}")
    for k, v in out["stats"].items():
        log(f"{k:7s}{v['real_r']:>+8.2f}{v['real_slope']:>+11.3f}{v['surr_r']:>+8.2f}{v['surr_slope']:>+11.3f}{v['window_corr']:>+9.2f}"
            f"{v['level_real']:>9.4f}/{v['level_surr']:.4f}")
    return out


def selftest():
    global N_WIN, W_SAMP
    N_WIN, W_SAMP = 600, 8192      # small windows are fine for a mechanism check (strong leak only)
    r = run(seeds=3, log=lambda *_: None)
    s = r["stage0b_style"]; h = r["held_input"]
    assert r["provenance"] == "synthetic"
    # the suite must be able to detect a leak when the design allows it ...
    assert h["leak_positive"]["tog_ii"]["flag_rate"] >= 0.66, h["leak_positive"]
    # ... and must not flag a clean receiver under that design
    assert h["iid_gain"]["tog_qi"]["flag_rate"] <= 0.34, h["iid_gain"]
    assert h["iid_gain"]["tog_ii"]["flag_rate"] <= 0.34, h["iid_gain"]
    # the coloured-quadrature scenario hits its operating point
    rng = np.random.default_rng(5); I, Q, _, _ = gen("quadrature_colored", rng, n=20, w=16384, power_range=1.5)
    ac = float(np.corrcoef(I.ravel()[:-1].astype(float), I.ravel()[1:].astype(float))[0, 1])
    assert -0.8 < ac < -0.55, ac
    assert abs(I.astype(float).std() - 2.37) < 0.4 and abs(float(np.corrcoef(I.ravel().astype(float), Q.ravel().astype(float))[0, 1])) < 0.05
    # the surrogate keeps each window's power and, for a spectrum-determined statistic, tracks window by window
    rng = np.random.default_rng(6); I, Q, _, _ = gen("quadrature_colored", rng, n=60, w=65536, power_range=2.0)
    sc = surrogate_compare(I, Q, log=lambda *_: None)
    assert sc["logP_corr"] > 0.99, sc["logP_corr"]
    assert sc["stats"]["tog_ii"]["window_corr"] > 0.6, sc["stats"]["tog_ii"]
    print("selftest ok"); return r


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--json"); ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--windows", type=int); ap.add_argument("--samples", type=int)
    ap.add_argument("--surrogate", metavar="CAPTURE.u8")
    a = ap.parse_args()
    if a.surrogate:
        Wd = a.samples or 204800; I_, Q_ = load_capture(a.surrogate, Wd)
        out = surrogate_compare(I_, Q_)
        if a.json: json.dump(out, open(a.json, "w"), indent=1)
        sys.exit(0)
    if a.windows: N_WIN = a.windows
    if a.samples: W_SAMP = a.samples
    res = selftest() if a.selftest else run(a.seeds)
    print(table(res))
    if a.json:
        json.dump(res, open(a.json, "w"), indent=1)
