#!/usr/bin/env python3
"""Run a replay manifest against the mock device (simulation) or, with approval, a serial port.

usage: replay_run.py mock   MANIFEST [--json run.json]
       replay_run.py serial MANIFEST --port /dev/ttyUSBn --approval approval.json     (UNTESTED, see below)

Simulation (mock) results are provenance "simulated". They check that the compiled schedule, the
firmware protocol and the key arithmetic agree; they are never evidence about RF behaviour.

Verification: the expected on-intervals are derived ANALYTICALLY from the manifest segments (exact
Fraction arithmetic), independently of the mock's tick scheduler, and compared with what the mock did.

Hardware path: the serial transport is refused unless an approval record names this exact manifest hash
and has not expired. The record is a plain JSON file, not a signature: it prevents running the wrong
or a stale manifest by mistake; it does not authenticate the approver. The serial transport has never
been run against a device (no hardware, no pyserial here) and must be bench-checked before use.
"""
import sys, os, json, time, datetime, argparse
from fractions import Fraction
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import mock_device as hw
import replay_compiler as rc

TOL_US = 350      # <= 2 ticks + I2C latency: K start quantisation and the deadline base; the schedule carries no drift


class RunError(Exception):
    pass


def expected_intervals_us(manifest):
    """[(on_us, off_us, freq_hz)] from the segments alone (the mock's I2C latency is added by the caller)."""
    out = []
    for s in manifest["segments"]:
        t0, t1 = s["t_ms"] * 1000, (s["t_ms"] + s["dur_ms"]) * 1000
        if s["modulation"] == "cw":
            out.append((Fraction(t0), Fraction(t1), s["freq_hz"]))
        else:
            period = Fraction(10**9, s["key_mhz"]); on = period * s["duty_pct"] / 100
            k = 0
            while t0 + k * period < t1:
                a = t0 + k * period; b = min(a + on, Fraction(t1))
                out.append((a, b, s["freq_hz"])); k += 1
    return out


def run_mock(manifest, i2c_us=hw.I2C_US):
    if not rc.verify_hash(manifest): raise RunError("manifest hash does not match its content")
    d = hw.MockKeyedSource(i2c_us=i2c_us)
    trace, errors = [], []
    for c in manifest["commands"]:
        d.advance_to(c["t_ms"] * 1000)
        reps = d.send(c["line"])
        trace.append({"t_ms": c["t_ms"], "cmd": c["line"], "reply": reps})
        if any(r.startswith("ERR") for r in reps):
            errors.append(f"t={c['t_ms']} ms {c['line']!r} -> {reps}")
            break
    d.advance_to(manifest["timing"]["end_ms"] * 1000 + 1_000_000)
    ver = verify_against_expectation(manifest, d, i2c_us)
    return {"provenance": "simulated", "manifest_sha256": manifest["manifest_sha256"], "device": "MockKeyedSource",
            "i2c_us": i2c_us, "command_errors": errors, "verification": ver, "final_status": d.send("?")[0],
            "gate_events": len(d.gate_log), "trace": trace}


def verify_against_expectation(manifest, d, i2c_us):
    exp = expected_intervals_us(manifest); got = d.rf_intervals()
    problems = []
    if len(exp) != len(got):
        problems.append(f"interval count: expected {len(exp)}, mock produced {len(got)}")
    max_err = 0.0
    for i, (e, g) in enumerate(zip(exp, got)):
        if g[1] is None: problems.append(f"interval {i}: never turned off"); continue
        for name, ev, gv in (("on", e[0] + i2c_us, g[0]), ("off", e[1] + i2c_us, g[1])):
            err = abs(float(ev) - gv); max_err = max(max_err, err)
            if err > TOL_US: problems.append(f"interval {i} {name}: expected {float(ev):.0f} us, got {gv} us (err {err:.0f})")
        if g[2] != e[2]: problems.append(f"interval {i}: freq expected {e[2]}, got {g[2]}")
    if got and got[-1][1] is None: problems.append("output left on at end of run")
    if d.out_on or d.armed: problems.append(f"end state not safe: out_on={d.out_on} armed={d.armed}")
    return {"ok": not problems, "intervals_expected": len(exp), "intervals_mock": len(got),
            "max_edge_error_us": round(max_err, 1), "tolerance_us": TOL_US, "problems": problems[:20]}


# ---------------- hardware path: approval gate + untested serial transport ----------------
def check_approval(manifest, approval, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    for k in ("manifest_sha256", "approver", "approved_utc", "expires_utc", "scope"):
        if k not in approval: raise RunError(f"approval missing {k}")
    if approval["scope"] != "conducted-bench": raise RunError("approval scope must be 'conducted-bench'")
    if approval["manifest_sha256"] != manifest["manifest_sha256"]: raise RunError("approval is for a different manifest")
    if not rc.verify_hash(manifest): raise RunError("manifest hash does not match its content")
    p = lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if now < p(approval["approved_utc"]): raise RunError("approval not yet valid")
    if now >= p(approval["expires_utc"]): raise RunError("approval expired")
    return True


class SerialTransport:
    """UNTESTED. Real-time executor over a serial port; construct only after check_approval()."""
    def __init__(self, port, manifest, approval):
        check_approval(manifest, approval)
        try:
            import serial
        except ImportError:
            raise RunError("pyserial is not installed")
        self.ser = serial.Serial(port, 115200, timeout=0.2); time.sleep(2.0)   # Uno resets on open
        self.ser.reset_input_buffer()

    def run(self, manifest):
        t0 = time.monotonic(); trace = []
        try:
            for c in manifest["commands"]:
                while (time.monotonic() - t0) * 1000 < c["t_ms"]: time.sleep(0.001)
                self.ser.write((c["line"] + "\n").encode()); rep = self.ser.readline().decode(errors="replace").strip()
                trace.append({"t_ms": c["t_ms"], "cmd": c["line"], "reply": rep})
                if rep.startswith("ERR"): raise RunError(f"device refused {c['line']!r}: {rep}")
        finally:
            self.ser.write(b"X\n")                       # always leave the output off
        return {"provenance": "measured-control-only", "trace": trace}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", choices=["mock", "serial"]); ap.add_argument("manifest")
    ap.add_argument("--json"); ap.add_argument("--port"); ap.add_argument("--approval")
    a = ap.parse_args()
    m = json.load(open(a.manifest))
    try:
        if a.target == "mock":
            r = run_mock(m)
            v = r["verification"]
            print(f"simulated run of {m['manifest_sha256'][:12]}: {r['gate_events']} gate events, "
                  f"{'VERIFIED' if v['ok'] and not r['command_errors'] else 'FAILED'}, max edge error {v['max_edge_error_us']} us")
            for p in v["problems"] + r["command_errors"]: print("  ", p)
            if a.json: json.dump(r, open(a.json, "w"), indent=1)
            sys.exit(0 if v["ok"] and not r["command_errors"] else 1)
        else:
            if not (a.port and a.approval): sys.exit("serial needs --port and --approval")
            t = SerialTransport(a.port, m, json.load(open(a.approval)))
            r = t.run(m)
            if a.json: json.dump(r, open(a.json, "w"), indent=1)
    except RunError as e:
        sys.exit(f"REFUSED: {e}")
