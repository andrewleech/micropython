#!/bin/bash
set -e

# Host-side checks for the pure pieces `mpremote debug` is built from: the
# MPDBG-READY handshake parser and the REPL-stream framing. No device needed.

# The package this exercises, found relative to this script rather than to the
# working directory the harness happens to run it from.
MPREMOTE_PKG_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export MPREMOTE_PKG_DIR

python3 - << 'EOF'
import os
import sys

sys.path.insert(0, os.environ["MPREMOTE_PKG_DIR"])

from mpremote import repl_dap
from mpremote.mpdebug_handshake import HandshakeError, read_handshake


def check(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"
    print(f"ok {name}")


# -- the handshake parser ------------------------------------------------

def once(line):
    """A read_chunk() that yields `line` and then nothing, as a real one does."""
    sent = []

    def read_chunk():
        if sent:
            return ""
        sent.append(True)
        return line

    return read_chunk


ready = read_handshake(
    once('MPDBG-READY {"host": "192.0.2.10", "port": 5678, "caps": {}}\n'), 5, "serial"
)
check("read_handshake takes a real address", ready["host"], "192.0.2.10")
check("read_handshake keeps the reported port", ready["port"], 5678)
check("read_handshake reports the channel kind", ready["kind"], "tcp")
check(
    "read_handshake resolves a wildcard for unix",
    read_handshake(once('MPDBG-READY {"host": "0.0.0.0", "port": 5678, "caps": {}}\n'), 5, "unix")[
        "host"
    ],
    "127.0.0.1",
)
check(
    "read_handshake tolerates output around the line",
    read_handshake(
        once('noise\nMPDBG-READY {"host": "192.0.2.10", "port": 1, "caps": {}}\r\nmore\n'),
        5,
        "serial",
    )["port"],
    1,
)
try:
    read_handshake(once('MPDBG-READY {"host": "::", "port": 5678, "caps": {}}\n'), 5, "serial")
except HandshakeError:
    print("ok read_handshake refuses to guess an address")
else:
    raise AssertionError("a wildcard with no known host should not resolve")


# -- the REPL-stream framing ---------------------------------------------

d = repl_dap.Demux()
d.feed(repl_dap.escape(b"plain\n"))
check("demux passes plain bytes", d.take_plain(99), b"plain\n")

d = repl_dap.Demux()
d.feed(repl_dap.escape(b"a\x18b"))
check("demux unescapes a literal marker", d.take_plain(99), b"a\x18b")

d = repl_dap.Demux()
payload = bytes(range(256)) * 4
d.feed(repl_dap.frame(repl_dap.CMD_DAP, payload))
check("demux reassembles a frame", d.take_dap(len(payload)), payload)

# One byte at a time is the case the incremental state machine exists for.
d = repl_dap.Demux()
wire = repl_dap.escape(b"out") + repl_dap.frame(repl_dap.CMD_DAP, b"hello")
for i in range(len(wire)):
    d.feed(wire[i : i + 1])
check("demux resumes across reads (plain)", d.take_plain(99), b"out")
check("demux resumes across reads (framed)", d.take_dap(99), b"hello")

d = repl_dap.Demux()
d.feed(repl_dap.frame(99, b""))
check("demux records an unknown code", d.unknown_code, 99)
EOF
