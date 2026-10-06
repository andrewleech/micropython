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

"""Measures what the tear delays of fi_sweep.py (fault injection mode 2) do to the flash word.

For every selected operation of the cuttable boot and every delay, the tool programs the slots,
arms the fault injection state (mode 2: reset a given number of microseconds after the flash
command was started), runs the bootloader and stops the core at its reset handler as soon as the
injected reset has happened, before anything has read, repaired or erased the flash. It then
reads the 16-byte units the interrupted operation covers over SWD and classifies each as erased
(all 0xFF), programmed, or ECC-invalid (FLASH_ECCDETR.ECCD set by the read). One JSON line per
trial: operation, delay, units erased/programmed/invalid. For erase operations the units of the
sector are counted the same way.

  tear_probe.py --layout mcuboot_layout.json --elf firmware.elf --probe SERIAL --target <pyocd target> \
      --tty /dev/serial/by-id/... --old-image old.hex --new-image new.hex --trace swap.jsonl \
      --ops 1,2,3 --delays-us 0:100:4 --out tear.jsonl

--trace is a JSON-lines file written by fi_sweep.py (--out); only its trace record is used.
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "host"))

import fi_sweep  # noqa: E402
import layout as layout_mod  # noqa: E402

FLASH_ECCCORR = 0x40022100
FLASH_ECCDETR = 0x40022104
ECCC = 1 << 30
ECCD = 1 << 31
UNIT = 16


def parse_range(text):
    if ":" in text:
        lo, hi, step = (int(x) for x in text.split(":"))
        return list(range(lo, hi, step))
    return [int(x) for x in text.split(",") if x]


def classify_units(tgt, addr, length):
    """Counts of erased / programmed / ECC-invalid 16-byte units in [addr, addr + length).

    A unit that reads without a double error (ECCD) but with a correction (ECCC) is counted as
    "corrected" (its data is the corrected value); a unit that is neither erased nor flagged is
    "programmed". The returned dict also holds the data and flags of the first unit that is not
    erased.
    """
    t = tgt.target
    counts = {"erased": 0, "programmed": 0, "invalid": 0, "corrected": 0}
    first_bad = None
    first = None
    for a in range(addr, addr + length, UNIT):
        t.write32(FLASH_ECCDETR, ECCD)
        t.write32(FLASH_ECCCORR, ECCC)
        data = bytes(t.read_memory_block8(a, UNIT))
        detr = t.read32(FLASH_ECCDETR)
        corr = t.read32(FLASH_ECCCORR)
        if detr & ECCD:
            counts["invalid"] += 1
            kind = "invalid"
            if first_bad is None:
                first_bad = a
        elif corr & ECCC:
            counts["corrected"] += 1
            kind = "corrected"
        elif data == b"\xff" * UNIT:
            counts["erased"] += 1
            continue
        else:
            counts["programmed"] += 1
            kind = "programmed"
        if first is None:
            first = {"unit": a, "kind": kind, "data": data.hex(), "eccdetr": detr, "ecccorr": corr}
    t.write32(FLASH_ECCDETR, ECCD)
    t.write32(FLASH_ECCCORR, ECCC)
    counts["first"] = first
    return counts, first_bad


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--layout", required=True)
    ap.add_argument("--probe", required=True)
    ap.add_argument("--target", required=True, help="pyocd target name")
    ap.add_argument("--tty")
    ap.add_argument("--scenario", choices=("swap", "revert"), default="swap")
    ap.add_argument("--old-image", required=True)
    ap.add_argument("--new-image", required=True)
    ap.add_argument("--trace", required=True)
    ap.add_argument("--ops", required=True, help="operation numbers (k) as a list or lo:hi:step")
    ap.add_argument("--delays-us", default="0:100:4")
    ap.add_argument("--repeat", type=int, default=1, help="trials per operation and delay")
    fi_sweep.add_fi_arguments(ap)
    ap.add_argument("--quiet-s", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    fi_sweep.resolve_fi_address(ap, args)

    lay = layout_mod.Layout.load(args.layout)
    with open(args.trace) as f:
        ops = json.loads(f.readline())["ops"]
    dev_base = lay.devices[0]["base"]
    tgt = fi_sweep.PyocdTarget(args.probe, args.target, args.tty, args.fi_addr)
    try:
        reset_handler = tgt.read_memory(dev_base + 4, 4)
        rh = int.from_bytes(reset_handler, "little") & ~1
        with open(args.out, "a") as out:
            for k in parse_range(args.ops):
                op = ops[k - 1]
                for delay in [d for d in parse_range(args.delays_us) for _ in range(args.repeat)]:
                    tgt.program(lay, args.old_image, args.new_image)
                    if args.scenario == "revert":
                        tgt.write_fi(fi_sweep.fi_word(0))
                        tgt.run()
                        tgt.wait_idle(args.quiet_s, args.timeout)
                        tgt.drain()
                        tgt.reset_halt()
                    tgt.write_fi(fi_sweep.fi_word(k, 2, delay))
                    # Step off the reset handler first, a breakpoint at the current pc would halt the core again at once.
                    tgt.target.step()
                    tgt.target.set_breakpoint(rh)
                    tgt.run()
                    end = time.time() + args.timeout
                    while time.time() < end:
                        if (
                            tgt._retry(tgt.target.get_state) == tgt.target.State.HALTED
                            and tgt.read_fi()[2] >= k
                        ):
                            break
                        time.sleep(0.01)
                    tgt.target.remove_breakpoint(rh)
                    length = op["len"] if op["kind"] == "write" else 0x2000
                    counts, bad = classify_units(tgt, dev_base + op["off"], length)
                    rec = {
                        "k": k,
                        "kind": op["kind"],
                        "off": op["off"],
                        "len": op["len"],
                        "delay_us": delay,
                        "counter": tgt.read_fi()[2],
                        "first_invalid": bad,
                    }
                    rec.update(counts)
                    out.write(json.dumps(rec) + "\n")
                    out.flush()
                    print(json.dumps(rec), file=sys.stderr, flush=True)
    finally:
        tgt.close()


if __name__ == "__main__":
    main()
