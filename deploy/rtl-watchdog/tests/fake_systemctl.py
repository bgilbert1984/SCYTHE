#!/usr/bin/env python3
"""Fake `systemctl --user` for ss-guard tests.

The guard under test calls `systemctl --user is-active scythe-rtl-tcp.service`
and `systemctl --user restart scythe-rtl-tcp.service`; this fake answers both
from files so the tests control the world. Install it as `systemctl` early in
PATH for the duration of a test.

Controlled via $SSGUARD_TEST_FAKESTATE (a temp dir):
  svcstate         service state string for `is-active` (default: active)
  servermode       fake_rtltcp mode to (re)start on `restart` ("none" = nothing)
  systemctl.calls  append-only log of invocations (argv after `systemctl`)
  server.pid       pid of the running fake server, if any

Env:
  SSGUARD_TEST_SRCDIR   dir containing fake_rtltcp.py
  SSGUARD_TEST_PORT     port for the fake server (default 1234)
  SSGUARD_TEST_RECFILE  where the fake server records client bytes
"""
import os
import signal
import subprocess
import sys
import time


def kill_server(sd):
    pidfile = os.path.join(sd, "server.pid")
    try:
        pid = int(open(pidfile).read().strip())
    except (FileNotFoundError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        os.remove(pidfile)
    except FileNotFoundError:
        pass


def start_server(sd, mode):
    srcdir = os.environ["SSGUARD_TEST_SRCDIR"]
    port = os.environ.get("SSGUARD_TEST_PORT", "1234")
    recfile = os.environ["SSGUARD_TEST_RECFILE"]
    readyfile = os.path.join(sd, "server.ready")
    try:
        os.remove(readyfile)
    except FileNotFoundError:
        pass
    p = subprocess.Popen(
        [sys.executable, os.path.join(srcdir, "fake_rtltcp.py"),
         mode, port, recfile, readyfile],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    with open(os.path.join(sd, "server.pid"), "w") as f:
        f.write(str(p.pid))
    for _ in range(100):
        if os.path.exists(readyfile):
            return
        time.sleep(0.05)
    raise RuntimeError("fake rtl_tcp server did not become ready")


def main():
    sd = os.environ["SSGUARD_TEST_FAKESTATE"]
    args = sys.argv[1:]
    with open(os.path.join(sd, "systemctl.calls"), "a") as f:
        f.write(" ".join(args) + "\n")
    if args[:2] == ["--user", "is-active"]:
        try:
            with open(os.path.join(sd, "svcstate")) as f:
                print(f.read().strip())
        except FileNotFoundError:
            print("active")
        return 0
    if args[:2] == ["--user", "restart"]:
        kill_server(sd)
        try:
            with open(os.path.join(sd, "servermode")) as f:
                mode = f.read().strip()
        except FileNotFoundError:
            mode = "none"
        if mode and mode != "none":
            start_server(sd, mode)
        with open(os.path.join(sd, "svcstate"), "w") as f:
            f.write("active")
        return 0
    return 0


main()
