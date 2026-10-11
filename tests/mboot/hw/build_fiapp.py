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

"""Build and sign the test application of the hardware power-cut sweeps (fiapp/fiapp.c).

  build_fiapp.py --layout mboot_layout.json --version 1.0.0 --counter 0 --sectors 3 --role primary out.hex
  build_fiapp.py --layout mboot_layout.json --version 2.0.0 --counter 1 --sectors 3 --role secondary out.hex

The application prints its version on the log UART and idles. --sectors sets the size of the
signed image in erase units (the pad array is sized so that header, code and TLVs fill the last
unit up to a few hundred bytes). The output is an Intel HEX file that fi_sweep.py programs:
role primary is the confirmed first image of the primary slot (as `mboot_sign.py initial`),
role secondary is an image in test state at the update base of the secondary slot (trailer magic
at the end of the slot, nothing else).
"""

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOP = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(TOP, "tools"))

import mboot_sign as ms  # noqa: E402


def build(app_addr, version, pad_bytes, tmp):
    major, minor, rev = (int(x) for x in version.split("+")[0].split("."))
    pad = os.path.join(tmp, "pad.bin")
    rng = random.Random(version + str(pad_bytes))
    with open(pad, "wb") as f:
        f.write(bytes(rng.randrange(256) for _ in range(pad_bytes)))
    elf = os.path.join(tmp, "fiapp.elf")
    binary = os.path.join(tmp, "fiapp.bin")
    cmd = [
        "arm-none-eabi-gcc",
        "-mcpu=cortex-m33",
        "-mthumb",
        "-Os",
        "-ffreestanding",
        "-nostdlib",
        "-Wall",
        "-Werror",
        "-std=gnu99",
        "-DAPP_MAJOR=%d" % major,
        "-DAPP_MINOR=%d" % minor,
        "-DAPP_REV=%d" % rev,
        "-DPAD_BYTES=%d" % pad_bytes,
        '-DPAD_FILE="%s"' % pad,
        "-Wl,--defsym=APP_ADDR=0x%x" % app_addr,
        "-Wl,-T," + os.path.join(HERE, "fiapp", "fiapp.ld"),
        "-o",
        elf,
        os.path.join(HERE, "fiapp", "fiapp.c"),
    ]
    subprocess.run(cmd, check=True)
    subprocess.run(["arm-none-eabi-objcopy", "-O", "binary", elf, binary], check=True)
    return binary


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--layout", required=True)
    ap.add_argument("--version", required=True, help="M.m.r")
    ap.add_argument("--counter", type=int, default=0)
    ap.add_argument("--sectors", type=int, required=True)
    ap.add_argument("--role", choices=("primary", "secondary"), required=True)
    ap.add_argument(
        "-k", "--key", default=os.path.join(TOP, "tests", "mboot", "keys", "test-ecdsa-p256.pem")
    )
    ap.add_argument("output")
    args = ap.parse_args()

    layout = ms.load_layout(args.layout)
    with open(args.layout) as f:
        doc = json.load(f)
    erase = doc["areas"]["primary"]["erase"]
    app_addr = doc["app_start"]
    pad_bytes = args.sectors * erase - layout.header_size - 0x400 - 0x200
    if pad_bytes <= 0:
        raise SystemExit("--sectors too small")
    with tempfile.TemporaryDirectory() as tmp:
        binary = build(app_addr, args.version, pad_bytes, tmp)
        if args.role == "primary":
            base_args = layout.initial_args
            addr = layout.primary_addr
        else:
            base_args = layout.sign_args + ["--pad"]
            addr = layout.secondary_addr + erase
        imgargs = ms.resolve_args(layout, base_args, args.version, args.counter)
        imgargs += ["-k", args.key, "--hex-addr", hex(addr)]
        ms.imgtool_sign(imgargs + [binary, args.output])
    parsed = ms.image_info(ms.read_image_file(args.output))
    print(
        "%s: %d bytes of image (%d sectors of %d), version %d.%d.%d, counter %s, role %s"
        % (
            args.output,
            parsed.total,
            -(-parsed.total // erase),
            erase,
            *parsed.version[:3],
            parsed.counter,
            args.role,
        )
    )


if __name__ == "__main__":
    main()
