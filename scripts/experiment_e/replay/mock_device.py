#!/usr/bin/env python3
"""Mock of the Experiment E keyed source (keyed_source.ino) for use without hardware.

It speaks the same line protocol with the same reply strings, limits, dead-man timer and
integer-remainder key schedule as scripts/experiment_e/keyed_source/keyed_source.ino. Parity with the
firmware is enforced by test_replay.py (constants and reply literals are read out of the .ino, and
key-edge times are compared with the compiled C++ firmware simulation).

PROVENANCE: everything this produces is "simulated". It says nothing about real RF.

What it models:    command grammar, arming, limits, dead-man, 100 us tick key schedule with exact
                   remainder carry, request->I2C-complete latency (default 120 us), set_freq latency.
What it does NOT:  loop() stalls and edge coalescing, serial-port timing, the real RF gate
                   (rf_gate_observed), filter/attenuator, anything electrical.

Use in-process:    d = MockKeyedSource(); d.advance_to(t_us); replies = d.send("ARM 1")
Use as a "port":   python3 mock_device.py --pty     (prints a /dev/pts/N path; wall-clock time)
"""
import sys, os, time

MIN_HZ, MAX_HZ, DEADMAN_MS = 100_000, 30_000_000, 30_000
TICK_US, I2C_US, FREQ_US = 100, 120, 4_000
KEY_MHZ_RANGE, DUTY_RANGE, SWEEP_MIN_DWELL_MS = (100, 20_000), (1, 99), 50
LINE_MAX = 63          # char lineBuf[64] leaves 63 usable bytes


class MockKeyedSource:
    def __init__(self, i2c_us=I2C_US, freq_us=FREQ_US, detected=True):
        self.i2c_us, self.freq_us, self.detected = i2c_us, freq_us, detected
        self.now_us = 0
        self.armed = self.base_on = self.out_on = self.verbose = False
        self.freq_hz, self.drive_ma = 7_074_000, 2
        self.keying = self.key_phase_on = False
        self.next_edge_tick = self.key_mhz = self.on_num = self.off_num = self.rem = 0
        self.sweeping = False
        self.sw_stop = self.sw_step = self.sw_dwell_ms = self.sw_next_ms = 0
        self.last_cmd_ms = 0
        self.edge_seq = self.done_count = self.max_lat_us = self.log_seq = 0
        # observable record (ground truth of what the mock did)
        self.gate_log = []          # dicts: seq,on,req_us,done_us
        self.freq_log = [(0, self.freq_hz)]   # (t_us, hz) when the frequency register changed
        self.async_out = []         # (t_us, line) unsolicited serial output
        self.boot_line = "READY unarmed, outputs off" if detected else "ERR si5351 not detected"
        self.async_out.append((0, self.boot_line))

    # ---- time ----
    @property
    def tick(self): return self.now_us // TICK_US
    @property
    def millis(self): return self.now_us // 1000

    def advance_to(self, t_us):
        if t_us < self.now_us: raise ValueError("time went backwards")
        while True:
            ev = self._next_event(t_us)
            if ev is None: break
            self.now_us = ev[0]
            ev[1]()
        self.now_us = t_us

    def _next_event(self, limit_us):
        cands = []
        if self.keying: cands.append((self.next_edge_tick * TICK_US, 0, self._key_edge))
        if self.armed: cands.append(((self.last_cmd_ms + DEADMAN_MS + 1) * 1000, 1, self._deadman))
        if self.sweeping: cands.append((self.sw_next_ms * 1000, 2, self._sweep_step))
        cands = [c for c in cands if self.now_us <= c[0] <= limit_us]
        if not cands: return None
        c = min(cands, key=lambda x: (x[0], x[1]))
        return c[0], c[2]

    # ---- gate path (mirrors pushRequest / serviceGate with an idle loop) ----
    def _request(self, on, req_us=None):
        req = self.now_us if req_us is None else req_us
        self.edge_seq += 1
        done = req + self.i2c_us
        self.log_seq += 1
        self.gate_log.append(dict(seq=self.log_seq, on=bool(on), req_us=req, done_us=done))
        self.done_count += 1
        self.max_lat_us = max(self.max_lat_us, self.i2c_us)
        self.out_on = bool(on)
        if self.verbose:
            self.async_out.append((done, f"GATE {self.log_seq} {1 if on else 0} req={req} done={done} lat={done-req} c=1"))

    def _key_edge(self):
        self.key_phase_on = not self.key_phase_on
        self._request(self.key_phase_on, self.next_edge_tick * TICK_US)
        tot = (self.on_num if self.key_phase_on else self.off_num) + self.rem
        t, self.rem = divmod(tot, self.key_mhz)
        self.next_edge_tick += (t if t else 1)

    def _deadman(self):
        self._all_off()
        self.async_out.append((self.now_us, "DEADMAN off"))

    def _sweep_step(self):
        nxt = self.freq_hz + self.sw_step
        if nxt > self.sw_stop:
            self.sweeping = False
            self.async_out.append((self.now_us, "SWEEP DONE"))
        else:
            self._set_freq(nxt)
            self.sw_next_ms += self.sw_dwell_ms
            if self.verbose: self.async_out.append((self.now_us, f"STEP {nxt} {self.millis}"))

    def _all_off(self):
        self.keying = self.sweeping = self.base_on = self.armed = False
        self._request(False)

    def _set_freq(self, hz):
        if hz < MIN_HZ or hz > MAX_HZ: return False
        self.freq_hz = hz
        self.freq_log.append((self.now_us + self.freq_us, hz))
        return True

    # ---- serial protocol (reply strings are copied from the firmware; test_replay.py checks this) ----
    def send(self, line):
        """Handle one command line at the current time. Returns the list of reply lines.
        Like the firmware's 64-byte line buffer: the 64th character triggers "ERR line too long", is
        dropped, and whatever follows is parsed as a NEW command line."""
        R = []
        while len(line) > LINE_MAX:
            R.append("ERR line too long")
            line = line[LINE_MAX + 1:]
        return R + self._send_line(line)

    def _send_line(self, line):
        self.last_cmd_ms = self.millis
        toks = line.strip().split()
        if not toks: return []
        cmd, a = toks[0].upper(), toks[1:]
        a1, a2, a3, a4 = (a + [None] * 4)[:4]
        R = []
        if cmd == "H":
            R.append("OK H ?  ARM 1|0  F hz  D 2|4|6|8  E 0|1  K mHz duty|K 0  S start stop step dwell_ms  C ppb  V 0|1  P  X")
        elif cmd == "?": R.append(self._status())
        elif cmd == "P": R.append("OK")
        elif cmd == "X": self._all_off(); R.append("OK off")
        elif cmd == "V": self.verbose = bool(a1) and a1[0] == "1"; R.append("OK")
        elif cmd == "ARM":
            if a1 is None: R.append("ERR usage ARM 1|0")
            elif a1[0] == "1":
                if not self.detected: R.append("ERR si5351 not detected")
                else: self.armed = True; R.append("OK armed")
            else: self._all_off(); R.append("OK disarmed")
        elif cmd == "F":
            ok = a1 is not None and _uint(a1) is not None and self._set_freq(_uint(a1))
            R.append("OK" if ok else "ERR freq out of range")
        elif cmd == "D":
            v = _atoi(a1)
            if a1 is None or v not in (2, 4, 6, 8): R.append("ERR drive 2|4|6|8")
            else: self.drive_ma = v; R.append("OK")
        elif cmd == "E":
            if not self._need_armed(R): return R
            self.base_on = a1 is not None and a1[0] == "1"
            if not self.keying: self._request(self.base_on)
            R.append("OK")
        elif cmd == "K":
            if a1 is None: R.append("ERR usage K mHz duty"); return R
            mhz = _uint(a1) or 0
            if mhz == 0:
                self.keying = False; self._request(self.armed and self.base_on); R.append("OK key off"); return R
            if not self._need_armed(R): return R
            duty = _uint(a2) if a2 is not None else 50
            duty = 0 if duty is None else duty
            if not (KEY_MHZ_RANGE[0] <= mhz <= KEY_MHZ_RANGE[1]) or not (DUTY_RANGE[0] <= duty <= DUTY_RANGE[1]):
                R.append("ERR key range"); return R
            self.keying = False
            self._request(True)                                   # edge 0: phase starts ON, at the command
            self.key_mhz = mhz
            self.on_num, self.off_num = 100_000 * duty, 100_000 * (100 - duty)
            ticks, self.rem = divmod(self.on_num, mhz)
            self.next_edge_tick = (self.now_us + self.i2c_us) // TICK_US + ticks   # firmware reads tickCount AFTER the I2C write
            self.key_phase_on = True; self.keying = True
            R.append("OK key on")
        elif cmd == "S":
            if not self._need_armed(R): return R
            if None in (a1, a2, a3, a4): R.append("ERR usage S start stop step dwell_ms"); return R
            start, stop, step, dwell = (_uint(x) or 0 for x in (a1, a2, a3, a4))
            if step == 0 or dwell < SWEEP_MIN_DWELL_MS or start > stop or start < MIN_HZ or stop > MAX_HZ:
                R.append("ERR sweep range"); return R
            self.sw_stop, self.sw_step, self.sw_dwell_ms = stop, step, dwell
            self._set_freq(start)
            self.sweeping = True; self.sw_next_ms = self.millis + dwell
            R.append(f"STEP {start} {self.millis}"); R.append("OK sweep")
        elif cmd == "C":
            if a1 is None: R.append("ERR usage C ppb")
            else: self._set_freq(self.freq_hz); R.append("OK")
        else: R.append("ERR unknown (H for help)")
        return R

    def _need_armed(self, R):
        if not self.armed: R.append("ERR not armed"); return False
        return True

    def _status(self):
        b = lambda x: 1 if x else 0
        return (f"OK armed={b(self.armed)} det={b(self.detected)} f={self.freq_hz} drive={self.drive_ma} base={b(self.base_on)}"
                f" out={b(self.out_on)} key={b(self.keying)} sweep={b(self.sweeping)} edges={self.edge_seq}"
                f" done={self.done_count} coalesced=0 ring_ovf=0 log_drop=0 max_lat_us={self.max_lat_us}")

    # ---- ground truth ----
    def rf_intervals(self):
        """[(on_us, off_us, freq_hz)] of the modelled output (on = I2C-complete; the real RF edge is unobserved)."""
        out, t_on = [], None
        for g in self.gate_log:
            if g["on"] and t_on is None: t_on = g["done_us"]
            elif not g["on"] and t_on is not None:
                out.append((t_on, g["done_us"], self.freq_at(t_on))); t_on = None
        if t_on is not None: out.append((t_on, None, self.freq_at(t_on)))
        return out

    def freq_at(self, t_us):
        f = self.freq_log[0][1]
        for t, hz in self.freq_log:
            if t <= t_us: f = hz
        return f


def _uint(s):
    """strtoul-like: leading digits, else None when there are none (firmware would read 0)."""
    if s is None: return None
    d = ""
    for ch in s:
        if ch.isdigit(): d += ch
        else: break
    return int(d) if d else (0 if s else None)


def _atoi(s):
    if s is None: return 0
    d = ""
    for ch in s:
        if ch.isdigit(): d += ch
        else: break
    return int(d) if d else 0


def serve_pty():
    """Expose the mock on a pseudo-terminal using wall-clock time. For host tools expecting a serial port."""
    import pty, select
    import tty, termios
    master, slave = pty.openpty()
    tty.setraw(slave, termios.TCSANOW)      # no echo, no CR/LF translation: behave like a USB-serial device
    print(os.ttyname(slave), flush=True)
    d = MockKeyedSource(); t0 = time.monotonic(); buf = b""
    os.write(master, (d.boot_line + "\n").encode())
    sent_async = 1
    while True:
        r, _, _ = select.select([master], [], [], 0.02)
        d.advance_to(int((time.monotonic() - t0) * 1e6))
        while sent_async < len(d.async_out):
            os.write(master, (d.async_out[sent_async][1] + "\n").encode()); sent_async += 1
        if not r: continue
        data = os.read(master, 4096)
        if not data: break
        buf += data.replace(b"\r", b"")
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            d.advance_to(int((time.monotonic() - t0) * 1e6))
            for rep in d.send(line.decode(errors="replace")): os.write(master, (rep + "\n").encode())
            while sent_async < len(d.async_out):
                os.write(master, (d.async_out[sent_async][1] + "\n").encode()); sent_async += 1


if __name__ == "__main__":
    if "--pty" in sys.argv: serve_pty()
    else: sys.exit(__doc__)
