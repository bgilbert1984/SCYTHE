"""Validate f1 estimator under mechanism-faithful timestamp conditions (v3 correction).

Generative model (from verified mechanism):
  True output time:  t_k = k*0.0005 + u_k,  u_k ~ Uniform[0, DT_SOLVER_MAX)
  Written timestamp: w_k = round_to_7_sig_digits(t_k)   (%.6e format)
  Signal:            s_k = ringdown(t_k)                (physical, at true times)

The estimator (as the protocol does):
  f_s = 1 / median(diff(w_k))     (from written timestamps)
  Window [0.6, 2.6] by nearest w_k
  FFT on s_k assuming uniform spacing; df_raw = f_s / N
  Hann, 8x pad, log10 magnitude, parabolic refinement; peak in [28.5, 53.0]

Reports maximum observed |f_est - f_true| over the grid. This is an observed
maximum over tested conditions, not a general accuracy bound.
"""
import numpy as np
import sys

FS_NOM = 2000.0
F_REF = 40.7690
T_MEAS = 2.0
PAD = 8
DT_SOLVER_MAX = 1.0602e-6   # from engine log (max of 25,469 DT reports)

def round_7sig(x):
    """Round to 7 significant digits (%.6e format)."""
    return float('%.6e' % x)

def hann(n):
    return np.hanning(n)

def estimate_faithful(freq_true, phase, tau, noise_rms, comp_freq, comp_amp_ratio,
                      t_start_idx, n, interp_on, rng):
    """Full faithful pipeline: generate at true times, estimate from written times."""
    # True times with overshoot
    k = np.arange(t_start_idx, t_start_idx + n)
    u = rng.uniform(0, DT_SOLVER_MAX, n)
    t_true = k * 0.0005 + u
    # Written timestamps (7 sig digits)
    w = np.array([round_7sig(t) for t in t_true])
    # Signal at TRUE times
    sig = np.cos(2 * np.pi * freq_true * t_true + phase)
    if tau is not None and tau > 0:
        sig = sig * np.exp(-t_true / tau)
    if comp_amp_ratio > 0:
        sig = sig + comp_amp_ratio * np.cos(2 * np.pi * comp_freq * t_true + 1.0)
    if noise_rms > 0:
        sig = sig + np.random.default_rng(0).normal(0, noise_rms, n)
    # Estimator: f_s from written timestamps (as protocol does)
    fs_est = 1.0 / np.median(np.diff(w))
    df_raw = fs_est / n
    # Preprocess and FFT (assuming uniform)
    sig = sig - sig.mean()
    sw = sig * hann(n)
    n_pad = n * PAD
    spec = np.fft.rfft(sw, n_pad)
    df_pad = fs_est / n_pad
    freqs = np.fft.rfftfreq(n_pad, 1.0 / fs_est)
    mask = (freqs >= 28.5) & (freqs <= 53.0)
    if interp_on == 'magnitude':
        mag = np.abs(spec[mask])
    elif interp_on == 'power':
        mag = np.abs(spec[mask]) ** 2
    elif interp_on == 'logmag':
        mag = np.log10(np.abs(spec[mask]) + 1e-300)
    else:
        raise ValueError(interp_on)
    kpk = np.argmax(mag)
    if kpk == 0 or kpk == len(mag) - 1:
        return freqs[mask][kpk]
    a, b, c = mag[kpk-1], mag[kpk], mag[kpk+1]
    denom = (a - 2*b + c)
    delta = 0.5*(a - c)/denom if abs(denom) >= 1e-300 else 0.0
    delta = np.clip(delta, -1.0, 1.0)
    return freqs[mask][kpk] + delta * df_pad

def run_grid(interp_on, seed=0):
    rng = np.random.default_rng(seed)
    # Use nominal df_raw for grid placement (0.5 Hz)
    df_raw_nom = 0.5
    k0 = int(round(F_REF / df_raw_nom))
    fracs = [0.0, 0.1, 0.25, 0.4, 0.5]
    phases = [0.0, np.pi/4, np.pi/2, np.pi]
    taus = [None, 2.0, 0.5]
    max_err = 0.0
    worst = None
    count = 0
    n_full = int(T_MEAS * FS_NOM)      # 4000
    n_half = n_full // 2               # 2000
    # Start index: t=0.6 s -> k=1200
    k_start = 1200
    for frac in fracs:
        f_true = (k0 + frac) * df_raw_nom
        for phase in phases:
            for tau in taus:
                for noise_rms, comp_ratio in [(0.0, 0.0), (0.005, 0.0),
                                              (0.0, 0.1), (0.005, 0.1)]:
                    # full window
                    f_est = estimate_faithful(f_true, phase, tau, noise_rms,
                                              255.0, comp_ratio,
                                              k_start, n_full, interp_on, rng)
                    err = abs(f_est - f_true)
                    count += 1
                    if err > max_err:
                        max_err = err
                        worst = (frac, phase, tau, noise_rms, comp_ratio,
                                 'full', f_true, f_est)
                    # half window
                    f_est_h = estimate_faithful(f_true, phase, tau, noise_rms,
                                                255.0, comp_ratio,
                                                k_start, n_half, interp_on, rng)
                    err_h = abs(f_est_h - f_true)
                    count += 1
                    if err_h > max_err:
                        max_err = err_h
                        worst = (frac, phase, tau, noise_rms, comp_ratio,
                                 'half', f_true, f_est_h)
    return max_err, worst, count

if __name__ == '__main__':
    print(f"Mechanism-faithful validation: DT_SOLVER_MAX={DT_SOLVER_MAX*1e6:.4f} us, "
          f"7-sig-digit timestamps")
    print(f"T_meas={T_MEAS} s, pad={PAD}x")
    for interp in ['magnitude', 'power', 'logmag']:
        max_err, worst, count = run_grid(interp)
        print(f"\n--- parabola on {interp}: {count} cases")
        print(f"  max observed |f_est - f_true| = {max_err:.6f} Hz "
              f"({max_err/F_REF*100:.4f}% of f_ref)")
        if worst:
            print(f"  worst: frac={worst[0]}, phase={worst[1]:.2f}, tau={worst[2]}, "
                  f"noise={worst[3]}, comp={worst[4]}, window={worst[5]}")
    print("\nNote: observed maximum over tested conditions; not a general bound.")
