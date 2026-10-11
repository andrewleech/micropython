#!/usr/bin/env python3
#
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

"""Wrap images into FAT volumes for the fsload cases of bl_test.c.

    mkfat.py [--size BYTES] OUTDIR IMAGE...

Writes OUTDIR/<name>.fat for every IMAGE <name>.bin: a FAT12 volume of BYTES (default 1 MiB, the
size of the SPI flash of most boards) that holds the image as /update.bin, and
OUTDIR/<name>.gz.fat with the image gzip compressed in /update.bin for the gzip reader.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "fuzz"))

import containers  # noqa: E402

VOLUME_SIZE = 1 << 20


def main():
    args = sys.argv[1:]
    size = VOLUME_SIZE
    if args[:1] == ["--size"] and len(args) > 1:
        size = int(args[1], 0)
        args = args[2:]
    if len(args) < 2:
        sys.exit(__doc__)
    out = pathlib.Path(args[0])
    out.mkdir(parents=True, exist_ok=True)
    for image in map(pathlib.Path, args[1:]):
        volume = containers.make_fat({"/update.bin": image.read_bytes()}, size=size, fat=12)
        (out / (image.stem + ".fat")).write_bytes(volume)
        volume = containers.make_fat(
            {"/update.bin": containers.make_gzip(image.read_bytes())}, size=size, fat=12
        )
        (out / (image.stem + ".gz.fat")).write_bytes(volume)


if __name__ == "__main__":
    main()
