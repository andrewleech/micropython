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

"""
Write the negative and positive MCUboot test images derived from one signed image.

  make_variants.py [--layout mcuboot_layout.json] <signed.bin> <outdir>

The input is a signed update image (tools/mcuboot_sign.py sign) with a security counter above 0, a
version above 0.0.0 and a layout id TLV. <outdir> receives one <name>.bin per entry of
image_check.VARIANT_SPECS (good, bad_sig, bad_hash, wrong_key, truncated_half, truncated_tlv,
oversize, old_version, old_counter, wrong_layout, no_layout, bad_header_magic) and manifest.json,
which lists for every file its size, SHA-256, the result codes a correct validator may return and
the result of the host reference check (image_check.classify) of the file.

The images that need a valid signature (oversize, old_version, old_counter, wrong_layout,
no_layout) are signed again from the body of the input with --key, wrong_key with --other-key.
The exit status is 1 if a variant is not produced or the reference check disagrees with the
expected result codes.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

TOP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOP / "tools"))

import image_check as ic  # noqa: E402
import mcuboot_sign as ms  # noqa: E402


def describe_diff(good, variant):
    """Where a variant differs from the good image, by region of the good image."""
    if variant == good:
        return "identical"
    regions = ic.image_regions(ic.parse_image(good))
    counts = {}
    first = None
    for i in range(min(len(good), len(variant))):
        if good[i] != variant[i]:
            first = i if first is None else first
            name = "after TLV area"
            for start, end, rname in regions:
                if start <= i < end:
                    name = rname
                    break
            counts[name] = counts.get(name, 0) + 1
    parts = []
    if len(variant) != len(good):
        parts.append("length %d (good %d)" % (len(variant), len(good)))
    if counts:
        total = sum(counts.values())
        where = ", ".join("%s %d" % item for item in sorted(counts.items()))
        parts.append("%d bytes differ from 0x%x: %s" % (total, first, where))
    return "; ".join(parts)


def make_all(good, key, other_key, pub, layout_id, header_size, max_image_size, oversize_len):
    """Return the manifest entries and file contents of all variants."""
    img = ic.parse_image(good)
    base = (img.version, img.counter() or 0)
    entries = []
    for name, description, expect, rollback in ic.VARIANT_SPECS:
        data = ic.make_variant(name, good, key, other_key, oversize_len)
        if name != "good" and data == good:
            raise ms.SignError("variant %s is identical to the good image" % name)
        results = {}
        for policy in ("version", "counter"):
            code, msg = ic.classify(
                data, pub, layout_id, header_size, max_image_size, base=base, rollback=policy
            )
            results[policy] = (code, msg)
        observed = results["counter" if rollback == "counter" else "version"]
        entries.append(
            {
                "name": name,
                "file": name + ".bin",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "description": description,
                "expect": expect,
                "rollback": rollback,
                "observed": observed[0],
                "observed_detail": observed[1],
                "observed_by_rollback_policy": {k: v[0] for k, v in results.items()},
                "differs": describe_diff(good, data),
                "_data": data,
            }
        )
    return entries


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--layout", help="mcuboot_layout.json (header size, layout id, size limits)")
    ap.add_argument(
        "--max-image-size",
        type=lambda v: int(v, 0),
        help="MCUBOOT_MAX_IMAGE_SIZE, when there is no --layout",
    )
    ap.add_argument("--key", default=str(ms.DEFAULT_KEY), help="key that signed the input")
    ap.add_argument("--other-key", default=str(ic.DEFAULT_OTHER_KEY), help="key for wrong_key")
    ap.add_argument("signed", help="signed update image")
    ap.add_argument("outdir")
    args = ap.parse_args(argv)

    try:
        good = Path(args.signed).read_bytes()
        img = ic.parse_image(good)
        pub = ms.load_public_key(args.key)
        if args.layout:
            layout = ms.load_layout(args.layout)
            layout_id, header_size = layout.layout_id, layout.header_size
            max_image_size = layout.max_image_size
            oversize_len = ic.oversize_length(layout.primary_size, layout.secondary_size)
        else:
            if args.max_image_size is None:
                raise ms.SignError("give --layout or --max-image-size")
            value = img.tlv_value(ms.TLV_LAYOUT_ID, True)
            layout_id = int.from_bytes(value, "big") if value else None
            header_size, max_image_size = img.hdr_size, args.max_image_size
            oversize_len = ic.oversize_length(max_image_size, None)
        code, msg = ic.classify(good, pub, layout_id, header_size, max_image_size)
        if code != ic.RES_OK:
            raise ms.SignError("%s is not valid for the key: %s" % (args.signed, msg))
        entries = make_all(
            good,
            args.key,
            args.other_key,
            pub,
            layout_id,
            header_size,
            max_image_size,
            oversize_len,
        )
    except ms.SignError as exc:
        sys.stderr.write("make_variants: error: %s\n" % exc)
        return 1

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    failed = []
    for e in entries:
        (outdir / e["file"]).write_bytes(e.pop("_data"))
        ok = e["observed"] in e["expect"]
        if not ok:
            failed.append(e["name"])
        print(
            "%-18s %8d  %-14s %s  %s"
            % (e["file"], e["size"], e["observed"], "ok " if ok else "BAD", e["differs"])
        )
    manifest = {
        "input": Path(args.signed).name,
        "layout_id": "%08x" % layout_id if layout_id is not None else None,
        "max_image_size": max_image_size,
        "variants": entries,
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    if failed:
        sys.stderr.write("make_variants: unexpected result for: %s\n" % ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
