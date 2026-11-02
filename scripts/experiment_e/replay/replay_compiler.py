#!/usr/bin/env python3
"""Experiment E replay compiler: CSV or SigMF annotations -> one deterministic manifest.

The manifest is the single artefact that runs against the mock device today and, once separately
approved, against the real keyed source. It contains the segments, the exact serial command schedule
for keyed_source.ino, the limits it was validated against, and a SHA-256 over canonical JSON.

Every limit must be given explicitly; there are no defaults, so a manifest can never be valid "by
accident". Anything the firmware cannot produce is a compile error, never a silent approximation.

Input formats
  CSV     header: t_s,dur_s,freq_hz,modulation[,key_mhz,duty_pct,drive_ma]   ('#' comment lines ok)
          t_s and dur_s are exact to 1 ms (at most 3 decimals, otherwise an error).
  SigMF   .sigmf-meta JSON. Each annotation becomes one segment: time from core:sample_start /
          core:sample_count / global core:sample_rate (rounded half-up to 1 ms, the error is recorded),
          frequency = centre of core:freq_lower_edge..core:freq_upper_edge, and the annotation must carry
          "scythe:modulation" (and "scythe:key_mhz", "scythe:duty_pct" for ook). Annotation bandwidth must
          not exceed --max-bw-hz. Annotations without a supported modulation are rejected, not skipped.

Supported modulations: cw (steady carrier), ook (square-wave keyed carrier, firmware K command).
Everything else (am, fm, ssb, psk, qam, fsk, chirp, ...) is PROHIBITED: the Si5351 firmware cannot
represent it and an approximation would misstate the experiment.

usage: replay_compiler.py compile INPUT --kind csv|sigmf --out manifest.json
          --source-provenance recorded|synthetic|simulated
          --freq-min-hz N --freq-max-hz N --max-duration-s N --channels 1 [--max-bw-hz N]
"""
import sys, os, json, csv, hashlib, argparse
from decimal import Decimal, InvalidOperation
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mock_device as hw

SCHEMA = "exp_e_replay_manifest/1"
SUPPORTED = ("cw", "ook")
PROHIBITED = ("am", "fm", "ssb", "usb", "lsb", "psk", "bpsk", "qpsk", "qam", "fsk", "gfsk", "msk", "chirp", "lfm",
              "ofdm", "dsss", "noise", "arbitrary", "iq")
PROVENANCE = ("recorded", "synthetic", "simulated")
FIRST_SEGMENT_MIN_MS = 200      # room for the ARM/D preamble
RETUNE_LEAD_MS = 50             # F is sent this long before the carrier is enabled (set_freq takes ms)
MIN_GAP_MS = 100                # off time between segments; the retune lands inside it
EDGE_GUARD_US = 1000           # a segment may not end within this of a key edge (ISR edge vs K 0 race)
PING_EVERY_MS = 10_000          # dead-man is 30 s
TAIL_MS = 100                   # X is sent this long after the last segment ends


class CompileError(Exception):
    pass


def sha256_bytes(b): return hashlib.sha256(b).hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _ms_exact(txt, what, row):
    try:
        d = Decimal(str(txt).strip())
    except InvalidOperation:
        raise CompileError(f"row {row}: {what}={txt!r} is not a number")
    ms = d * 1000
    if ms != ms.to_integral_value():
        raise CompileError(f"row {row}: {what}={txt} is not exact to 1 ms (refusing to round silently)")
    return int(ms)


def _int(txt, what, row):
    try:
        d = Decimal(str(txt).strip())
    except InvalidOperation:
        raise CompileError(f"row {row}: {what}={txt!r} is not a number")
    if d != d.to_integral_value():
        raise CompileError(f"row {row}: {what}={txt} must be a whole number")
    return int(d)


def read_csv(path):
    with open(path, "rb") as fh: raw = fh.read()
    lines = [l for l in raw.decode("utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if not lines: raise CompileError("empty CSV")
    rd = csv.DictReader(lines)
    allowed = {"t_s", "dur_s", "freq_hz", "modulation", "key_mhz", "duty_pct", "drive_ma"}
    need = {"t_s", "dur_s", "freq_hz", "modulation"}
    cols = set(rd.fieldnames or [])
    if not need <= cols: raise CompileError(f"CSV missing columns {sorted(need - cols)}")
    if cols - allowed: raise CompileError(f"CSV has unknown columns {sorted(cols - allowed)}")
    segs = []
    for i, r in enumerate(rd, start=2):
        s = dict(src_row=i, t_ms=_ms_exact(r["t_s"], "t_s", i), dur_ms=_ms_exact(r["dur_s"], "dur_s", i),
                 freq_hz=_int(r["freq_hz"], "freq_hz", i), modulation=(r["modulation"] or "").strip().lower())
        for k in ("key_mhz", "duty_pct", "drive_ma"):
            if r.get(k) not in (None, ""): s[k] = _int(r[k], k, i)
        segs.append(s)
    return raw, segs, {}


def read_sigmf(path, max_bw_hz):
    with open(path, "rb") as fh: raw = fh.read()
    meta = json.loads(raw.decode("utf-8"))
    g = meta.get("global", {})
    rate = g.get("core:sample_rate")
    if not rate: raise CompileError("SigMF global core:sample_rate missing")
    rate = Fraction(str(rate))
    notes = {"sample_rate": str(rate), "sigmf_datatype": g.get("core:datatype")}
    data = path.replace(".sigmf-meta", ".sigmf-data")
    if os.path.exists(data) and g.get("core:sha512"):
        h = hashlib.sha512()
        with open(data, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 22), b""): h.update(chunk)
        if h.hexdigest() != g["core:sha512"]: raise CompileError("SigMF dataset does not match core:sha512")
        notes["dataset_sha512_verified"] = True
    else:
        notes["dataset_sha512_verified"] = False
    segs, qerr = [], Fraction(0)
    for i, a in enumerate(meta.get("annotations", [])):
        for k in ("core:sample_start", "core:sample_count", "core:freq_lower_edge", "core:freq_upper_edge"):
            if k not in a: raise CompileError(f"annotation {i}: missing {k}")
        mod = str(a.get("scythe:modulation", "")).strip().lower()
        if not mod: raise CompileError(f"annotation {i}: no scythe:modulation (refusing to guess from label {a.get('core:label')!r})")
        lo, hi = Fraction(str(a["core:freq_lower_edge"])), Fraction(str(a["core:freq_upper_edge"]))
        if hi < lo: raise CompileError(f"annotation {i}: freq edges reversed")
        if max_bw_hz is None: raise CompileError("--max-bw-hz is required for SigMF input")
        if hi - lo > max_bw_hz: raise CompileError(f"annotation {i}: bandwidth {float(hi-lo):.0f} Hz > --max-bw-hz {max_bw_hz}")
        def to_ms(n):
            x = Fraction(int(n)) * 1000 / rate
            ms = int(x + Fraction(1, 2)) if x >= 0 else -int(-x + Fraction(1, 2))
            return ms, abs(x - ms)
        t_ms, e1 = to_ms(a["core:sample_start"]); d_ms, e2 = to_ms(a["core:sample_count"])
        qerr = max(qerr, e1, e2)
        c = (lo + hi) / 2
        if c != c.__floor__(): raise CompileError(f"annotation {i}: centre frequency {float(c)} Hz is not a whole number")
        s = dict(src_row=i, t_ms=t_ms, dur_ms=d_ms, freq_hz=int(c), modulation=mod)
        for k, kk in (("scythe:key_mhz", "key_mhz"), ("scythe:duty_pct", "duty_pct"), ("scythe:drive_ma", "drive_ma")):
            if k in a: s[kk] = int(a[k])
        segs.append(s)
    notes["time_quantisation_error_ms_max"] = float(qerr)
    return raw, segs, notes


def validate(segs, lim):
    if lim["channels"] != 1:
        raise CompileError(f"channels={lim['channels']}: the firmware drives CLK0 only (channels must be 1)")
    if not (hw.MIN_HZ <= lim["freq_min_hz"] <= lim["freq_max_hz"] <= hw.MAX_HZ):
        raise CompileError(f"freq limits must lie within the hardware envelope [{hw.MIN_HZ}, {hw.MAX_HZ}] and be ordered")
    if lim["max_duration_s"] <= 0: raise CompileError("max_duration_s must be positive")
    if not segs: raise CompileError("no segments")
    segs.sort(key=lambda s: (s["t_ms"], s["src_row"]))
    prev_end = None
    for n, s in enumerate(segs):
        where = f"segment {n} (source row {s['src_row']})"
        m = s["modulation"]
        if m in PROHIBITED: raise CompileError(f"{where}: modulation {m!r} is prohibited (firmware supports only {SUPPORTED})")
        if m not in SUPPORTED: raise CompileError(f"{where}: unsupported modulation {m!r} (supported: {SUPPORTED})")
        if s["dur_ms"] <= 0: raise CompileError(f"{where}: non-positive duration")
        if not (lim["freq_min_hz"] <= s["freq_hz"] <= lim["freq_max_hz"]):
            raise CompileError(f"{where}: {s['freq_hz']} Hz outside explicit bounds [{lim['freq_min_hz']}, {lim['freq_max_hz']}]")
        if n == 0 and s["t_ms"] < FIRST_SEGMENT_MIN_MS: raise CompileError(f"{where}: first segment must start at >= {FIRST_SEGMENT_MIN_MS} ms")
        if prev_end is not None:
            if s["t_ms"] < prev_end: raise CompileError(f"{where}: overlaps the previous segment (one channel)")
            if s["t_ms"] - prev_end < MIN_GAP_MS: raise CompileError(f"{where}: gap {s['t_ms']-prev_end} ms < {MIN_GAP_MS} ms (retune needs an off gap)")
        prev_end = s["t_ms"] + s["dur_ms"]
        if prev_end + TAIL_MS > lim["max_duration_s"] * 1000:
            raise CompileError(f"{where}: ends at {prev_end} ms, over max_duration_s={lim['max_duration_s']}")
        s.setdefault("drive_ma", 2)
        if s["drive_ma"] not in (2, 4, 6, 8): raise CompileError(f"{where}: drive_ma must be 2|4|6|8")
        if m == "cw":
            for k in ("key_mhz", "duty_pct"):
                if k in s: raise CompileError(f"{where}: {k} given for cw")
            s["key_mhz"] = s["duty_pct"] = 0
        else:
            if "key_mhz" not in s: raise CompileError(f"{where}: ook needs key_mhz")
            s.setdefault("duty_pct", 50)
            if not (hw.KEY_MHZ_RANGE[0] <= s["key_mhz"] <= hw.KEY_MHZ_RANGE[1]):
                raise CompileError(f"{where}: key_mhz {s['key_mhz']} outside {hw.KEY_MHZ_RANGE}")
            if not (hw.DUTY_RANGE[0] <= s["duty_pct"] <= hw.DUTY_RANGE[1]):
                raise CompileError(f"{where}: duty_pct {s['duty_pct']} outside {hw.DUTY_RANGE}")
            P = Fraction(10**9, s["key_mhz"]); on = P * s["duty_pct"] / 100; dur_us = s["dur_ms"] * 1000
            r = dur_us % P                                  # position of the segment end inside a key period
            near = min(r, P - r, abs(r - on))
            if near < EDGE_GUARD_US:
                raise CompileError(f"{where}: ends {float(near):.0f} us from a key edge; the final edge would race the stop "
                                   f"command and emit a spurious pulse. Change the duration by at least {EDGE_GUARD_US} us.")
            period_ms = 1_000_000 / s["key_mhz"]
            if s["dur_ms"] < 2 * period_ms:
                raise CompileError(f"{where}: shorter than two key periods ({period_ms:.1f} ms each)")
    drives = {s["drive_ma"] for s in segs}
    if len(drives) > 1: raise CompileError(f"mixed drive strengths {sorted(drives)}: not supported in manifest/1")
    return segs


def commands_for(segs):
    cmds = [(0, "V 1"), (0, f"D {segs[0]['drive_ma']}"), (0, "ARM 1")]
    for s in segs:
        t0, t1 = s["t_ms"], s["t_ms"] + s["dur_ms"]
        cmds.append((t0 - RETUNE_LEAD_MS, f"F {s['freq_hz']}"))
        if s["modulation"] == "cw":
            cmds += [(t0, "E 1"), (t1, "E 0")]
        else:
            cmds += [(t0, f"K {s['key_mhz']} {s['duty_pct']}"), (t1, "K 0")]
    end = segs[-1]["t_ms"] + segs[-1]["dur_ms"] + TAIL_MS
    cmds.append((end, "X"))
    t = PING_EVERY_MS                                     # keep the dead-man fed through long idle stretches
    while t < end:
        cmds.append((t, "P")); t += PING_EVERY_MS
    order = {c: i for i, c in enumerate(["V", "D", "ARM", "F", "E", "K", "P", "X"])}
    cmds.sort(key=lambda c: (c[0], order.get(c[1].split()[0], 9)))
    # no two successive commands may be more than the dead-man apart
    for a, b in zip(cmds, cmds[1:]):
        if b[0] - a[0] > hw.DEADMAN_MS - 5000: raise CompileError("internal: command gap would trip the dead-man")
    return [{"t_ms": t, "line": l} for t, l in cmds], end


def compile_manifest(path, kind, provenance, limits, max_bw_hz=None):
    if provenance not in PROVENANCE: raise CompileError(f"source provenance must be one of {PROVENANCE}")
    for k in ("freq_min_hz", "freq_max_hz", "max_duration_s", "channels"):
        if limits.get(k) is None: raise CompileError(f"limit {k} is required (no defaults)")
    raw, segs, notes = read_csv(path) if kind == "csv" else read_sigmf(path, max_bw_hz)
    segs = validate(segs, dict(limits))
    cmds, end_ms = commands_for(segs)
    body = {
        "schema": SCHEMA,
        "source": {"kind": kind, "name": os.path.basename(path), "sha256": sha256_bytes(raw),
                   "provenance": provenance, "notes": notes},
        "limits": dict(limits, allowed_modulations=list(SUPPORTED), prohibited_modulations=list(PROHIBITED),
                       max_bw_hz=max_bw_hz),
        "hardware_envelope": {"min_hz": hw.MIN_HZ, "max_hz": hw.MAX_HZ, "deadman_ms": hw.DEADMAN_MS,
                              "key_mhz": list(hw.KEY_MHZ_RANGE), "duty_pct": list(hw.DUTY_RANGE), "firmware": "keyed_source.ino"},
        "timing": {"retune_lead_ms": RETUNE_LEAD_MS, "min_gap_ms": MIN_GAP_MS, "ping_every_ms": PING_EVERY_MS, "end_ms": end_ms},
        "segments": [{k: s[k] for k in ("t_ms", "dur_ms", "freq_hz", "modulation", "key_mhz", "duty_pct", "drive_ma", "src_row")} for s in segs],
        "commands": cmds,
    }
    body["manifest_sha256"] = sha256_bytes(canonical(body).encode())
    return body


def verify_hash(m):
    b = {k: v for k, v in m.items() if k != "manifest_sha256"}
    return sha256_bytes(canonical(b).encode()) == m.get("manifest_sha256")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["compile", "verify"]); ap.add_argument("input")
    ap.add_argument("--kind", choices=["csv", "sigmf"]); ap.add_argument("--out")
    ap.add_argument("--source-provenance"); ap.add_argument("--max-bw-hz", type=int)
    ap.add_argument("--freq-min-hz", type=int); ap.add_argument("--freq-max-hz", type=int)
    ap.add_argument("--max-duration-s", type=int); ap.add_argument("--channels", type=int)
    a = ap.parse_args()
    if a.cmd == "verify":
        m = json.load(open(a.input)); ok = verify_hash(m); print("hash ok" if ok else "HASH MISMATCH"); sys.exit(0 if ok else 1)
    try:
        m = compile_manifest(a.input, a.kind, a.source_provenance,
                             dict(freq_min_hz=a.freq_min_hz, freq_max_hz=a.freq_max_hz, max_duration_s=a.max_duration_s, channels=a.channels), a.max_bw_hz)
    except CompileError as e:
        sys.exit(f"COMPILE ERROR: {e}")
    s = json.dumps(m, indent=1, sort_keys=True)
    if a.out: open(a.out, "w").write(s + "\n")
    print(f"{len(m['segments'])} segments, {len(m['commands'])} commands, sha256 {m['manifest_sha256']}")
