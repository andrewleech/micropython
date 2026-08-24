#!/bin/bash
set -e

# Host-side checks for the transport timeouts a mounted or pty-backed session
# depends on. No device is touched: a pty pair stands in for one.

# The package this exercises, found relative to this script rather than to the
# working directory the harness happens to run it from.
MPREMOTE_PKG_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export MPREMOTE_PKG_DIR

python3 - << 'EOF'
import os
import pty
import sys
import threading
import time

sys.path.insert(0, os.environ["MPREMOTE_PKG_DIR"])

from mpremote.transport_serial import SerialTransport


def check(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"
    print(f"ok {name}")


# -- read_until's two timeout_overall modes ------------------------------

master, slave = pty.openpty()
t = SerialTransport(os.ttyname(slave))
try:
    # A peer that says nothing. This is the case timeout_overall bounds at its
    # default, and the regression this pins: with the check missing from the
    # quiet branch, only the (much longer) char timeout ends the call.
    began = time.monotonic()
    t.read_until(1, b"\x04", timeout=30, timeout_overall=0.5)
    quiet = time.monotonic() - began
    check("read_until quiet honours timeout_overall", 0.4 < quiet < 5, True)

    # A peer that keeps producing bytes past the deadline. Non-strict is
    # deliberately not cut off - that is what keeps a verbose board's boot
    # printing intact - so only the strict form is bounded here.
    stop = threading.Event()

    def chatter():
        while not stop.is_set():
            try:
                os.write(master, b"x")
            except OSError:
                return
            time.sleep(0.005)

    th = threading.Thread(target=chatter, daemon=True)
    th.start()
    try:
        began = time.monotonic()
        t.read_until(1, b"\x04", timeout=30, timeout_overall=0.5, timeout_overall_strict=True)
        strict = time.monotonic() - began
    finally:
        stop.set()
        th.join(timeout=2)
    check("read_until strict is bounded while data still arrives", 0.4 < strict < 5, True)
finally:
    t.close()
    os.close(master)


# -- SerialIntercept carries the port's read timeout ---------------------


class _FakePort:
    timeout = None
    fd = -1

    def inWaiting(self):
        return 0

    def read(self, n):
        return b""

    def write(self, data):
        return len(data)


from mpremote.transport_serial import SerialIntercept  # noqa: E402

port = _FakePort()
intercept = SerialIntercept(port, None)
check("SerialIntercept applies the RPC floor", port.timeout, 5.0)
intercept.timeout = 0.1
check("SerialIntercept.timeout reaches the port", port.timeout, 0.1)
check("SerialIntercept.timeout reads back", intercept.timeout, 0.1)
EOF
