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

"""Decode the 16-byte DFU result returned by vendor request 0x81.

Usage:
  mboot_dfu_result.py --result 0102030405060708090a0b0c0d0e0f10 [--json]
"""

import argparse
import json
import struct
import sys

DFU_RESULT_FMT = "<IHBBII"

RESULTS = {
    0: "OK",
    1: "ERR_FLASH",
    2: "ERR_HEADER",
    3: "ERR_HASH",
    4: "ERR_SIG",
    5: "ERR_DOWNGRADE",
    6: "ERR_TOO_BIG",
    7: "ERR_NOT_TARGET",
    8: "ERR_PENDING",
    9: "ERR_LAYOUT",
    10: "ERR_NO_IMAGE",
    11: "ERR_REQUEST",
    12: "ERR_TOO_SMALL",
    16: "ERR_FS_MOUNT",
    17: "ERR_FS_OPEN",
    18: "ERR_FS_READ",
    19: "ERR_FS_GZIP",
    20: "ERR_FS_PATH",
}

SOURCES = {0: "boot", 1: "dfu", 2: "fsload", 3: "app"}


def name_of(table, value):
    return table.get(value, "UNKNOWN(%d)" % value)


def decode_result(data):
    """Decode a 16-byte mboot_dfu_recovery_result_t."""
    if len(data) != 16:
        raise ValueError("a DFU result is 16 bytes, got %d" % len(data))
    seq, code, source, phase, detail, reserved = struct.unpack(DFU_RESULT_FMT, data)
    return {
        "seq": seq,
        "code": code,
        "code_name": name_of(RESULTS, code),
        "source": source,
        "source_name": name_of(SOURCES, source),
        "phase": phase,
        "detail": detail,
        "reserved": reserved,
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--result", metavar="HEX", required=True, help="16-byte DFU vendor request 0x81 result"
    )
    ap.add_argument("--json", action="store_true", help="print JSON instead of a summary")
    args = ap.parse_args()

    try:
        res = decode_result(bytes.fromhex(args.result))
    except ValueError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(res))
    else:
        print(
            "seq %d code %d (%s) source %s phase %d detail 0x%08x"
            % (
                res["seq"],
                res["code"],
                res["code_name"],
                res["source_name"],
                res["phase"],
                res["detail"],
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
