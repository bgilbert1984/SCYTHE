"""Lossless compression experiments on DISPOSABLE copies of RERUN2 IQ (u8 interleaved I,Q). Every transform is inverted and
every compressed stream is decompressed and compared byte-for-byte before its size is reported."""
import sys, os, subprocess, hashlib, time, json, math, lzma, zlib
import numpy as np

def zstd(data, level, long=False):
    cmd = ["zstd", "-q", "-c", f"-{level}", "-T1"] + (["--long=27"] if long else [])
    c = subprocess.run(cmd, input=data, capture_output=True, check=True).stdout
    d = subprocess.run(["zstd", "-q", "-dc"] + (["--long=27"] if long else []), input=c, capture_output=True, check=True).stdout
    assert d == data, "zstd round-trip FAILED"
    return len(c)

def xz(data, preset=6):
    c = lzma.compress(data, preset=preset); assert lzma.decompress(c) == data; return len(c)

# ---- invertible transforms: forward returns bytes; inverse returns original bytes
def t_deint(b):   a = np.frombuffer(b, np.uint8); return a[0::2].tobytes() + a[1::2].tobytes()
def i_deint(t):   n = len(t) // 2; a = np.empty(2 * n, np.uint8); a[0::2] = np.frombuffer(t[:n], np.uint8); a[1::2] = np.frombuffer(t[n:], np.uint8); return a.tobytes()
def t_delta_plane(b):  # deinterleave then first difference (mod 256) per plane
    a = np.frombuffer(b, np.uint8); out = []
    for p in (a[0::2], a[1::2]): out.append(np.diff(p, prepend=np.uint8(0)).astype(np.uint8).tobytes())
    return out[0] + out[1]
def i_delta_plane(t):
    n = len(t) // 2; a = np.empty(2 * n, np.uint8)
    a[0::2] = np.cumsum(np.frombuffer(t[:n], np.uint8), dtype=np.uint8); a[1::2] = np.cumsum(np.frombuffer(t[n:], np.uint8), dtype=np.uint8); return a.tobytes()
def t_nibble(b):  # deinterleave, then split each plane into high-nibble and low-nibble streams (packed 2/byte)
    a = np.frombuffer(b, np.uint8); out = []
    for p in (a[0::2], a[1::2]):
        hi, lo = p >> 4, p & 15
        out += [((hi[0::2] << 4) | hi[1::2]).tobytes(), ((lo[0::2] << 4) | lo[1::2]).tobytes()]
    return b"".join(out)
def i_nibble(t):
    q = len(t) // 4; parts = [np.frombuffer(t[i * q:(i + 1) * q], np.uint8) for i in range(4)]
    planes = []
    for hi_p, lo_p in ((parts[0], parts[1]), (parts[2], parts[3])):
        hi = np.empty(2 * q, np.uint8); hi[0::2] = hi_p >> 4; hi[1::2] = hi_p & 15
        lo = np.empty(2 * q, np.uint8); lo[0::2] = lo_p >> 4; lo[1::2] = lo_p & 15
        planes.append((hi << 4) | lo)
    a = np.empty(4 * q, np.uint8); a[0::2] = planes[0]; a[1::2] = planes[1]; return a.tobytes()
def t_bitplane(b):  # deinterleave then 8 bit-planes per plane (np.packbits), plane-major
    a = np.frombuffer(b, np.uint8); out = []
    for p in (a[0::2], a[1::2]):
        for k in range(8): out.append(np.packbits((p >> k) & 1).tobytes())
    return b"".join(out)
def i_bitplane(t):
    n = len(t) // 16; planes = []
    for pi in range(2):
        p = np.zeros(n * 8, np.uint8)
        for k in range(8): p |= (np.unpackbits(np.frombuffer(t[(pi * 8 + k) * n:(pi * 8 + k + 1) * n], np.uint8)) << k).astype(np.uint8)
        planes.append(p)
    a = np.empty(2 * len(planes[0]), np.uint8); a[0::2] = planes[0]; a[1::2] = planes[1]; return a.tobytes()
TRANSFORMS = {"identity": (lambda b: b, lambda t: t), "deinterleave": (t_deint, i_deint), "delta_plane": (t_delta_plane, i_delta_plane),
              "nibble_split": (t_nibble, i_nibble), "bitplane": (t_bitplane, i_bitplane)}

def H(counts):
    p = counts[counts > 0] / counts.sum(); return float(-(p * np.log2(p)).sum())
def entropy_bounds(b):
    a = np.frombuffer(b, np.uint8); I, Q = a[0::2], a[1::2]; out = {}
    out["H0_I"] = H(np.bincount(I, minlength=256)); out["H0_Q"] = H(np.bincount(Q, minlength=256))
    for nm, p in (("I", I), ("Q", Q)):
        joint = np.bincount(p[:-1].astype(np.int32) * 256 + p[1:], minlength=65536).astype(float)
        out[f"H1_{nm}|prev"] = H(joint) - H(np.bincount(p[:-1], minlength=256))
    jq = np.bincount(I.astype(np.int32) * 256 + Q, minlength=65536).astype(float)
    out["H_Q|I"] = H(jq) - H(np.bincount(I, minlength=256))
    out["I_sd"], out["Q_sd"] = float(I.std()), float(Q.std()); out["I_mean"], out["Q_mean"] = float(I.mean()), float(Q.mean())
    return out

def main(files):
    rows = []; tot = {}
    for f in files:
        raw = open(f, "rb").read(); n = len(raw)
        for tname, (fw, inv) in TRANSFORMS.items():
            t = fw(raw); assert inv(t) == raw, f"transform {tname} not invertible"
            for cname, fn in (("zstd3", lambda x: zstd(x, 3)), ("zstd6", lambda x: zstd(x, 6)), ("zstd19L", lambda x: zstd(x, 19, True)), ("xz6", lambda x: xz(x, 6))):
                if cname == "zstd19L" and tname not in ("identity", "deinterleave", "delta_plane"): continue
                t0 = time.time(); sz = fn(t); tot.setdefault((tname, cname), [0, 0, 0.0]); r = tot[(tname, cname)]; r[0] += sz; r[1] += n; r[2] += time.time() - t0
        rows.append((os.path.basename(f), entropy_bounds(raw)))
    print("RESULTS  (sum over %d visits; ratio = original/compressed; all round-trips verified)" % len(files))
    print(f"{'transform':14s} {'codec':8s} {'ratio':>7s} {'bits/byte':>9s} {'s/MB':>6s}")
    for (tn, cn), (c, o, s) in sorted(tot.items(), key=lambda kv: kv[1][0]):
        print(f"{tn:14s} {cn:8s} {o/c:7.3f} {8*c/o:9.3f} {s/(o/1e6):6.2f}")
    keys = list(rows[0][1]); print("\nENTROPY BOUNDS (bits/sample, mean over visits; BOUNDS, not achieved codecs):")
    for k in keys: print(f"  {k:12s} {np.mean([r[1][k] for r in rows]):8.4f}")

if __name__ == "__main__": main(sys.argv[1:])
