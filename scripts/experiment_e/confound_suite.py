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
  leak_positive   POSITIVE CONTROL: repeated I samples driven by a hidden emission envelope,
                  with the analog input power held constant.

Design check (the discriminating test): the same scenarios with receiver input held
at constant power while the independent reference (commanded stimulus) is varied.
A byte-statistic-vs-own-power regression cannot separate iid_gain from leak_positive;
the held-input design can.

usage: confound_suite.py [--seeds 20] [--json out.json] | --selftest
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
    ks = rng.integers(2, n // 50 - 1, nperm) * 50
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
        "gain_step", "crosstalk", "usb_burst", "leak_positive"]
ROLE = dict(iid_gain="null", spectral_fixed="neg_ctrl", spectral_coupled="null",
            clipping="null", gain_step="null", crosstalk="null",
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
         f"{'scenario':17s}{'role':9s}{'tog_qi r (med)':>16s}{'flag':>6s}{'tog_ii r (med)':>16s}{'flag':>6s}{'hw r (med)':>12s}{'flag':>6s}"]
    for sc, v in res["stage0b_style"].items():
        L.append(f"{sc:17s}{v['role']:9s}" + "".join(
            f"{v[s]['median_r']:>+16.2f}{v[s]['flag_rate']:>6.2f}" if s != "hw" else
            f"{v[s]['median_r']:>+12.2f}{v[s]['flag_rate']:>6.2f}" for s in ("tog_qi", "tog_ii", "hw")))
    L += ["", "Held-input design (analog power constant; reference = commanded stimulus)",
          f"{'scenario':17s}{'role':9s}{'tog_qi r (med)':>16s}{'flag':>6s}{'tog_ii r (med)':>16s}{'flag':>6s}"]
    for sc, v in res["held_input"].items():
        L.append(f"{sc:17s}{v['role']:9s}" + "".join(
            f"{v[s]['median_r']:>+16.2f}{v[s]['flag_rate']:>6.2f}" for s in ("tog_qi", "tog_ii")))
    return "\n".join(L)


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
    print("selftest ok"); return r


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--json"); ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--windows", type=int); ap.add_argument("--samples", type=int)
    a = ap.parse_args()
    if a.windows: N_WIN = a.windows
    if a.samples: W_SAMP = a.samples
    res = selftest() if a.selftest else run(a.seeds)
    print(table(res))
    if a.json:
        json.dump(res, open(a.json, "w"), indent=1)
