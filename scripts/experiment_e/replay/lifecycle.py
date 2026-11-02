#!/usr/bin/env python3
"""Experiment E lifecycle and kill-switch simulation. No RF is ever energised: the device is the mock.

PROVENANCE: "simulated". It tests the governance logic and measures kill latency against a MODEL of the
firmware; it is not evidence about real hardware behaviour.

Lifecycle   DRAFT -> APPROVED -> RUNNING -> COMPLETED | ABORTED(reason)
            A manifest that fails pre-flight stays DRAFT and NOTHING is sent to the device (not even V 1).

Supervisor  A host-side process that mediates every command and keeps re-authorising while the device
            runs on its own (K keys autonomously for tens of seconds, so checking approval only at start
            is not enough):
              every 100 ms   approval valid (window, revocation, expiry), manifest hash intact, detector
              every 1 s      "?" poll: link alive, device state plausible
              every command  energising commands need current authorisation; safe commands never do
            On any failure: state ABORTED, X is sent (retried), optional hard_kill() if X cannot be trusted.
            Safe commands (always allowed): X, ARM 0, E 0, K 0, P, ?     Energising: ARM 1, E 1, K n>0, S.

Fault events (simulated): operator_kill, revoke, expire_at, tamper, host_crash, link_drop, device_err, stuck_rf.

Auditor     Independent of the supervisor. Reads the TRUE RF log (SimHardware.true_log, which can differ from
            what the firmware believes) and checks:
              I1  RF only inside the approval window, and not later than the kill bound after it ended
              I2  the device is never armed before approval
              I3  after a terminal state RF is off within the bound for its cause and stays off
              I4  RF only inside planned segments (plus the bound for faults that stop the supervisor)
              I5  never outside the manifest frequency limits
Findings this simulation documents rather than fixes:
  * Supervisor-less (host crash, link loss) worst-case unattended RF is the firmware dead-man, 30 s.
  * The firmware has no concept of approval: anything holding the serial port can ARM it. Governance is
    host-side only.
  * "Firmware believes off but RF is on" (stuck output) is invisible over serial. It needs an independent
    RF detector and a hard power cut; both are modelled as optional hooks.
"""
import sys, os, datetime, copy
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import mock_device as hw
import replay_compiler as rc
import replay_run as rr

EPOCH = datetime.datetime(2026, 11, 3, 0, 0, 0, tzinfo=datetime.timezone.utc)
CHECK_US, POLL_US, LINK_US = 100_000, 1_000_000, 2_000
END_SLACK_US, DETECT_MARGIN_MS = 5_000, 250      # link (2 ms) + I2C (0.12 ms) + margin; detector exclusion around segments
SAFE = {"X", "P", "?"}
BOUND_US = dict(operator_kill=20_000, device_refused=20_000, approval=130_000, manifest_tamper=130_000,
                rf_outside_plan=250_000, link_lost=hw.DEADMAN_MS * 1000 + 2_500_000,
                host_crash=hw.DEADMAN_MS * 1000 + 2_500_000, unexpected_output=1_300_000)


def at(ms): return EPOCH + datetime.timedelta(milliseconds=ms)
def iso(ms): return at(ms).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def make_approval(manifest, approved_ms=-1000, expires_ms=10**9, scope="conducted-bench", sha=None):
    return dict(manifest_sha256=sha or manifest["manifest_sha256"], approver="sim-owner", approved_utc=iso(approved_ms),
                expires_utc=iso(expires_ms), scope=scope)


def is_energising(line):
    t = line.split()
    c = t[0].upper()
    if c == "ARM": return len(t) > 1 and t[1][:1] == "1"
    if c == "E": return len(t) > 1 and t[1][:1] == "1"
    if c == "K": return len(t) > 1 and (hw._uint(t[1]) or 0) > 0
    if c == "S": return True
    return False


def is_safe(line):
    c = line.split()[0].upper()
    return c in SAFE or (c in ("ARM", "E", "K") and not is_energising(line))


class SimHardware(hw.MockKeyedSource):
    """Mock plus the TRUE rf state, which can diverge from the firmware's belief (stuck output)."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.stuck = self.cut = False; self.true_on = False; self.true_log = []   # (t_us, bool)
        self.freq_log_true = self.freq_log
    def _request(self, on, req_us=None):
        super()._request(on, req_us)
        done = self.gate_log[-1]["done_us"]
        if self.cut: return
        if on: self.true_on = True
        elif not self.stuck: self.true_on = False
        self.true_log.append((done, self.true_on))
    def hard_cut(self, t_us):
        self.cut = True; self.true_on = False; self.true_log.append((t_us, False))
    def true_intervals(self, horizon_us):
        out, t_on = [], None
        for t, v in self.true_log:
            if v and t_on is None: t_on = t
            elif not v and t_on is not None: out.append((t_on, t)); t_on = None
        if t_on is not None: out.append((t_on, horizon_us))
        return out


class Link:
    def __init__(self, dev, latency_us=LINK_US):
        self.dev, self.lat = dev, latency_us
        self.drop = []; self.err_next = False; self.sent = []   # sent: (t_us, line, reply|None)
    def send(self, line, t_us):
        if any(a <= t_us < b for a, b in self.drop):
            self.sent.append((t_us, line, None)); return None
        self.dev.advance_to(t_us + self.lat)
        rep = self.dev.send(line)
        if self.err_next and not is_safe(line):
            self.err_next = False; rep = ["ERR injected"]
        self.sent.append((t_us, line, rep)); return rep


class Run:
    """One simulated session: manifest + approval + fault events -> Result."""
    def __init__(self, manifest, approval, events=(), detector=False, hard_kill=False, horizon_s=None):
        self.m = copy.deepcopy(manifest); self.approval = approval
        self.events = sorted(events, key=lambda e: e[0])
        self.dev = SimHardware(); self.link = Link(self.dev)
        self.use_detector, self.use_hard = detector, hard_kill
        self.state, self.reason, self.abort_us, self.trigger_us, self.cause = "DRAFT", None, None, None, None
        self.revoked_ms = None; self.expire_override_ms = None; self.faults = {}
        self.alive = True; self.fails = 0; self.retries = {}; self.cmds = []; self.hard_killed_us = None; self.pending_x = []
        end = self.m["timing"]["end_ms"] * 1000
        self.horizon = int((horizon_s * 1e6) if horizon_s else end + 40_000_000 + 4_000_000)
        self.preflight_error = None

    # ---- authorisation ----
    def authorize(self, now_us, run_end_ms=None):
        now_ms = now_us / 1000
        if self.revoked_ms is not None and now_ms >= self.revoked_ms: raise rr.RunError("approval revoked")
        ap = dict(self.approval)
        if self.expire_override_ms is not None and now_ms >= self.expire_override_ms: raise rr.RunError("approval expired")
        rr.check_approval(self.m, ap, now=at(now_ms))
        if run_end_ms is not None:
            exp = datetime.datetime.fromisoformat(ap["expires_utc"].replace("Z", "+00:00"))
            if exp < at(run_end_ms + 1000): raise rr.RunError("approval does not cover the whole run")
        return True

    def preflight(self):
        try:
            self.authorize(0, run_end_ms=self.m["timing"]["end_ms"])
        except Exception as e:
            self.preflight_error = str(e); return False
        self.state = "APPROVED"; return True

    # ---- abort / kill ----
    def abort(self, reason, t_us, cause):
        if self.state in ("ABORTED", "COMPLETED"): return
        self.state, self.reason, self.abort_us, self.cause = "ABORTED", reason, t_us, cause
        if self.trigger_us is None: self.trigger_us = t_us
        self.pending_x = [t_us + i * 50_000 for i in range(6)]        # X, retried every 50 ms until acknowledged
        self._try_x(t_us)

    def _try_x(self, t):
        rep = self.link.send("X", t)
        if rep and rep[0] == "OK off": self.pending_x = []

    # ---- main loop ----
    def run(self):
        if not self.preflight():
            self.dev.advance_to(self.horizon); return self
        self.state = "RUNNING"
        cmds = self.cmds = [(c["t_ms"] * 1000, c["line"]) for c in self.m["commands"]]
        next_check, next_poll = CHECK_US, POLL_US
        t = 0
        while True:
            cand = [self.horizon]
            if self.events: cand.append(self.events[0][0])
            if self.alive:
                if self.state == "RUNNING":
                    if cmds: cand.append(cmds[0][0])
                    cand += [next_check, next_poll]
                if self.pending_x: cand.append(self.pending_x[0])
            t = max(t, min(cand))
            if t >= self.horizon: break
            while self.events and self.events[0][0] <= t:
                self._fault(self.events.pop(0))
            if not self.alive: continue
            if self.state == "ABORTED":
                while self.pending_x and self.pending_x[0] <= t:
                    ts = self.pending_x.pop(0); self._try_x(ts)
                    if self.pending_x == [] : break
                if not self.pending_x and self.hard_killed_us is None and self.use_hard and self._x_unacked():
                    self.dev.advance_to(t); self.dev.hard_cut(t); self.hard_killed_us = t
                continue
            if self.state != "RUNNING": continue
            if t >= next_check:
                next_check += CHECK_US
                self._periodic(t)
                if self.state != "RUNNING": continue
            if t >= next_poll:
                next_poll += POLL_US; self._poll(t)
                if self.state != "RUNNING": continue
            while cmds and cmds[0][0] <= t and self.state == "RUNNING":
                ts, line = cmds.pop(0); self._command(max(ts, t), line)
        self.dev.advance_to(self.horizon)
        return self

    def _x_unacked(self):
        acks = [r for (_, l, r) in self.link.sent if l == "X" and r and r[0] == "OK off"]
        return not acks

    def _fault(self, ev):
        t, kind = ev[0], ev[1]
        self.faults.setdefault(kind, t)
        if self.trigger_us is None and kind not in ("device_err", "stuck_rf"): self.trigger_us = t   # stuck: RF "should be off" is only known from the plan
        if kind == "operator_kill":
            if self.alive: self.abort("operator kill", t, "operator_kill")     # the kill is a host action; a dead host cannot send X
        elif kind == "revoke": self.revoked_ms = t / 1000
        elif kind == "expire_at": self.expire_override_ms = t / 1000
        elif kind == "tamper": self.m["commands"][-2]["line"] = "F 29999999"          # supervisor's copy only
        elif kind == "host_crash": self.alive = False
        elif kind == "link_drop": self.link.drop.append((t, ev[2]))
        elif kind == "device_err": self.link.err_next = True; self.trigger_us = self.trigger_us
        elif kind == "stuck_rf": self.dev.advance_to(t); self.dev.stuck = True
        else: raise ValueError(kind)

    def _periodic(self, t):
        try:
            self.authorize(t)
        except Exception as e:
            cause = "manifest_tamper" if "hash" in str(e) else "approval"
            self.abort(str(e), t, cause); return
        if self.use_detector:
            ms = t / 1000; segs = self.m["segments"]
            planned = any(s["t_ms"] - DETECT_MARGIN_MS <= ms <= s["t_ms"] + s["dur_ms"] + DETECT_MARGIN_MS for s in segs)
            self.dev.advance_to(t)
            if self.dev.true_on and not planned:
                self.abort("RF detected outside the planned segments", t, "rf_outside_plan")
                if self.use_hard: self.dev.hard_cut(t); self.hard_killed_us = t

    def _poll(self, t):
        rep = self.link.send("?", t)
        if rep is None:
            self.fails += 1
            if self.fails >= 2: self.abort("link lost (no reply to ? twice)", t, "link_lost")
            return
        self.fails = 0
        ms = t / 1000
        end = self.m["timing"]["end_ms"]
        if ms > end + 1500 and "out=1" in rep[0]:
            self.abort("device reports output on after the run", t, "unexpected_output")

    def _command(self, t, line):
        if not is_safe(line):
            try:
                self.authorize(t)
            except Exception as e:
                self.abort(f"command {line!r} refused: {e}", t, "approval"); return
        rep = self.link.send(line, t)
        if rep is None:                                   # unanswered: retry, because a lost "K 0" leaves the output keying
            self.retries["n"] = self.retries.get("n", 0) + 1
            if self.retries["n"] >= 4: self.abort(f"link lost (command {line!r} unanswered 4 times)", t, "link_lost"); return
            self.cmds.insert(0, (t + 100_000, line)); return
        self.retries["n"] = 0
        if rep and rep[0].startswith("ERR"):
            self.abort(f"device refused {line!r}: {rep[0]}", t, "device_refused"); return
        if line == "X": self.state = "COMPLETED"


# ---------------- auditor ----------------
def _merge(iv):
    out = []
    for lo, hi in sorted(iv):
        if out and lo <= out[-1][1]: out[-1] = (out[-1][0], max(out[-1][1], hi))
        else: out.append((lo, hi))
    return out


def audit(run, slack_us=1_000):
    """Return a list of invariant violations, judged on the TRUE RF log and the device gate log."""
    v, H = [], run.horizon
    iv = run.dev.true_intervals(H)
    m, lim = run.m, run.m["limits"]
    ap = run.approval
    a0 = (datetime.datetime.fromisoformat(ap["approved_utc"].replace("Z", "+00:00")) - EPOCH).total_seconds() * 1e6
    a1 = (datetime.datetime.fromisoformat(ap["expires_utc"].replace("Z", "+00:00")) - EPOCH).total_seconds() * 1e6
    valid_end = a1
    for ms in (run.revoked_ms, run.expire_override_ms):
        if ms is not None: valid_end = min(valid_end, ms * 1000)
    if run.state == "DRAFT":
        if run.link.sent: v.append(f"I2 DRAFT run sent {len(run.link.sent)} command(s) to the device")
        if run.dev.gate_log or run.dev.armed: v.append("I2 DRAFT run touched the device")
        return v
    arms = [t for (t, l, r) in run.link.sent if l.upper().startswith("ARM 1") and r]
    if any(t < a0 for t in arms): v.append("I2 device armed before approval")
    cause = run.cause
    segs = [(s["t_ms"] * 1000, (s["t_ms"] + s["dur_ms"]) * 1000) for s in m["segments"]]
    slack = 400_000 if cause == "rf_outside_plan" else END_SLACK_US
    # windows in which the supervisor cannot act, so RF (and approval expiry/revocation) cannot be enforced
    blind = []
    if "host_crash" in run.faults: blind.append((run.faults["host_crash"], run.faults["host_crash"] + BOUND_US["host_crash"]))
    for a_, b_ in run.link.drop: blind.append((a_, b_ + 400_000))
    if cause == "link_lost" and run.trigger_us is not None: blind.append((run.trigger_us, run.trigger_us + BOUND_US["link_lost"]))
    blind = _merge(blind)
    eff_end = valid_end + BOUND_US["approval"]                      # supervisor reaction bound after validity ends
    for lo, hi in blind:
        if lo <= eff_end: eff_end = max(eff_end, hi)
    allowed = _merge([(sa - slack_us, sb + slack) for sa, sb in segs] + blind)
    for a, b in iv:
        if a < a0: v.append(f"I1 RF on at {a} us before approval {a0:.0f}")
        if a >= eff_end: v.append(f"I1 RF turned on at {a} us after validity ended {valid_end:.0f} (+reaction bound/blind window)")
        if b > eff_end: v.append(f"I1 RF stayed on to {b} us, validity ended {valid_end:.0f}, enforcement possible until {eff_end:.0f}")
        if not any(lo <= a and b <= hi for lo, hi in allowed):
            v.append(f"I4 RF {a}..{b} us is not inside a planned segment or a supervisor-blind window")
        f = run.dev.freq_at(a)
        if not (lim["freq_min_hz"] <= f <= lim["freq_max_hz"]): v.append(f"I5 RF on at {f} Hz outside limits")
    if run.state == "ABORTED" or run.cause:
        trig = run.trigger_us if run.trigger_us is not None else run.abort_us
        last_off = max((b for a, b in iv if b <= H and b != H), default=None)
        if iv and iv[-1][1] == H and not run.dev.cut: v.append("I3 RF still on at the end of the simulation")
        elif iv and cause in BOUND_US:
            off = iv[-1][1]
            lat = off - trig if trig is not None else 0
            if lat > BOUND_US[cause]: v.append(f"I3 kill latency {lat/1000:.0f} ms > bound {BOUND_US[cause]/1000:.0f} ms for {cause}")
    if run.dev.armed: v.append("I3 device still armed at the end of the simulation")
    return v


def kill_latency_us(run):
    iv = run.dev.true_intervals(run.horizon)
    if not iv or run.trigger_us is None: return None
    off = iv[-1][1]
    return None if off == run.horizon and not run.dev.cut else max(0, off - run.trigger_us)


if __name__ == "__main__":
    sys.exit(__doc__)
