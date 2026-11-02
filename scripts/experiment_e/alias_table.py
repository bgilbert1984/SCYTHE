#!/usr/bin/env python3
"""Experiment E: harmonic-folding (alias) table for a square-wave probe into a direct-sampling RTL2832U.

PROVENANCE: computed arithmetic from stated assumptions. Not measured. The assumptions below are the
parts that can be wrong; the bench must confirm the ADC clock and the real harmonic levels.

Model
  Probe        ideal rectangular wave at f0, duty d. Harmonic n has amplitude |sin(n*pi*d)|/(n*pi) relative
               to the fundamental amplitude sin(pi*d)/pi, i.e. rel(n) = |sin(n*pi*d)| / (n * sin(pi*d)).
               At d=0.5 only odd n exist, rel = 1/n.
  Edge rolloff single pole at 1/(pi*t_rise) (default 2 ns). ASSUMPTION about the Si5351 output stage.
  Probe filter optional Butterworth low-pass (order N, cutoff fc) applied to the TRUE frequency before
               sampling. Without it the table is the unfiltered worst case.
  Sampler      real ADC at fs (default 28.8 MHz = librtlsdr DEF_RTL_XTAL_FREQ), NO anti-alias filter.
               A component at f appears at fa = |f - round(f/fs)*fs| in [0, fs/2] (and at -fa).
  Receiver     direct sampling: rtlsdr_set_center_freq() -> rtlsdr_set_if_freq(): a digital mixer, no tuner.
               Passband = [fc - bw/2, fc + bw/2] (default bw = 2.048 MHz). Because the input is real, a
               tone at fa lands in band if fa OR -fa is inside the passband.
  Uncertainty  alias error = n*f0*ppm_probe + k*fs*ppm_adc (k = fold index, round(f/fs)); a harmonic
               within that error + decimation-filter transition of a band edge is MARGINAL.
Not modelled: ADC nonlinearity (strong in-band tones create their own HD2/HD3), decimation-filter
  rejection shape, ADC input bandwidth rolloff, USB/host spurs, mains. A "CLEAN" verdict only means no
  probe harmonic is predicted to land in the passband above the floor; it is not evidence about the DUT.

usage:
  alias_table.py explain --f0 12.5e6 --center 7.074e6 [model options]
  alias_table.py sweep --f0-range 0.2e6:14e6:100e3 --centers 1e6,7.074e6,12.5e6 [--csv out.csv] [--md out.md]
  alias_table.py --selftest
"""
import sys, math, json, argparse
import numpy as np

FS_DEFAULT = 28_800_000.0
BW_DEFAULT = 2_048_000.0


def fold(f, fs):
    """Return (alias_hz in [0, fs/2], fold_index k) for a real tone at f >= 0."""
    k = np.rint(f / fs)
    return np.abs(f - k * fs), k


def harmonic_rel_amp(n, duty):
    n = np.asarray(n, dtype=float)
    return np.abs(np.sin(n * math.pi * duty)) / (n * math.sin(math.pi * duty))


def filter_gain(f, lpf_hz, order):
    if not lpf_hz: return np.ones_like(f)
    return 1.0 / np.sqrt(1.0 + (f / lpf_hz) ** (2 * order))


def edge_gain(f, rise_ns):
    if not rise_ns: return np.ones_like(f)
    fe = 1.0 / (math.pi * rise_ns * 1e-9)
    return 1.0 / np.sqrt(1.0 + (f / fe) ** 2)


def in_band(fa, lo, hi):
    """Real input: a tone at fa appears at +fa and -fa after the mixer."""
    return ((fa >= lo) & (fa <= hi)) | ((-fa >= lo) & (-fa <= hi))


def dist_to_band(fa, lo, hi):
    """Distance (Hz) from the nearest of +/-fa to the passband (0 if inside)."""
    def d(x): return np.where((x >= lo) & (x <= hi), 0.0, np.minimum(np.abs(x - lo), np.abs(x - hi)))
    return np.minimum(d(fa), d(-fa))


def edge_dist(fa, lo, hi):
    """Distance (Hz) from the nearest of +/-fa to the nearest passband EDGE (positive either side)."""
    cands = [np.abs(s * fa - e) for s in (1, -1) for e in (lo, hi)]
    return np.minimum.reduce(cands)


class Model:
    def __init__(self, fs=FS_DEFAULT, bw=BW_DEFAULT, duty=0.5, rise_ns=2.0, lpf_hz=None, lpf_order=5,
                 floor_dbc=-70.0, ppm_probe=20.0, ppm_adc=35.0, transition_hz=100e3, max_hz=500e6):
        self.__dict__.update(locals()); del self.__dict__["self"]

    def describe(self):
        return {k: v for k, v in self.__dict__.items()}


def harmonics(f0, m):
    nmax = int(m.max_hz // f0)
    n = np.arange(1, nmax + 1)
    rel = harmonic_rel_amp(n, m.duty)
    keep = rel > 1e-9                          # drops exact zeros (even harmonics at 50 %)
    n, rel = n[keep], rel[keep]
    f = n * f0
    g = rel * filter_gain(f, m.lpf_hz, m.lpf_order) * edge_gain(f, m.rise_ns)
    dbc = 20 * np.log10(g / (harmonic_rel_amp(1, m.duty) * filter_gain(np.array([f0]), m.lpf_hz, m.lpf_order)
                             * edge_gain(np.array([f0]), m.rise_ns))[0] + 1e-30)
    # dBc here is relative to the (filtered) fundamental at the sampler input
    fa, k = fold(f, m.fs)
    return n, f, fa, k, dbc


def analyse(f0, center, m):
    lo, hi = center - m.bw / 2, center + m.bw / 2
    n, f, fa, k, dbc = harmonics(f0, m)
    inb = in_band(fa, lo, hi)
    unc = n * f0 * m.ppm_probe * 1e-6 + k * m.fs * m.ppm_adc * 1e-6 + m.transition_hz
    ed = edge_dist(fa, lo, hi)
    fund_in = bool(lo <= f0 <= hi) or bool(lo <= -f0 <= hi)
    hot = inb & (dbc > m.floor_dbc)                              # in-band and above the floor
    marginal = ~inb & (ed <= unc) & (dbc > m.floor_dbc)          # predicted outside but within error of an edge
    marginal_in = inb & (ed <= unc) & (dbc > m.floor_dbc)        # predicted inside but within error of an edge
    # fundamental is the intended probe; contamination = harmonics (n>1) only
    hot_h = hot & (n > 1)
    if fund_in:
        verdict = "IN_BAND"
    elif hot_h.any():
        verdict = "CONTAMINATED"
    elif (marginal & (n > 1)).any():
        verdict = "MARGINAL"
    else:
        verdict = "CLEAN_OUT"
    top = None
    if hot_h.any():
        i = np.argmax(np.where(hot_h, dbc, -999)); top = (int(n[i]), float(fa[i]), float(dbc[i]))
    return dict(f0=f0, center=center, lo=lo, hi=hi, fundamental_in_band=fund_in, verdict=verdict,
                n_hot=int(hot_h.sum()), n_marginal=int((marginal | marginal_in)[n > 1].sum()),
                worst_n=top[0] if top else None, worst_alias_hz=top[1] if top else None,
                worst_dbc=top[2] if top else None, _arrays=(n, f, fa, k, dbc, inb, unc))


def explain(f0, center, m, show_dbc=-90.0):
    r = analyse(f0, center, m)
    n, f, fa, k, dbc, inb, unc = r["_arrays"]
    print(f"probe {f0/1e6:.6f} MHz  duty {m.duty:.2f}  center {center/1e6:.6f} MHz  passband "
          f"[{r['lo']/1e6:.4f}, {r['hi']/1e6:.4f}] MHz  fs {m.fs/1e6:.3f} MHz")
    print(f"filter: {'none (UNFILTERED worst case)' if not m.lpf_hz else f'Butterworth {m.lpf_order} @ {m.lpf_hz/1e6:.3f} MHz'};"
          f" edge rise {m.rise_ns} ns; floor {m.floor_dbc} dBc")
    print(f"{'n':>4} {'true MHz':>10} {'fold k':>6} {'alias MHz':>10} {'dBc':>8} {'in-band':>8} {'+/-err kHz':>10}")
    shown = 0
    for i in range(len(n)):
        if dbc[i] < show_dbc and not (inb[i] and n[i] > 1): continue
        if shown >= 40: print("   ... (truncated; use --json for all)"); break
        flag = "YES" if inb[i] else ""
        print(f"{n[i]:>4d} {f[i]/1e6:>10.4f} {int(k[i]):>6d} {fa[i]/1e6:>10.4f} {dbc[i]:>8.1f} {flag:>8} {unc[i]/1e3:>10.1f}")
        shown += 1
    print(f"verdict: {r['verdict']}  (fundamental in band: {r['fundamental_in_band']}; harmonics in band above floor: {r['n_hot']};"
          f" marginal: {r['n_marginal']})")
    if r["worst_n"]: print(f"worst: n={r['worst_n']} aliases to {r['worst_alias_hz']/1e6:.4f} MHz at {r['worst_dbc']:.1f} dBc")
    return r


def sweep(f0s, centers, m):
    rows = []
    for c in centers:
        for f0 in f0s:
            r = analyse(float(f0), float(c), m); r.pop("_arrays"); rows.append(r)
    return rows


def rng(spec):
    a, b, s = (float(x) for x in spec.split(":"))
    return np.arange(a, b + s / 2, s)


def write_md(rows, m, path):
    L = ["# Experiment E alias table (computed, not measured)", "",
         "Assumptions: `" + json.dumps(m.describe()) + "`", "",
         "| center MHz | probe MHz | verdict | harmonics in band | marginal | worst (n, alias MHz, dBc) |", "|---|---|---|---|---|---|"]
    for r in rows:
        w = f"{r['worst_n']}, {r['worst_alias_hz']/1e6:.3f}, {r['worst_dbc']:.1f}" if r["worst_n"] else ""
        L.append(f"| {r['center']/1e6:.3f} | {r['f0']/1e6:.3f} | {r['verdict']} | {r['n_hot']} | {r['n_marginal']} | {w} |")
    open(path, "w").write("\n".join(L) + "\n")


def write_csv(rows, path):
    import csv
    keys = [k for k in rows[0] if not k.startswith("_")]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, keys); w.writeheader(); w.writerows(rows)


def selftest():
    m = Model(rise_ns=0)                     # ideal: matches the plan's hand arithmetic
    # 1. the bench plan's table (docs/EXPERIMENT_E_BENCH_PLAN.md)
    def al(f0, ns): return [round(float(fold(n * f0, FS_DEFAULT)[0]) / 1e6, 3) for n in ns]
    assert al(7.074e6, [3, 5, 7]) == [7.578, 6.57, 8.082], al(7.074e6, [3, 5, 7])
    assert al(12.5e6, [3, 5, 7, 9]) == [8.7, 4.9, 1.1, 2.7], al(12.5e6, [3, 5, 7, 9])
    # 2. independent check: time-domain synthesis sampled at fs, FFT peak positions must equal the fold
    N = 4096; binhz = FS_DEFAULT / N; f0 = 1234 * binhz               # exact bin, ~8.70 MHz
    t = np.arange(N) / FS_DEFAULT
    ns = [1, 3, 5, 7, 9, 11, 13, 15, 17, 19]
    x = sum((1.0 / n) * np.sin(2 * np.pi * n * f0 * t) for n in ns)   # exact bins: n*f0 = n*1234 bins
    X = np.abs(np.fft.rfft(x)) / (N / 2)
    for n in ns:
        fa, _ = fold(n * f0, FS_DEFAULT); b = int(round(fa / binhz))
        assert abs(X[b] - 1.0 / n) < 1e-6, (n, b, X[b])
    # every non-zero FFT bin must be one of the predicted aliases (nothing unexplained)
    pred = {int(round(float(fold(n * f0, FS_DEFAULT)[0]) / binhz)) for n in ns}
    seen = {i for i in range(len(X)) if X[i] > 1e-6}
    assert seen == pred, (seen ^ pred)
    # 3. duty: 25 % keeps even harmonics except n=4k
    assert harmonic_rel_amp(4, 0.25) < 1e-12 and harmonic_rel_amp(2, 0.25) > 0.1
    # 4. verdicts: unfiltered 7.074 probe into a 7.074 passband is IN_BAND; 12.5 into 7.074 has 5th..? computed, not asserted
    assert analyse(7.074e6, 7.074e6, m)["verdict"] == "IN_BAND"
    # 5. filter: a 5th-order 10 MHz LPF removes the far harmonics so more cases become CLEAN
    mf = Model(rise_ns=0, lpf_hz=10e6, lpf_order=5, floor_dbc=-70)
    a = sum(analyse(f, 7.074e6, m)["verdict"] == "CLEAN_OUT" for f in rng("0.2e6:5e6:100e3"))
    b = sum(analyse(f, 7.074e6, mf)["verdict"] == "CLEAN_OUT" for f in rng("0.2e6:5e6:100e3"))
    assert b >= a, (a, b)
    # 6. negative-frequency image: center 1.0 MHz passband reaches below 0, so a 0.01 MHz alias is in band
    assert bool(in_band(np.array([10e3]), 1e6 - 1.024e6, 1e6 + 1.024e6)[0])
    print("selftest ok"); return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]: sys.exit(selftest())
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["explain", "sweep"])
    ap.add_argument("--f0", type=float); ap.add_argument("--center", type=float)
    ap.add_argument("--f0-range"); ap.add_argument("--centers")
    ap.add_argument("--fs", type=float, default=FS_DEFAULT); ap.add_argument("--bw", type=float, default=BW_DEFAULT)
    ap.add_argument("--duty", type=float, default=0.5); ap.add_argument("--rise-ns", type=float, default=2.0)
    ap.add_argument("--lpf-hz", type=float); ap.add_argument("--lpf-order", type=int, default=5)
    ap.add_argument("--floor-dbc", type=float, default=-70.0)
    ap.add_argument("--ppm-probe", type=float, default=20.0); ap.add_argument("--ppm-adc", type=float, default=35.0)
    ap.add_argument("--transition-hz", type=float, default=100e3); ap.add_argument("--max-hz", type=float, default=500e6)
    ap.add_argument("--csv"); ap.add_argument("--md")
    a = ap.parse_args()
    m = Model(a.fs, a.bw, a.duty, a.rise_ns, a.lpf_hz, a.lpf_order, a.floor_dbc, a.ppm_probe, a.ppm_adc, a.transition_hz, a.max_hz)
    if a.mode == "explain":
        explain(a.f0, a.center, m)
    else:
        rows = sweep(rng(a.f0_range), [float(c) for c in a.centers.split(",")], m)
        from collections import Counter
        for c in sorted({r["center"] for r in rows}):
            cnt = Counter(r["verdict"] for r in rows if r["center"] == c)
            print(f"center {c/1e6:.3f} MHz: " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items())))
        if a.csv: write_csv(rows, a.csv)
        if a.md: write_md(rows, m, a.md)
