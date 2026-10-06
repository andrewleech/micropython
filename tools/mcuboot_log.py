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

"""Decode an MCUboot update log dump and the DFU result record.

The update log is an append-only array of 32-byte records in the log area, which is two erase
units used ping-pong. A dump is the raw content of that area, for example the "Update log" DFU
alternate read with dfu-util, or a flash read over SWD. Records are listed in sequence order, so
the newest are last wherever they sit in the area.

Usage:
  mcuboot_log.py DUMP.bin [--unit BYTES] [--json]
  mcuboot_log.py --result 0102030405060708090a0b0c0d0e0f10

--unit gives the erase unit size, so each row shows the unit it was found in. --result decodes
the 16 bytes returned by the DFU vendor request 0x81.

A slot whose first word is erased is free. A slot whose first word is not erased but whose magic
or CRC is wrong is reported as "consumed". The append code skips such slots, so a record torn by
a power cut never reads as valid.
"""

import argparse
import functools
import json
import struct
import sys
import zlib

REC_MAGIC = 0x474C424D  # bytes "MBLG"
REC_SIZE = 32
REC_FMT = "<IIBBBBBBHIIII"
DFU_RESULT_FMT = "<IHBBII"

TYPES = {
    1: "BOOT_OK",
    2: "NO_IMAGE",
    3: "DFU_BEGIN",
    4: "IMAGE_ACCEPTED",
    5: "IMAGE_REJECTED",
    6: "SWAP_DONE",
    7: "SWAP_DONE_PERM",
    8: "REVERTED",
    9: "SLOT_REJECTED_AT_BOOT",
    10: "FSLOAD_BEGIN",
    11: "FSLOAD_DONE",
    12: "FSLOAD_FAILED",
    13: "APP_CONFIRMED",
    14: "APP_UPGRADE_REQUESTED",
    15: "APP_DFU_REQUESTED",
    16: "ASSERT",
    17: "FSLOAD_RETRY",
    18: "SECCNT_FAILED",
}

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


def decode_slot(raw):
    """Decode one 32-byte slot. Returns a dict with state free, valid or consumed."""
    if raw[:4] == b"\xff\xff\xff\xff":
        return {"state": "free"}
    fields = struct.unpack(REC_FMT, raw)
    (
        magic,
        seq,
        rtype,
        result,
        source,
        flags,
        vmaj,
        vmin,
        vrev,
        vbuild,
        detail,
        hash_prefix,
        crc,
    ) = fields
    if magic != REC_MAGIC:
        return {"state": "consumed", "reason": "bad magic 0x%08x" % magic}
    calc = zlib.crc32(raw[:28]) & 0xFFFFFFFF
    if calc != crc:
        return {"state": "consumed", "reason": "bad crc 0x%08x (expected 0x%08x)" % (crc, calc)}
    return {
        "state": "valid",
        "seq": seq,
        "type": rtype,
        "type_name": name_of(TYPES, rtype),
        "result": result,
        "result_name": name_of(RESULTS, result),
        "source": source,
        "source_name": name_of(SOURCES, source),
        "primary_valid": bool(flags & 1),
        "secondary_header": bool(flags & 2),
        "flags": flags,
        "version": "%d.%d.%d+%d" % (vmaj, vmin, vrev, vbuild),
        "detail": detail,
        "hash_prefix": hash_prefix,
    }


def seq_cmp(a, b):
    """Order of two sequence numbers with 32-bit wrap: (int32_t)(a - b)."""
    d = (a - b) & 0xFFFFFFFF
    if d >= 0x80000000:
        d -= 0x100000000
    return d


def decode_dump(data, unit=0):
    """Returns (records in sequence order, consumed slots, number of free slots, notes)."""
    if len(data) % REC_SIZE:
        raise ValueError("dump size %d is not a multiple of %d" % (len(data), REC_SIZE))
    records = []
    consumed = []
    free = 0
    for off in range(0, len(data), REC_SIZE):
        rec = decode_slot(data[off : off + REC_SIZE])
        rec["offset"] = off
        if unit:
            rec["unit"] = off // unit
        if rec["state"] == "free":
            free += 1
        elif rec["state"] == "consumed":
            consumed.append(rec)
        else:
            records.append(rec)
    # Sort by wrap-aware sequence number.
    records.sort(key=functools.cmp_to_key(lambda a, b: seq_cmp(a["seq"], b["seq"])))
    notes = []
    for prev, cur in zip(records, records[1:]):
        if cur["seq"] == prev["seq"]:
            notes.append(
                "duplicate seq %d at offsets 0x%x and 0x%x"
                % (cur["seq"], prev["offset"], cur["offset"])
            )
        elif (cur["seq"] - prev["seq"]) & 0xFFFFFFFF != 1:
            notes.append(
                "gap between seq %d and %d (older history was erased)" % (prev["seq"], cur["seq"])
            )
    return records, consumed, free, notes


def decode_result(data):
    """The 16-byte mcuboot_dfu_result_t returned by vendor request 0x81."""
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


def format_table(records, consumed, free, notes, unit=0):
    lines = []
    head = "%10s %-6s %-23s %-14s %-6s %-5s %-14s %10s %10s" % (
        "seq",
        "unit@off" if unit else "off",
        "type",
        "result",
        "source",
        "flags",
        "version",
        "detail",
        "hash",
    )
    lines.append(head)

    def where(r):
        return "%d@%x" % (r["unit"], r["offset"] % unit) if unit else "0x%x" % r["offset"]

    for r in records:
        flags = ("P" if r["primary_valid"] else "-") + ("S" if r["secondary_header"] else "-")
        lines.append(
            "%10d %-6s %-23s %-14s %-6s %-5s %-14s 0x%08x 0x%08x"
            % (
                r["seq"],
                where(r),
                r["type_name"],
                r["result_name"],
                r["source_name"],
                flags,
                r["version"],
                r["detail"],
                r["hash_prefix"],
            )
        )
    for r in consumed:
        lines.append("%10s %-6s consumed slot: %s" % ("-", where(r), r["reason"]))
    lines.append("%d records, %d consumed, %d free slots" % (len(records), len(consumed), free))
    for n in notes:
        lines.append("note: " + n)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("dump", nargs="?", help="log area dump, - for stdin")
    ap.add_argument("--unit", type=lambda s: int(s, 0), default=0, help="erase unit size in bytes")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    ap.add_argument(
        "--result", metavar="HEX", help="decode a 16-byte DFU vendor request 0x81 result"
    )
    args = ap.parse_args()

    if args.result:
        res = decode_result(bytes.fromhex(args.result))
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
    if not args.dump:
        ap.print_help()
        return 2
    data = sys.stdin.buffer.read() if args.dump == "-" else open(args.dump, "rb").read()
    try:
        records, consumed, free, notes = decode_dump(data, args.unit)
    except ValueError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"records": records, "consumed": consumed, "free": free, "notes": notes}))
    else:
        print(format_table(records, consumed, free, notes, args.unit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
