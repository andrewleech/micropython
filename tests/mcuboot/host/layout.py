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

"""Layout loader for the host sweep and fi_sweep.py.

Reads mcuboot_layout.json as written by tools/mcuboot_gen.py and derives the
trailer geometry of a slot from the bootutil formulas in bootutil_misc.h and
bootutil_area.c, for the slot policies swap (swap modes offset, move and scratch),
overwrite-external and single.

Command line:
  layout.py --make FILE.json   make variables for the Makefile
  layout.py --show FILE.json   derived geometry
"""

import argparse
import json
import sys

MAGIC_SZ = 16


def align_down(v, a):
    return v - (v % a)


def align_up(v, a):
    return align_down(v + a - 1, a)


class Layout:
    def __init__(self, data, path=None):
        self.path = path
        self.data = data
        missing = [
            f for f in ("devices", "areas", "max_align", "max_img_sectors") if f not in data
        ]
        if missing:
            raise ValueError("layout %s lacks %s" % (path, ", ".join(missing)))
        self.devices = data["devices"]
        self.areas = data["areas"]
        self.max_align = data["max_align"]
        self.max_img_sectors = data["max_img_sectors"]
        self.header_size = data.get("header_size", 0x400)
        self.policy = data.get("policy", "swap")
        self.swap_mode = data.get("swap_mode") or ("offset" if self.policy == "swap" else None)
        self.board = data.get("board", "")
        imgtool = data.get("imgtool") or {}
        self.sign_args = [str(t) for t in imgtool.get("sign_args", [])]
        if self.policy not in ("swap", "overwrite-external", "single"):
            raise ValueError("unknown policy %r" % self.policy)
        if self.policy == "swap" and self.swap_mode not in ("offset", "move", "scratch"):
            raise ValueError("unknown swap mode %r" % self.swap_mode)
        if "primary" not in self.areas:
            raise ValueError("layout has no primary area")
        pri = self.areas["primary"]
        self.dev = self.devices[pri["dev"]]
        if self.policy == "single":
            if "secondary" in self.areas:
                raise ValueError("policy single has no secondary area")
            return
        if "secondary" not in self.areas:
            raise ValueError("layout has no secondary area")
        sec = self.areas["secondary"]
        sec_dev = self.devices[sec["dev"]]
        # bootutil swaps sector by sector and addresses both trailers with the same alignment.
        if sec["erase"] != pri["erase"] or sec_dev["write"] != self.dev["write"]:
            raise ValueError("primary and secondary slots differ in erase or write unit")
        want = {
            "offset": pri["size"] + pri["erase"],
            "move": pri["size"] - pri["erase"],
        }.get(self.swap_mode if self.policy == "swap" else None, pri["size"])
        if sec["size"] != want:
            raise ValueError("secondary slot is %#x bytes, expected %#x" % (sec["size"], want))

    @property
    def update_area(self):
        """Name of the area an update is written to."""
        return "primary" if self.policy == "single" else "secondary"

    @property
    def update_spare(self):
        """Bytes in front of the update image in its slot (swap using offset)."""
        return self.erase if self.policy == "swap" and self.swap_mode == "offset" else 0

    @property
    def new_slot_size(self):
        """Size an update image is padded to: its slot without the spare unit in front of it."""
        return self.areas[self.update_area]["size"] - self.update_spare

    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls(json.load(f), path)

    # Sizes and offsets are device relative unless stated.
    @property
    def erase(self):
        """Erase unit of the slots (the unit of the run that holds the primary slot)."""
        return self.areas["primary"]["erase"]

    @property
    def write(self):
        return self.dev["write"]

    @property
    def ecc(self):
        return bool(self.dev.get("ecc", False))

    def area_geometry(self, size):
        """Trailer fields of a slot of `size` bytes, offsets relative to the slot start.

        Swap using offset keeps two status states per sector and a field for the size of the
        unprotected TLVs; move and scratch keep three states. Overwrite-external has no status
        and no swap size field, single has no status."""
        a = self.max_align
        w = self.write
        offset = self.policy == "swap" and self.swap_mode == "offset"
        states = 0
        if self.policy == "swap":
            states = 2 if offset else 3
        status_sz = self.max_img_sectors * states * w
        fields = 3 if self.policy == "overwrite-external" else 4
        info_sz = a * fields + (a if offset else 0) + align_up(MAGIC_SZ, a)
        magic = size - MAGIC_SZ
        image_ok = align_down(magic - a, a)
        copy_done = image_ok - a
        swap_info = copy_done - a
        unprot = swap_info - a if offset else None
        swap_size = (
            None if self.policy == "overwrite-external" else (unprot if offset else swap_info) - a
        )
        status_start = size - status_sz - info_sz
        return {
            "status_start": status_start,
            "status_end": size - info_sz,
            "trailer_start": status_start,
            "trailer_sector": align_down(status_start, self.erase),
            "swap_size": swap_size,
            "unprot_tlv": unprot,
            "swap_info": swap_info,
            "copy_done": copy_done,
            "image_ok": image_ok,
            "magic": magic,
            "size": size,
        }

    def area_at(self, dev, off, length):
        """Name of the area containing [off, off+length) on device dev, or None."""
        for name, a in self.areas.items():
            if a["dev"] == dev and a["off"] <= off and off + length <= a["off"] + a["size"]:
                return name
        return None

    def host_args(self):
        """Arguments of host_sweep describing the devices and slots.

        A device that is not memory mapped is a SPI flash: host_sweep gives it the memory of a
        NOR chip behind drivers/memory/spiflash.c. The erase units of a device are the runs as
        size/erase pairs joined by +."""
        args = []
        for d in self.devices:
            # A memory mapped device is placed at its CPU address, for bootutil's hash-in-place reads.
            base = d["base"] if d.get("mapped") and d.get("base", 0) >= 0x10000 else 0
            args += [
                "--dev",
                "%d:%s:%d:%d:%d:%d:%d"
                % (
                    d["size"],
                    "+".join("%d/%d" % (r["size"], r["erase"]) for r in d["runs"]),
                    d["write"],
                    1 if d.get("ecc") else 0,
                    d.get("erased_val", 255),
                    base,
                    0 if d.get("mapped", 1) else 1,
                ),
            ]
        args += ["--policy", self.policy, "--mode", self.swap_mode or "none"]
        for name in ("primary", "secondary", "scratch"):
            if name in self.areas:
                a = self.areas[name]
                args += ["--area", "%s=%d:%d:%d" % (name, a["dev"], a["off"], a["size"])]
        return args


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--make", metavar="FILE")
    ap.add_argument("--show", metavar="FILE")
    args = ap.parse_args()
    if args.make:
        lay = Layout.load(args.make)
        print("MAX_ALIGN := %d" % lay.max_align)
        print("MAX_IMG_SECTORS := %d" % lay.max_img_sectors)
    elif args.show:
        lay = Layout.load(args.show)
        for name in ("primary", "secondary"):
            print(name, lay.areas[name])
            for k, v in lay.area_geometry(lay.areas[name]["size"]).items():
                print("  %-14s 0x%x" % (k, v))
    else:
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
