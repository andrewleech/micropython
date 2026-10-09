#!/usr/bin/env python3
# This file is part of the MicroPython project, http://micropython.org/
#
# The MIT License (MIT)
#
# Copyright (c) 2026 Andrew Leech
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

"""Flash operation trace lines of a MBOOT_TEST_FI bootloader.

One line per flash write or erase, printed through the log sink before the
operation (and before the fault-injection hook):

    FI <n> <w|e> <dev> 0x<off> 0x<len>

n is the 1-based operation number (the fault-injection counter value after the
increment), dev the device id and off/len are device relative. The lines may
carry a log prefix such as "[INF] ". The trace of one boot is the run of lines
before the next bootloader banner.

Usage: fi_log.py LOG   print the parsed operations of the first boot in LOG
"""

import re
import sys

BANNER = re.compile(r"MPY MCUboot")
FI_LINE = re.compile(r"\bFI (\d+) ([we]) (\d+) (0x[0-9a-fA-F]+) (0x[0-9a-fA-F]+)\s*$")


def format_op(op):
    return "FI %d %s %d 0x%x 0x%x" % (
        op["seq"],
        "e" if op["kind"] == "erase" else "w",
        op["dev"],
        op["off"],
        op["len"],
    )


def trace_to_log(ops, banner=True):
    lines = ["[INF] MPY MCUboot (trace)"] if banner else []
    lines += ["[INF] " + format_op(op) for op in ops]
    return "\n".join(lines) + "\n"


def parse_trace(text):
    """Operations of the first boot in text, as dicts with seq, kind, dev, off, len.

    Raises ValueError if the numbering is not 1..M without gaps.
    """
    segments = BANNER.split(text)
    ops = []
    for seg in segments:
        found = []
        for line in seg.replace("\r", "").split("\n"):
            m = FI_LINE.search(line)
            if m:
                found.append(
                    {
                        "seq": int(m.group(1)),
                        "kind": "erase" if m.group(2) == "e" else "write",
                        "dev": int(m.group(3)),
                        "off": int(m.group(4), 16),
                        "len": int(m.group(5), 16),
                    }
                )
        if found:
            ops = found
            break
    if not ops:
        raise ValueError("no FI trace lines in the log")
    for i, op in enumerate(ops):
        if op["seq"] != i + 1:
            raise ValueError(
                "trace numbering breaks at line %d: expected %d, got %d"
                % (i + 1, i + 1, op["seq"])
            )
    return ops


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    with open(sys.argv[1], errors="replace") as f:
        ops = parse_trace(f.read())
    for op in ops:
        print(format_op(op))
    return 0


if __name__ == "__main__":
    sys.exit(main())
