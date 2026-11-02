#!/usr/bin/env python3
"""Experiment E Stage 0B: fixed-tuning regression on a recorded capture FILE (u8 interleaved I,Q, 2.048 MS/s).

Same statistic definitions and circular-shift test as stage0b_regress.py / stage0b_partial.py, but reads a file:
those scripts capture from rtl_tcp at import time and cannot be reused on stored data.

usage: stage0b_reanalyze.py LABEL=capture.u8 [LABEL=capture.u8 ...]

For each file prints, per 0.1 s window: Spearman r of each byte statistic against log10 window power (raw and
linearly detrended), against a circular-shift null (multiples of 5 s for long series), and the partial
correlation after removing trend and window mean. NOTE the regressor is the variance of the same bytes the
statistics are computed from (see confound_suite.py); a correlation here is not evidence of an external emission.
"""
import sys, numpy as np
RATE = 2_048_000; W = RATE // 10 * 2
POP = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def rank(x): r = np.empty(len(x)); r[np.argsort(x)] = np.arange(len(x)); return r
def sp(x, y):
    a, b = rank(x), rank(y); a = a - a.mean(); b = b - b.mean(); return float((a * b).sum() / np.sqrt((a * a).sum() * (b * b).sum()))
def detr(x): t = np.arange(len(x)); return x - np.polyval(np.polyfit(t, x, 1), t)
def resid(y, cols):
    X = np.column_stack([np.ones(len(y))] + cols); b, *_ = np.linalg.lstsq(X, y, rcond=None); return y - X @ b


def test(x, y, n, rng):
    r = sp(x, y); step = 50 if n >= 300 else max(2, n // 10)
    ks = rng.integers(2, n // step - 1, 1000) * step
    null = [abs(sp(x, np.roll(y, int(k)))) for k in ks]
    return r, float(np.percentile(null, 95)), (1 + sum(v >= abs(r) for v in null)) / 1001


def run(name, path, seed=7):
    rng = np.random.default_rng(seed)
    a = np.fromfile(path, dtype=np.uint8); n = len(a) // W; a = a[:n * W].reshape(n, W)
    I, Q = a[:, 0::2], a[:, 1::2]; z = I.astype(np.float32); zq = Q.astype(np.float32)
    P = ((z - z.mean(1, keepdims=True)) ** 2 + (zq - zq.mean(1, keepdims=True)) ** 2).mean(1); lp = np.log10(P + 1e-9)
    mean = a.mean(1); sd = a.astype(np.float32).std(1); t = np.arange(n, dtype=float)
    f = {"hw": POP[a].mean(1), "tog_iq": POP[I ^ Q].mean(1) / 8, "tog_qi": POP[Q[:, :-1] ^ I[:, 1:]].mean(1) / 8,
         "tog_ii": POP[I[:, :-1] ^ I[:, 1:]].mean(1) / 8}
    print(f"\n{name}: {n} windows, power range x{10 ** (lp.max() - lp.min()):.2f}, corr(logP, log sd) {np.corrcoef(lp, np.log10(sd))[0, 1]:.4f}, "
          f"mean byte {a.mean():.2f}, I sd {I.std():.2f}, Q sd {Q.std():.2f}, corr(I,Q) {np.corrcoef(I[:, :100000].ravel(), Q[:, :100000].ravel())[0, 1]:+.3f}, "
          f"lag-1 autocorr I {np.corrcoef(I.ravel()[:-1][:4_000_000], I.ravel()[1:][:4_000_000])[0, 1]:+.2f}")
    for s_, y in f.items():
        out = []
        for tag, (xx, yy) in (("raw", (lp, y)), ("detrended", (detr(lp), detr(y)))):
            r, n95, p = test(xx, yy, n, rng); out.append(f"{tag}: r={r:+.3f} slope={np.polyfit(xx, yy, 1)[0]:+.3f}/dec null95={n95:.3f} p={p:.3f}")
        print(f"  {s_:7s} " + " | ".join(out))
    for lab, cov in (("trend only", [t]), ("+mean,mean^2", [t, mean, mean ** 2])):
        xr = resid(lp, cov); line = []
        for k in ("tog_qi", "tog_ii"):
            r, n95, p = test(xr, resid(f[k], cov), n, rng); line.append(f"{k}: r={r:+.3f} (null95 {n95:.3f}, p={p:.3f})")
        print(f"  partial [{lab}] " + " | ".join(line))


if __name__ == "__main__":
    if len(sys.argv) < 2: sys.exit(__doc__)
    for arg in sys.argv[1:]:
        nm, p = arg.split("=", 1); run(nm, p)
