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

"""Label the flash operations of an update with a phase class.

The mapping depends only on the area an operation touches and the offset inside
the slot (trailer start, status table, trailer fields, erase unit) computed from
the layout, not on bootutil internals, so it applies to the traces of the host
harness and to flash operation lists recorded on hardware alike.

Classes:
  P_ERASE_SEC      erase of a secondary slot sector (not a trailer sector)
  P_WRITE_PRI      write of image data into the primary slot
  P_ERASE_PRI      erase of a primary slot sector (not a trailer sector)
  P_WRITE_SEC      write of image data into the secondary slot
  P_STATUS         write in the swap status table of either slot
  P_SWAPSIZE       swap_size trailer field
  P_UNPROT_TLV     unprotected TLV size trailer field
  P_SWAPINFO       swap_info trailer field
  P_COPYDONE       copy_done flag
  P_IMAGEOK        image_ok flag
  P_MAGIC          trailer magic
  P_TRAILER_ERASE  erase of a sector that holds a slot trailer
  P_LOG            update audit log or counter area
  P_SCRATCH_ERASE  erase in the scratch area of swap using scratch
  P_SCRATCH_WRITE  write in the scratch area (data and the status kept there)
  P_INTENT         intent area of the single slot policy with fsload
  P_SHADOW_WRITE   write in the shadow area of the trailer sectors (ECC devices)
  P_SHADOW_ERASE   erase in the shadow area
  P_OTHER          anything else (reported as an error by the report)

swap_info is a separate trailer field with its own failure signature, and the
shadow area exists only on ECC devices; shadow writes and erases are cut points
like any other flash operation.

Command line: phases.py LAYOUT.json TRACE.jsonl  prints the label of every
operation of the first trace record.
"""

import json
import sys

import layout as layout_mod

CLASSES = (
    "P_TRAILER_ERASE",
    "P_SWAPSIZE",
    "P_UNPROT_TLV",
    "P_SWAPINFO",
    "P_STATUS",
    "P_COPYDONE",
    "P_IMAGEOK",
    "P_MAGIC",
    "P_ERASE_SEC",
    "P_WRITE_PRI",
    "P_ERASE_PRI",
    "P_WRITE_SEC",
    "P_LOG",
    "P_SCRATCH_ERASE",
    "P_SCRATCH_WRITE",
    "P_INTENT",
    "P_SHADOW_WRITE",
    "P_SHADOW_ERASE",
    "P_OTHER",
)

_FIELD_CLASS = (
    ("swap_size", "P_SWAPSIZE"),
    ("unprot_tlv", "P_UNPROT_TLV"),
    ("swap_info", "P_SWAPINFO"),
    ("copy_done", "P_COPYDONE"),
    ("image_ok", "P_IMAGEOK"),
    ("magic", "P_MAGIC"),
)


def classify(lay, op):
    """Class of one operation; op has kind ('write'|'erase'), dev, off, len (device relative)."""
    kind, dev, off, length = op["kind"], op["dev"], op["off"], op["len"]
    name = lay.area_at(dev, off, length)
    if name is None:
        return "P_OTHER"
    if name in ("log", "seccnt"):
        return "P_LOG"
    if name == "scratch":
        return "P_SCRATCH_ERASE" if kind == "erase" else "P_SCRATCH_WRITE"
    if name == "intent":
        return "P_INTENT"
    if name == "shadow":
        return "P_SHADOW_ERASE" if kind == "erase" else "P_SHADOW_WRITE"
    if name not in ("primary", "secondary"):
        return "P_OTHER"
    area = lay.areas[name]
    rel = off - area["off"]
    geo = lay.area_geometry(area["size"])
    if kind == "erase":
        if rel + length > geo["trailer_sector"]:
            return "P_TRAILER_ERASE"
        return "P_ERASE_PRI" if name == "primary" else "P_ERASE_SEC"
    # write
    if rel >= geo["status_start"] and rel + length <= geo["status_end"]:
        return "P_STATUS"
    if rel >= geo["status_end"]:
        for field, cls in _FIELD_CLASS:
            if geo[field] is not None and rel <= geo[field] < rel + length:
                return cls
        return "P_OTHER"
    if rel + length > geo["status_start"]:
        return "P_OTHER"
    return "P_WRITE_PRI" if name == "primary" else "P_WRITE_SEC"


def label_trace(lay, ops):
    return [classify(lay, op) for op in ops]


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    lay = layout_mod.Layout.load(sys.argv[1])
    with open(sys.argv[2]) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("type") == "trace":
                for op, cls in zip(rec["ops"], label_trace(lay, rec["ops"])):
                    print(
                        "%4d %-5s dev%d off=0x%06x len=0x%05x %s"
                        % (op["seq"], op["kind"], op["dev"], op["off"], op["len"], cls)
                    )
                return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
