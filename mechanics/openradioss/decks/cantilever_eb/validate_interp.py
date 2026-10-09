"""Validate parabolic interpolation accuracy for the dynamic f1 estimator.

Tests the frozen estimator pipeline (Hann window, zero-pad, FFT, parabolic
peak refinement) on synthetic ringdown signals spanning:
- fractional-bin offsets (0.0 to 0.5)
- phases (0 to 2*pi)
- decay time constants (none, moderate, strong)
- noise at permitted levels
- competing modes at permitted levels
- full windows and half-windows

Compares parabola on magnitude vs power vs log-magnitude.
"""
import numpy as np

# Frozen protocol parameters (v2 proposal)
FS = 2000.0          # Hz, /TFILE 0.0005 s
F_REF = 40.7690      # Hz
T_MEAS = 2.0         # s, usable measurement (v2: t_end=2.7, start by 0.6)
PAD = 8              # zero-padding factor

def hann(n):
    # Symmetric Hann (NumPy convention): 0.5 - 0.5*cos(2*pi*k/(n-1))
    return np.hanning(n)

def estimate(freq_true, phase, tau, noise_rms, comp_freq, comp_amp_ratio,
             t_start, t_dur, interp_on):
    """Run the estimator pipeline; return estimated frequency."""
    n = int(t_dur * FS)
    t = np.arange(n) / FS + t_start
    # ringdown: decaying cosine
    sig = np.cos(2 * np.pi * freq_true * t + phase)
    if tau is not None and tau > 0:
        sig = sig * np.exp(-t / tau)
    # competing mode
    if comp_amp_ratio > 0:
        sig = sig + comp_amp_ratio * np.cos(2 * np.pi * comp_freq * t + 1.0)
    # noise
    if noise_rms > 0:
        sig = sig + np.random.default_rng(0).normal(0, noise_rms, n)
    # preprocess: remove mean, Hann, zero-pad
    sig = sig - sig.mean()
    w = hann(n)
    sw = sig * w
    n_pad = n * PAD
    spec = np.fft.rfft(sw, n_pad)
    df = FS / n_pad
    # search band [28.5, 53.0]
    freqs = np.fft.rfftfreq(n_pad, 1 / FS)
    mask = (freqs >= 28.5) & (freqs <= 53.0)
    if interp_on == 'magnitude':
        mag = np.abs(spec[mask])
    elif interp_on == 'power':
        mag = np.abs(spec[mask]) ** 2
    elif interp_on == 'logmag':
        mag = np.log10(np.abs(spec[mask]) + 1e-300)
    else:
        raise ValueError(interp_on)
    k = np.argmax(mag)
    # parabolic refinement (need neighbors inside mask)
    if k == 0 or k == len(mag) - 1:
        return freqs[mask][k]  # no refinement possible
    a, b, c = mag[k - 1], mag[k], mag[k + 1]
    denom = (a - 2 * b + c)
    if abs(denom) < 1e-300:
        delta = 0.0
    else:
        delta = 0.5 * (a - c) / denom
    delta = np.clip(delta, -1.0, 1.0)
    f_est = freqs[mask][k] + delta * df
    return f_est

def run_grid(interp_on):
    rng_cases = []
    # fractional-bin offsets: place true freq at bin_center + frac*df_raw
    df_raw = FS / int(T_MEAS * FS)  # raw bin width without padding
    n_raw = int(T_MEAS * FS)
    # pick a base bin near f_ref
    k0 = int(round(F_REF / df_raw))
    fracs = [0.0, 0.1, 0.25, 0.4, 0.5]
    phases = [0.0, np.pi / 4, np.pi / 2, np.pi]
    taus = [None, 2.0, 0.5]  # none, moderate, strong decay
    # noise: permitted level -> signal 100x quiet band; use noise_rms = 0.005 (amp 1.0)
    # competing mode: permitted up to 1/10 amplitude; test at 0.1 and 0.05
    max_err = 0.0
    worst = None
    count = 0
    for frac in fracs:
        f_true = (k0 + frac) * df_raw
        for phase in phases:
            for tau in taus:
                for noise_rms, comp_ratio in [(0.0, 0.0), (0.005, 0.0),
                                              (0.0, 0.1), (0.005, 0.1)]:
                    # full window
                    f_est = estimate(f_true, phase, tau, noise_rms,
                                     255.0, comp_ratio, 0.6, T_MEAS, interp_on)
                    err = abs(f_est - f_true)
                    count += 1
                    if err > max_err:
                        max_err = err
                        worst = (frac, phase, tau, noise_rms, comp_ratio,
                                 'full', f_true, f_est)
                    # half window (first half)
                    f_est_h = estimate(f_true, phase, tau, noise_rms,
                                       255.0, comp_ratio, 0.6, T_MEAS / 2,
                                       interp_on)
                    err_h = abs(f_est_h - f_true)
                    count += 1
                    if err_h > max_err:
                        max_err = err_h
                        worst = (frac, phase, tau, noise_rms, comp_ratio,
                                 'half', f_true, f_est_h)
    return max_err, worst, count

if __name__ == '__main__':
    print(f"FS={FS} Hz, T_meas={T_MEAS} s, raw df={FS/int(T_MEAS*FS):.4f} Hz, pad={PAD}x")
    for interp in ['magnitude', 'power', 'logmag']:
        max_err, worst, count = run_grid(interp)
        print(f"\n--- parabola on {interp}: {count} cases")
        print(f"  max |f_est - f_true| = {max_err:.6f} Hz ({max_err/F_REF*100:.4f}% of f_ref)")
        if worst:
            print(f"  worst: frac={worst[0]}, phase={worst[1]:.2f}, tau={worst[2]}, "
                  f"noise={worst[3]}, comp={worst[4]}, window={worst[5]}")
