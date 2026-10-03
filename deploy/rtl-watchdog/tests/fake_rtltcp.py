#!/usr/bin/env python3
"""Fake rtl_tcp server for ss-guard tests.

Listens on 127.0.0.1:<port>, sends the 12-byte dongle info with the RTL0
magic, then behaves per mode:
  healthy: streams I/Q bytes continuously
  stall:   sends the magic, then never sends I/Q (the wedge the guard hunts)

Records every byte received from clients into recfile -- the guard must send
nothing (no tuner commands, ever). Writes readyfile once listening.

Usage: fake_rtltcp.py <healthy|stall> <port> <recfile> <readyfile>
"""
import socket
import sys
import threading
import time


def main():
    mode = sys.argv[1]
    port = int(sys.argv[2])
    recfile = sys.argv[3]
    readyfile = sys.argv[4]
    stop = threading.Event()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(5)
    srv.settimeout(0.5)
    with open(readyfile, "w") as f:
        f.write("ready")

    def handle(conn):
        got = b""
        try:
            conn.sendall(b"RTL0" + b"\x00" * 8)  # 12-byte dongle info
            if mode == "healthy":
                deadline = time.time() + 300
                chunk = bytes(4096)
                while time.time() < deadline and not stop.is_set():
                    try:
                        conn.sendall(chunk)
                    except OSError:
                        break
                    time.sleep(0.005)
            # Drain anything the client sent (expect: nothing) until close.
            conn.settimeout(0.5)
            while not stop.is_set():
                try:
                    d = conn.recv(4096)
                except socket.timeout:
                    continue
                if not d:
                    break
                got += d
        except OSError:
            pass
        finally:
            with open(recfile, "ab") as f:
                f.write(got)
            try:
                conn.close()
            except OSError:
                pass

    try:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            threading.Thread(target=handle, args=(conn,), daemon=True).start()
    finally:
        stop.set()
        srv.close()


main()
