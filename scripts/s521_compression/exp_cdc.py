"""Content-defined chunking (gear hash, FastCDC-style normalisation not applied: plain min/avg/max cut) vs fixed-size chunking.
Reports unique vs total bytes, optionally zstd-compressed unique bytes. Operates on disposable copies only."""
import sys, os, hashlib, subprocess, numpy as np
rng = np.random.default_rng(12345); GEAR = rng.integers(0, 2**63, 256, dtype=np.uint64)
def cdc_cuts(data, avg=1 << 16, mn=1 << 14, mx=1 << 18):
    mask = np.uint64(avg - 1); cuts = []; start = 0; n = len(data); a = np.frombuffer(data, np.uint8)
    # rolling gear: h = (h << 1) + G[b] over a 64-bit window; vectorised by blocks
    h = np.uint64(0); i = 0
    g = GEAR[a]
    while start < n:
        end = min(start + mx, n); lo = start + mn
        if lo >= end: cuts.append(end); start = end; continue
        # compute h at each position in [start, end) via scan (python loop over bytes is slow; use cumulative trick with 64-bit shifts)
        seg = g[start:end]; hs = np.empty(len(seg), np.uint64); hh = np.uint64(0)
        # Gear hash only depends on last 64 bytes: h_i = sum_{k<64} G[b_{i-k}] << k
        acc = np.zeros(len(seg), np.uint64)
        for k in range(64):
            if k >= len(seg): break
            acc[k:] += (seg[:len(seg) - k] << np.uint64(k))
        cand = np.nonzero((acc[lo - start:] & mask) == 0)[0]
        cut = (lo + int(cand[0]) + 1) if len(cand) else end
        cuts.append(min(cut, end)); start = min(cut, end)
    return cuts
def chunks(data, mode, size=1 << 16):
    if mode == "fixed": return [data[i:i + size] for i in range(0, len(data), size)]
    cuts = cdc_cuts(data); out = []; s = 0
    for c in cuts: out.append(data[s:c]); s = c
    return out
def run(paths, mode):
    seen = {}; total = 0
    for p in paths:
        d = open(p, "rb").read(); total += len(d)
        for c in chunks(d, mode):
            h = hashlib.sha256(c).digest()
            if h not in seen: seen[h] = c
    uniq = sum(len(c) for c in seen.values())
    z = subprocess.run(["zstd", "-q", "-c", "-6", "-T1"], input=b"".join(seen.values()), capture_output=True).stdout
    return total, uniq, len(z), len(seen)
if __name__ == "__main__":
    mode = sys.argv[1]; t, u, z, n = run(sys.argv[2:], mode)
    print(f"{mode:6s} files={len(sys.argv[2:])} total={t/1e6:.1f}MB unique={u/1e6:.1f}MB ({100*u/t:.2f}%) chunks={n} zstd6(unique)={z/1e6:.1f}MB")
