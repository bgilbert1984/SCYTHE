#!/usr/bin/env python3
"""LTE downlink carrier-frequency estimator for the XTR ppm calibration, Tier A. DRAFT.

Measures where an LTE FDD downlink carrier's centre appears relative to the receiver's LO, from one
byte-exact 256 ms window at 2.048 MS/s. The caller turns that into an offset against the carrier's
nominal frequency (from the channel raster of an identified cell). The calibration quantity is the
offset's dependence on frequency across several cells; this module only measures one cell.

Method (three stages, all from the window itself; nothing is assumed about the cell):
  1. Band-limit to +-0.96 MHz and resample to 1.92 MS/s (exactly 15/16 of the receive rate, so a
     2^19-sample window becomes 491,520 samples). The central six resource blocks (PSS/SSS) lie
     inside this band for every LTE bandwidth.
  2. Cyclic-prefix correlation (lag 128, normal-CP slot structure of 960 samples) in 5 ms blocks,
     folded modulo the slot and matched against the CP positions. The phase of the sum is the
     carrier offset modulo the 15 kHz subcarrier spacing. Blocks are short because the receiver's
     sample clock is itself off by up to ~100 ppm, which slides the symbol grid by ~1 sample per
     5 ms; the phase estimate does not depend on that slide.
  3. Integer-subcarrier ambiguity: after removing the fractional offset, each block's OFDM symbols
     are FFT'd and tested against the three primary-synchronisation sequences at every subcarrier
     shift in the allowed range, using a differential (adjacent-subcarrier) correlation so that
     timing error only adds a constant phase. The (root, shift) that wins in most blocks fixes the
     integer part. The offset is fractional + shift * 15 kHz.

Standard error: the large-sample variance of a correlation-coefficient phase,
(1 - r^2) / (2 L r^2) for L correlated samples and coefficient r, converted to hertz. It is a
stated approximation, not a bound.

Refuses (LteNotDetected) rather than guess: no CP structure, or no PSS winner in enough blocks, or a
result outside the allowed range. A steady tone has CP structure too; it fails the PSS stage.

NOT VERIFIED. The PSS definition used here (Zadoff-Chu roots 25, 29, 34; d_u(n) =
exp(-j*pi*u*n*(n+1)/63) for n = 0..30 and exp(-j*pi*u*(n+1)*(n+2)/63) for n = 31..61; mapped to
subcarriers -31..-1 and 1..31 around the unused DC; transmitted in the last OFDM symbol of slots 0
and 10) was taken from a patent filing and recollection of 3GPP TS 36.211 clause 6.11.1. The
equations on the public mirror are images and were not read. The root indices and the slot/symbol
placement were confirmed as text; the sequence equation and the subcarrier mapping were not. The
test generator in this file uses the same definition, so passing tests cannot catch an error in it.
The calibration plan carries `pss_definition_confirmed` and an unconfirmed definition blocks any
proposal.

Limits: normal-CP FDD only; one cell per window (a second cell on the same channel, as in a
single-frequency network or a strong adjacent-sector overlap, will confuse stage 3); the receiver's
sample-clock scaling of the measured offset is not corrected (at most |offset| * 100 ppm, reported by
the caller).
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

FS_RX = 2048000
FS_LTE = 1920000
NFFT = 128
CP_LEN = (10, 9, 9, 9, 9, 9, 9)                 # normal CP at 1.92 MS/s, per symbol of a 960-sample slot
SYM_LEN = tuple(NFFT + c for c in CP_LEN)
SYM_START = tuple(int(sum(SYM_LEN[:i])) for i in range(7))
SLOT = 960
BLOCK = 10 * SLOT                               # 5 ms: one PSS period
SUBCARRIER_HZ = 15000.0
PSS_ROOTS = (25, 29, 34)
FFT_BACKOFF = 4                                 # start the FFT this many samples inside the CP
assert sum(SYM_LEN) == SLOT


class LteNotDetected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# ------------------------------------------------------------------------------ PSS
def pss_sequence(root: int) -> Dict[int, complex]:
    """subcarrier index k (-31..-1, 1..31) -> d_u. Unverified definition; see the module docstring."""
    out: Dict[int, complex] = {}
    for n in range(62):
        if n <= 30:
            d = np.exp(-1j * np.pi * root * n * (n + 1) / 63.0)
            k = n - 31
        else:
            d = np.exp(-1j * np.pi * root * (n + 1) * (n + 2) / 63.0)
            k = n - 30
        out[k] = complex(d)
    return out


def _pss_pairs(root: int) -> Tuple[np.ndarray, np.ndarray]:
    """Subcarriers k with k+1 also in the sequence, and the differential reference P[k]*conj(P[k+1])."""
    p = pss_sequence(root)
    ks = np.array([k for k in sorted(p) if (k + 1) in p], dtype=int)
    ref = np.array([p[k] * np.conj(p[k + 1]) for k in ks])
    return ks, ref


# -------------------------------------------------------------------------- front end
def resample_to_lte(window: np.ndarray, fs: float) -> np.ndarray:
    if fs != FS_RX or (len(window) * 15) % 16 != 0:
        raise LteNotDetected("WINDOW_NOT_SUPPORTED",
                             f"need fs={FS_RX} and a length divisible by 16, got {fs}, {len(window)}")
    n = len(window)
    m = n * 15 // 16
    spec = np.fft.fft(window)
    out = np.zeros(m, dtype=complex)
    out[: m // 2] = spec[: m // 2]
    out[-(m // 2):] = spec[-(m // 2):]
    return np.fft.ifft(out) * (m / n)


def _mask_sum(arr: np.ndarray) -> np.ndarray:
    """For every slot phase tau, the sum of arr over the CP samples of the seven symbols."""
    two = np.concatenate([arr, arr])
    cs = np.concatenate([[0], np.cumsum(two)])
    tau = np.arange(SLOT)
    out = np.zeros(SLOT, dtype=complex)
    for s in range(7):
        a = tau + SYM_START[s]
        out += cs[a + CP_LEN[s]] - cs[a]
    return out


# ------------------------------------------------------------------------- estimator
def estimate_lte_carrier(window: np.ndarray, fs: float, search_hz: Tuple[float, float],
                         params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = params or {}
    min_r = float(params.get("min_cp_coeff", 0.05))
    min_share = float(params.get("min_pss_vote_share", 0.5))
    x = resample_to_lte(window, fs)
    m_total = len(x)
    nblocks = m_total // BLOCK
    if nblocks < 6:
        raise LteNotDetected("WINDOW_TOO_SHORT", f"{nblocks} blocks of 5 ms")
    used = list(range(1, nblocks - 1))                      # leave the circular-resample edges out
    z = np.conj(x[:-NFFT]) * x[NFFT:]
    e = 0.5 * (np.abs(x[:-NFFT]) ** 2 + np.abs(x[NFFT:]) ** 2)
    taus, c_sel, e_sel = [], [], []
    for b in used:
        seg = slice(b * BLOCK, (b + 1) * BLOCK)
        cm = _mask_sum(z[seg].reshape(10, SLOT).sum(0))
        em = _mask_sum(e[seg].reshape(10, SLOT).sum(0).astype(complex)).real
        tau = int(np.argmax(np.abs(cm)))
        taus.append(tau); c_sel.append(cm[tau]); e_sel.append(em[tau])
    c_sum, e_sum = np.sum(c_sel), float(np.sum(e_sel))
    r = float(abs(c_sum) / e_sum) if e_sum > 0 else 0.0
    if r < min_r:
        raise LteNotDetected("NO_CP_STRUCTURE", f"CP correlation coefficient {r:.3g} < {min_r}")
    eps_hz = float(np.angle(c_sum)) * FS_LTE / (2.0 * np.pi * NFFT)
    l_samples = len(used) * 10 * sum(CP_LEN)
    std_hz = math.sqrt(max(1.0 - r * r, 1e-12) / (2.0 * l_samples * r * r)) * FS_LTE / (2.0 * np.pi * NFFT)

    xc = x * np.exp(-2j * np.pi * eps_hz * np.arange(m_total) / FS_LTE)
    bound = max(abs(search_hz[0]), abs(search_hz[1]))
    m_max = int(math.ceil(bound / SUBCARRIER_HZ)) + 1
    shifts = np.arange(-m_max, m_max + 1)
    refs = {u: _pss_pairs(u) for u in PSS_ROOTS}
    votes: Dict[Tuple[int, int], int] = {}
    scores = []
    for b, tau in zip(used, taus):
        starts, cps = [], []
        for j in range(10):
            for s in range(7):
                starts.append(b * BLOCK + tau + SYM_START[s] + SLOT * j + CP_LEN[s] - FFT_BACKOFF)
        starts = np.array(starts)
        if starts.min() < 0 or starts.max() + NFFT > m_total:
            continue
        y = np.fft.fft(xc[starts[:, None] + np.arange(NFFT)], axis=1)
        yd = y * np.conj(np.roll(y, -1, axis=1))
        best = (-1.0, None, None)
        for u in PSS_ROOTS:
            ks, ref = refs[u]
            for m in shifts:
                bins = (ks + m) % NFFT
                num = np.abs(yd[:, bins] @ np.conj(ref))
                den = np.abs(yd[:, bins]).sum(axis=1) + 1e-30
                sc = float((num / den).max())
                if sc > best[0]:
                    best = (sc, u, int(m))
        scores.append(best[0])
        votes[(best[1], best[2])] = votes.get((best[1], best[2]), 0) + 1
    if not votes:
        raise LteNotDetected("NO_PSS_BLOCKS", "no block could be tested")
    (root, shift), count = max(votes.items(), key=lambda kv: kv[1])
    share = count / sum(votes.values())
    if share < min_share:
        raise LteNotDetected("NO_PSS", f"best PSS hypothesis won {share:.2f} of blocks < {min_share}")
    total = eps_hz + shift * SUBCARRIER_HZ
    if not (search_hz[0] <= total <= search_hz[1]):
        raise LteNotDetected("OUT_OF_RANGE", f"{total:.1f} Hz outside {search_hz}")
    return {"baseband_hz": float(total), "snr_db": float(10 * math.log10(max(r / max(1 - r, 1e-9), 1e-9))),
            "crb_hz": float(std_hz), "cp_coefficient": r, "pss_root": int(root),
            "integer_subcarrier_shift": int(shift), "pss_vote_share": float(share),
            "median_pss_score": float(np.median(scores)), "blocks": len(used)}


# ---------------------------------------------------------------- synthetic generator
def synthetic_lte_clean(n_out: int, cfo_hz: float, rng: np.random.Generator, *, pss_root: int = 29,
                        multipath: bool = False, sample_clock_ppm: float = 0.0) -> np.ndarray:
    """A 5 MHz normal-CP LTE downlink (7.68 MS/s, FFT 512, 300 used subcarriers) as a receiver centred
    on the carrier would see it through a 2.048 MS/s front end: carrier offset `cfo_hz`, optional
    two-tap multipath, optional receiver sample-clock error, unit mean power. Random QPSK on every
    resource element except the PSS (last symbol of slots 0 and 10) and its reserved neighbours.
    Same PSS definition as the estimator: it cannot detect an error in that definition."""
    fs_tx, nfft, used_half = 7.68e6, 512, 150
    cp = (40, 36, 36, 36, 36, 36, 36)
    n_wide = int(round(n_out * fs_tx / FS_RX))
    n_slots = int(math.ceil(n_wide / 3840)) + 3
    kk = np.r_[np.arange(-used_half, 0), np.arange(1, used_half + 1)]
    syms = []
    pss = pss_sequence(pss_root)
    for slot in range(n_slots):
        for s in range(7):
            x = np.zeros(nfft, dtype=complex)
            qpsk = (rng.choice([-1, 1], len(kk)) + 1j * rng.choice([-1, 1], len(kk))) / np.sqrt(2)
            x[kk % nfft] = qpsk
            if s == 6 and slot % 10 == 0:
                for k in range(-36, 37):
                    x[k % nfft] = 0.0
                for k, v in pss.items():
                    x[k % nfft] = v
            t = np.fft.ifft(x) * nfft
            syms.append(np.concatenate([t[-cp[s]:], t]))
    tx = np.concatenate(syms)
    off = int(rng.integers(0, 3840))
    tx = tx[off: off + n_wide + 8]
    t = np.arange(len(tx)) / fs_tx
    tx = tx * np.exp(2j * np.pi * cfo_hz * t)
    if multipath:
        d = 3
        tx = tx + 0.5 * np.exp(1j * 1.1) * np.concatenate([np.zeros(d, complex), tx[:-d]])
    grid = np.arange(n_wide) * (1.0 + sample_clock_ppm * 1e-6)
    wide = np.interp(grid, np.arange(len(tx)), tx.real) + 1j * np.interp(grid, np.arange(len(tx)), tx.imag)
    spec = np.fft.fft(wide)
    out = np.zeros(n_out, dtype=complex)
    out[: n_out // 2] = spec[: n_out // 2]
    out[-(n_out // 2):] = spec[-(n_out // 2):]
    y = np.fft.ifft(out) * (n_out / n_wide)
    return y / np.sqrt(np.mean(np.abs(y) ** 2))


def add_noise(clean: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    sigma2 = 10 ** (-snr_db / 10.0)
    n = len(clean)
    return clean + (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * math.sqrt(sigma2 / 2)
