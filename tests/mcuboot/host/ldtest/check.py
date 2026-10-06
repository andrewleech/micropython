#!/usr/bin/env python3
# Checks the toy images of the Makefile against the layout the generator wrote: the vector table
# is at the first byte after the image header of the primary slot, the image ends inside the
# slot, and the symbols that only the layout script defines have the values of the layout.

import json
import subprocess
import sys


def symbols(elf):
    out = subprocess.check_output(["nm", elf], text=True)
    return {
        name: int(addr, 16)
        for addr, _, name in (line.split() for line in out.splitlines() if len(line.split()) == 3)
    }


def main(layout_json, *elfs):
    lay = json.load(open(layout_json))
    primary = lay["areas"]["primary"]
    app_start = primary["addr"] + lay["header_size"]
    ok = True
    for elf in elfs:
        sym = symbols(elf)
        good = True
        checks = (
            ("toy_vectors", sym["toy_vectors"], app_start),
            ("toy_image_start", sym["toy_image_start"], primary["addr"]),
            ("toy_app_size", sym["toy_app_size"], lay["app_len"]),
            (
                "toy_text_end below the image limit",
                sym["toy_text_end"] <= primary["addr"] + lay["max_image_size"],
                True,
            ),
        )
        for name, got, want in checks:
            if got != want:
                ok = good = False
                print("FAIL %s: %s is %r, expected %r" % (elf, name, got, want))
        if good:
            print(
                "ok   %s: image at 0x%08X (primary 0x%08X + header 0x%X), app length 0x%X"
                % (elf, sym["toy_vectors"], primary["addr"], lay["header_size"], lay["app_len"])
            )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
