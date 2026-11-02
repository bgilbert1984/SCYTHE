#!/usr/bin/env python3
"""Experiment E Stage 0B re-capture: same sequence as stage0b_capture.py, but writes to a durable directory
and logs everything needed to reproduce it. Stdlib only; run on the host that serves rtl_tcp on 127.0.0.1:1234.

usage: stage0b_recapture.py OUT_DIR [--antenna "free text declared by the operator"]

Each capture: direct sampling Q-branch (rtl_tcp cmd 0x09 = 2), set centre frequency (cmd 0x01), discard 2 s of
settling, then record exactly SECS of u8 I/Q. The tuner state is restored (direct off, 100 MHz) in a finally block.
The antenna / terminator is NOT detectable by software; it is recorded as declared by the operator.
Writes capture_log.json after every capture, so an interrupted run still documents what exists.
"""
import socket, struct, sys, time, os, json, hashlib

RATE = 2_048_000
CFG = [("q1", 12_500_000, 60), ("am", 1_000_000, 60), ("hf7", 7_074_000, 60), ("hf5", 5_000_000, 60),
       ("q2", 12_500_000, 60), ("am_120s", 1_000_000, 120)]


def main():
    args = sys.argv[1:]
    if not args: sys.exit(__doc__)
    out = args[0]; antenna = "not recorded"
    if "--antenna" in args: antenna = args[args.index("--antenna") + 1]
    os.makedirs(out, exist_ok=True)
    s = socket.create_connection(("127.0.0.1", 1234), timeout=20)
    hdr = b""
    while len(hdr) < 12: hdr += s.recv(12 - len(hdr))
    magic, tuner, ngains = hdr[:4].decode("ascii", "replace"), struct.unpack(">I", hdr[4:8])[0], struct.unpack(">I", hdr[8:12])[0]
    log = {"schema": "exp_e_stage0b_recapture/1", "provenance": "recorded", "host": socket.gethostname(),
           "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rate_hz": RATE,
           "format": "u8 interleaved I,Q", "direct_sampling": "Q-branch (cmd 0x09 = 2)",
           "gain": "rtl_tcp default (no gain command sent)", "rtl_tcp_header": {"magic": magic, "tuner_type": tuner, "gain_count": ngains},
           "antenna_declared_by_operator": antenna, "captures": []}

    def save():
        json.dump(log, open(os.path.join(out, "capture_log.json"), "w"), indent=1)

    def cmd(c, p): s.sendall(struct.pack(">BI", c, p))

    def rd(n, fh=None, h=None):
        left = n
        while left > 0:
            x = s.recv(min(262144, left))
            if not x: raise EOFError("rtl_tcp closed the stream")
            left -= len(x)
            if fh: fh.write(x)
            if h: h.update(x)

    try:
        for name, f, secs in CFG:
            cmd(0x09, 2); cmd(0x01, f); time.sleep(0.5); rd(RATE * 2 * 2)
            path = os.path.join(out, name + ".u8"); h = hashlib.sha256(); t0 = time.time()
            with open(path, "wb") as fh: rd(RATE * 2 * secs, fh, h)
            fsync_fd = os.open(path, os.O_RDONLY); os.fsync(fsync_fd); os.close(fsync_fd)
            log["captures"].append({"name": name, "centre_hz": f, "seconds": secs, "bytes": os.path.getsize(path),
                                    "sha256": h.hexdigest(), "start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
                                    "wall_seconds": round(time.time() - t0, 2)})
            save(); print(name, f, "ok", os.path.getsize(path), flush=True)
        log["complete"] = True
    except BaseException as e:
        log["complete"] = False; log["error"] = repr(e); save(); print("ERROR", repr(e), flush=True); raise
    finally:
        try:
            cmd(0x09, 0); cmd(0x01, 100_000_000); time.sleep(0.3)
            log["restored"] = "direct sampling off, centre 100 MHz"
        except Exception as e:
            log["restored"] = f"RESTORE FAILED: {e!r}"
        s.close(); log["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); save()
        print(log["restored"], flush=True)


if __name__ == "__main__":
    main()
