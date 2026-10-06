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

# Reads the MCUboot configuration of a build from shared/mcuboot/include/mcuboot_layout.h (and
# the board's mpconfigboard.h and the port's mcuboot_dev.h behind it) by running the C
# preprocessor over it. Writes what the linker scripts, Makefiles and image signing tools need
# to --out:
#
#   mcuboot_layout.ld    linker symbols of the flash areas
#   mcuboot_layout.mk    make variables
#   mcuboot_layout.json  the layout, read by tools/mcuboot_sign.py and the test tools
#   mcuboot_keys_gen.c   bootutil_keys[] for the public key given with --key (bootloader)
#
# The command that preprocesses (compiler and its -I and -D options) follows "--".

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TOP = Path(__file__).resolve().parent.parent

# Area ids of shared/mcuboot/include/sysflash/sysflash.h.
AREA_IDS = {
    "boot": 0,
    "primary": 1,
    "secondary": 2,
    "scratch": 3,
    "log": 4,
    "seccnt": 5,
    "fs": 6,
    "intent": 7,
    "shadow": 8,
}
LAYOUT_TLV_TAG = 0x00A1

AREAS = ("boot", "primary", "secondary", "scratch", "log", "seccnt", "shadow", "fs", "intent")
DEVICE_FIELDS = ("NAME", "BASE", "SIZE", "WRITE", "ERASED_VAL", "MAPPED", "ECC", "RUNS")
MAX_RUNS = 4

# Macros of mcuboot_layout.h that are read. A name that is not defined (a feature that is off, or
# an area the configuration does not have) is absent from the result.
NAMES = (
    ["MCUBOOT_%s_%s" % (a.upper(), f) for a in AREAS for f in ("ADDR", "SIZE", "UNIT")]
    + [
        "MCUBOOT_" + n
        for n in (
            "HEADER_SIZE",
            "TLV_RESERVE",
            "MAX_IMAGE_SIZE",
            "MAX_IMG_SECTORS",
            "MAX_WRITE_UNIT",
            "MAX_ALIGN_VALUE",
            "TRAILER_SIZE",
            "TRAILER_SECTORS",
            "SLOT_UNIT",
            "SECONDARY_DEV",
            "SECONDARY_WRITE",
            "UPDATE_ADDR",
            "UPDATE_SIZE",
            "UPDATE_SPARE",
            "UPDATE_TRAILER_OFF",
            "UPDATE_IMAGE_SIZE",
            "APP_LIMIT",
            "LAYOUT_ID",
            "SECURITY_COUNTER",
            "ROLLBACK_COUNTER",
            "POLICY_SWAP",
            "POLICY_OVERWRITE_EXTERNAL",
            "POLICY_SINGLE",
            "SWAP_USING_OFFSET",
            "BOOTSTRAP",
            "OVERWRITE_ONLY",
            "SINGLE_APPLICATION_SLOT",
            "SWAP_USING_MOVE",
            "SWAP_USING_SCRATCH",
            "USE_MBED_TLS",
            "FIH_LEVEL",
            "SECCNT_FLASH",
            "SECCNT_PORT",
            "DOWNGRADE_PREVENTION",
            "CONFIRM_AUTO",
            "HASH_STORAGE_DIRECTLY",
            "VALIDATE_PRIMARY_SLOT",
            "DFU_ENABLE",
            "DFU_VID",
            "DFU_PID",
            "FSLOAD_FAT",
            "FSLOAD_LFS2",
            "FSLOAD_RAW",
            "FSLOAD_GZIP",
            "ECC_SHADOW",
            "MIN_IMAGE_SIZE",
        )
    ]
    + ["MCUBOOT_DEV%d_%s" % (d, f) for d in (0, 1) for f in DEVICE_FIELDS]
    + [
        "MCUBOOT_DEV%d_RUN%d_%s" % (d, j, f)
        for d in (0, 1)
        for j in range(MAX_RUNS)
        for f in ("SIZE", "ERASE")
    ]
)

FIH_LEVELS = ("off", "low", "medium", "high")
FSLOAD_TYPES = ("raw", "fat", "lfs2", "gzip")

_TOKEN = re.compile(
    r"\s*(?:(0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*|(<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^~!<>?:()]))"
)

# Binary operators by precedence, as in C: a higher number binds tighter.
_PREC = {
    "||": 1,
    "&&": 2,
    "|": 3,
    "^": 4,
    "&": 5,
    "==": 6,
    "!=": 6,
    "<": 7,
    ">": 7,
    "<=": 7,
    ">=": 7,
    "<<": 8,
    ">>": 8,
    "+": 9,
    "-": 9,
    "*": 10,
    "/": 10,
    "%": 10,
}


def _c_div(a, b):
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


_BINOPS = {
    "||": lambda a, b: int(bool(a) or bool(b)),
    "&&": lambda a, b: int(bool(a) and bool(b)),
    "|": lambda a, b: a | b,
    "^": lambda a, b: a ^ b,
    "&": lambda a, b: a & b,
    "==": lambda a, b: int(a == b),
    "!=": lambda a, b: int(a != b),
    "<": lambda a, b: int(a < b),
    ">": lambda a, b: int(a > b),
    "<=": lambda a, b: int(a <= b),
    ">=": lambda a, b: int(a >= b),
    "<<": lambda a, b: a << b,
    ">>": lambda a, b: a >> b,
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": _c_div,
    "%": lambda a, b: a - _c_div(a, b) * b,
}


def evaluate(expr):
    """Value of a preprocessed constant expression: integers with C suffixes, parentheses, the
    unary operators - + ~ !, the binary operators of C (arithmetic, shifts, comparisons, bit
    operations, && and ||) and the conditional operator. Both arms of a conditional are
    evaluated, so a division by zero in the arm that is not taken is an error."""
    tokens = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN.match(expr, pos)
        if m is None:
            if expr[pos:].strip() == "":
                break
            raise ValueError("not a constant expression: " + expr)
        tokens.append(int(m.group(1), 0) if m.group(1) is not None else m.group(2))
        pos = m.end()
    state = {"i": 0}

    def peek():
        return tokens[state["i"]] if state["i"] < len(tokens) else None

    def take():
        tok = peek()
        state["i"] += 1
        return tok

    def primary():
        tok = take()
        if isinstance(tok, int):
            return tok
        if tok == "(":
            v = conditional()
            if take() != ")":
                raise ValueError("not a constant expression: " + expr)
            return v
        if tok in ("-", "+", "~", "!"):
            v = primary()
            return {"-": -v, "+": v, "~": ~v, "!": int(not v)}[tok]
        raise ValueError("not a constant expression: " + expr)

    def binary(min_prec):
        v = primary()
        while peek() in _PREC and _PREC[peek()] >= min_prec:
            op = take()
            r = binary(_PREC[op] + 1)
            v = _BINOPS[op](v, r)
        return v

    def conditional():
        c = binary(1)
        if peek() == "?":
            take()
            a = conditional()
            if take() != ":":
                raise ValueError("not a constant expression: " + expr)
            b = conditional()
            return a if c else b
        return c

    value = conditional()
    if state["i"] != len(tokens):
        raise ValueError("not a constant expression: " + expr)
    return value


def probe(cmd):
    """Values of NAMES as the preprocessor sees them. A name that is not defined is absent."""
    text = '#include "mcuboot_layout.h"\n#define V(n) @ #n n\n'
    text += "".join("V(%s)\n" % n for n in NAMES)
    r = subprocess.run(
        cmd + ["-E", "-P", "-x", "c", "-"], input=text, capture_output=True, text=True
    )
    if r.returncode != 0:
        sys.exit("mcuboot_gen: the preprocessor failed:\n" + r.stderr)
    values = {}
    for line in r.stdout.splitlines():
        m = re.match(r'@ "(\w+)" (.*)$', line)
        if not m or m.group(2).strip() == m.group(1):
            continue
        name, expr = m.groups()
        expr = expr.strip()
        values[name] = expr[1:-1] if expr.startswith('"') else evaluate(expr)
    return values


def read_mpy_version():
    text = (TOP / "py" / "mpconfig.h").read_text()
    parts = []
    for name in ("MAJOR", "MINOR", "MICRO"):
        m = re.search(r"^#define MICROPY_VERSION_%s\s+([0-9]+)\s*$" % name, text, re.M)
        if not m:
            sys.exit("mcuboot_gen: MICROPY_VERSION_%s not found in py/mpconfig.h" % name)
        parts.append(int(m.group(1)))
    return tuple(parts)


def build(v, board):
    g = lambda n, d=None: v.get("MCUBOOT_" + n, d)
    devices = []
    for d in (0, 1):
        if "MCUBOOT_DEV%d_BASE" % d not in v:
            break
        runs = []
        off = 0
        for j in range(v["MCUBOOT_DEV%d_RUNS" % d]):
            size = v["MCUBOOT_DEV%d_RUN%d_SIZE" % (d, j)]
            runs.append(
                {"off": off, "size": size, "erase": v["MCUBOOT_DEV%d_RUN%d_ERASE" % (d, j)]}
            )
            off += size
        devices.append(
            {
                "id": d,
                "name": v["MCUBOOT_DEV%d_NAME" % d],
                "base": v["MCUBOOT_DEV%d_BASE" % d],
                "size": v["MCUBOOT_DEV%d_SIZE" % d],
                "runs": runs,
                "write": v["MCUBOOT_DEV%d_WRITE" % d],
                "erased_val": v["MCUBOOT_DEV%d_ERASED_VAL" % d],
                "mapped": v["MCUBOOT_DEV%d_MAPPED" % d],
                "ecc": v["MCUBOOT_DEV%d_ECC" % d],
            }
        )
    areas = {}
    for a in AREAS:
        addr = g(a.upper() + "_ADDR")
        size = g(a.upper() + "_SIZE")
        if addr is None or size is None or size == 0:
            continue
        dev = max((d["id"] for d in devices if addr >= d["base"]), default=0)
        areas[a] = {
            "id": AREA_IDS[a],
            "dev": dev,
            "off": addr - devices[dev]["base"],
            "size": size,
            "addr": addr,
            "erase": g(a.upper() + "_UNIT"),
        }
    header = g("HEADER_SIZE")
    max_align = g("MAX_ALIGN_VALUE")
    layout_id = "%08x" % g("LAYOUT_ID")
    version = "%d.%d.%d" % read_mpy_version()

    if g("POLICY_SINGLE"):
        policy = "single"
    elif g("POLICY_OVERWRITE_EXTERNAL"):
        policy = "overwrite-external"
    else:
        policy = "swap"
    swap_mode = None
    if policy == "swap":
        swap_mode = (
            "offset" if g("SWAP_USING_OFFSET") else "move" if g("SWAP_USING_MOVE") else "scratch"
        )
    if g("SECCNT_PORT"):
        rollback = "counter-port"
    elif g("SECCNT_FLASH"):
        rollback = "counter-flash"
    elif g("DOWNGRADE_PREVENTION"):
        rollback = "version"
    else:
        rollback = "none"
    fsload = [t for t in FSLOAD_TYPES if g("FSLOAD_" + t.upper())]

    sign_args = ["--version", version + "+0", "--header-size", "0x%X" % header, "--pad-header"]
    if policy == "overwrite-external":
        sign_args.append("--overwrite-only")
    sign_args += ["--align", str(g("MAX_WRITE_UNIT"))]
    if max_align > 8:
        sign_args += ["--max-align", str(max_align)]
    sign_args += [
        "--slot-size",
        "0x%X" % areas["primary"]["size"],
        "--max-sectors",
        str(g("MAX_IMG_SECTORS")),
        "--erased-val",
        "0xff" if devices[0]["erased_val"] == 0xFF else "0",
        "--security-counter",
        str(g("SECURITY_COUNTER", 0)),
        "--public-key-format",
        "hash",
        "--custom-tlv",
        "0x%04X" % LAYOUT_TLV_TAG,
        "0x" + layout_id,
    ]
    # Image for the first programming of the primary slot. It carries the trailer magic and
    # image_ok, except with overwrite-external: imgtool writes no image_ok there, and bootutil
    # only raises the security counter of a slot with a good magic when image_ok is set. So that
    # image has no trailer, and its counter is the stored one after the first boot.
    initial_args = (
        sign_args if policy == "overwrite-external" else sign_args + ["--pad", "--confirm"]
    )
    dfu_addr = g("UPDATE_ADDR") + g("UPDATE_SPARE")
    return {
        "schema": 1,
        "board": board,
        "layout_id": layout_id,
        "policy": policy,
        "swap_mode": swap_mode,
        "header_size": header,
        "max_align": max_align,
        "max_img_sectors": g("MAX_IMG_SECTORS"),
        "max_image_size": g("MAX_IMAGE_SIZE"),
        "min_image_size": g("MIN_IMAGE_SIZE"),
        "trailer_size": g("TRAILER_SIZE"),
        "app_start": areas["primary"]["addr"] + header,
        "app_len": g("APP_LIMIT") - header - g("TLV_RESERVE"),
        "rollback": rollback,
        "crypto": "mbedtls" if g("USE_MBED_TLS") else "tinycrypt",
        "fih": FIH_LEVELS[g("FIH_LEVEL")],
        "confirm_mode": "auto" if g("CONFIRM_AUTO") else "manual",
        "hash_direct": bool(g("HASH_STORAGE_DIRECTLY")),
        "validate_primary": bool(g("VALIDATE_PRIMARY_SLOT")),
        "devices": devices,
        "areas": areas,
        "keys": {"files": [], "key_id": 0, "pub_sha256": []},
        "imgtool": {
            "version": version + "+0",
            "align": g("MAX_WRITE_UNIT"),
            "max_align": max_align if max_align > 8 else None,
            "sign_args": sign_args,
            "initial_args": initial_args,
        },
        "dfu": {
            "enabled": bool(g("DFU_ENABLE")),
            "vid": g("DFU_VID"),
            "pid": g("DFU_PID"),
            "write_align": g("SECONDARY_WRITE"),
            "update_addr": dfu_addr,
            "regions": [
                {
                    "name": "Application" if policy == "single" else "Secondary slot",
                    "addr": dfu_addr,
                    "size": g("UPDATE_IMAGE_SIZE"),
                }
            ],
        },
        "fsload": fsload,
        "bl_version": version,
    }


def render_ld(lay):
    a = lay["areas"]
    out = ["/* Generated by tools/mcuboot_gen.py. Do not edit. */", ""]
    # Plain assignments rather than PROVIDE: ld accepts them in MEMORY expressions.
    for area in ("boot", "primary", "secondary"):
        if area in a:
            out.append("MCUBOOT_%s_START = 0x%08X;" % (area.upper(), a[area]["addr"]))
            out.append("MCUBOOT_%s_SIZE = 0x%X;" % (area.upper(), a[area]["size"]))
    out.append("MCUBOOT_HEADER_SIZE = 0x%X;" % lay["header_size"])
    out.append("MCUBOOT_MAX_IMAGE_SIZE = 0x%X;" % lay["max_image_size"])
    out.append("MCUBOOT_APP_START = MCUBOOT_PRIMARY_START + MCUBOOT_HEADER_SIZE;")
    out.append("MCUBOOT_APP_LEN = 0x%X;" % lay["app_len"])
    for area in ("log", "seccnt", "shadow", "scratch", "intent", "fs"):
        if area in a:
            out.append("MCUBOOT_%s_START = 0x%08X;" % (area.upper(), a[area]["addr"]))
            out.append("MCUBOOT_%s_SIZE = 0x%X;" % (area.upper(), a[area]["size"]))
    return "\n".join(out) + "\n"


def render_mk(lay):
    fsload = lay["fsload"]
    pairs = [
        ("MCUBOOT_LAYOUT_ID", lay["layout_id"]),
        ("MCUBOOT_APP_LINK_ADDR", "0x%08X" % lay["app_start"]),
        ("MCUBOOT_PRIMARY_ADDR", "0x%08X" % lay["areas"]["primary"]["addr"]),
        ("MCUBOOT_DFU_UPDATE_ADDR", "0x%08X" % lay["dfu"]["update_addr"]),
        ("MCUBOOT_DFU_ENABLE", str(int(lay["dfu"]["enabled"]))),
        ("MCUBOOT_DFU_WRITE_ALIGN", str(lay["dfu"]["write_align"])),
        ("MCUBOOT_POLICY", lay["policy"]),
        ("MCUBOOT_SWAP_MODE", lay["swap_mode"] or ""),
        ("MCUBOOT_CRYPTO", lay["crypto"]),
        ("MCUBOOT_ROLLBACK", lay["rollback"]),
        ("MCUBOOT_FIH", lay["fih"]),
        ("MCUBOOT_CONFIRM_MODE", lay["confirm_mode"]),
        ("MCUBOOT_HASH_DIRECT", str(int(lay["hash_direct"]))),
        ("MCUBOOT_VALIDATE_PRIMARY", str(int(lay["validate_primary"]))),
        ("MCUBOOT_FSLOAD", " ".join(fsload)),
        ("MCUBOOT_FSLOAD_ENABLE", str(int(bool(fsload)))),
        ("MCUBOOT_SPIFLASH_ENABLE", str(int(len(lay["devices"]) > 1))),
        ("MCUBOOT_BL_VERSION", lay["bl_version"]),
        ("MCUBOOT_IMGTOOL_SIGN_ARGS", " ".join(lay["imgtool"]["sign_args"])),
        ("MCUBOOT_IMGTOOL_INITIAL_ARGS", " ".join(lay["imgtool"]["initial_args"])),
    ]
    out = ["# Generated by tools/mcuboot_gen.py. Do not edit.", ""]
    return "\n".join(out + ["%s := %s" % (k, v) if v else "%s :=" % k for k, v in pairs]) + "\n"


def load_key(path):
    scripts = str(TOP / "lib" / "mcuboot" / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from imgtool import keys

    key = keys.load(str(path))
    if key is None or type(key).__name__ not in ("ECDSA256P1", "ECDSA256P1Public"):
        sys.exit("mcuboot_gen: %s is not an ECDSA P-256 key" % path)
    return key


def render_keys_c(key, path):
    import io

    buf = io.StringIO()
    key.emit_c_public(file=buf, name_suffix="_0")
    return (
        "// Generated by tools/mcuboot_gen.py from %s. Do not edit.\n\n"
        '#include "bootutil/sign_key.h"\n\n%s\n'
        "const struct bootutil_key bootutil_keys[] = {\n"
        "    {\n        .key = ecdsa_pub_key_0,\n        .len = &ecdsa_pub_key_0_len,\n    },\n};\n"
        "const int bootutil_key_cnt = 1;\n" % (path, buf.getvalue().rstrip("\n"))
    )


def write_if_changed(path, text):
    if not path.exists() or path.read_text() != text:
        path.write_text(text)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    split = argv.index("--") if "--" in argv else len(argv)
    ap = argparse.ArgumentParser(
        description=__doc__, usage="%(prog)s [options] -- compiler options"
    )
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--board", default="", help="board name for the layout json")
    ap.add_argument("--key", help="public or private PEM key the bootloader accepts")
    args = ap.parse_args(argv[:split])
    cmd = argv[split + 1 :]
    if not cmd:
        ap.error("the preprocessor command is missing after --")

    lay = build(probe(cmd), args.board)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.key:
        import hashlib

        key = load_key(args.key)
        lay["keys"] = {
            "files": [str(args.key)],
            "key_id": 0,
            "pub_sha256": [hashlib.sha256(key.get_public_bytes()).hexdigest()],
        }
        write_if_changed(out / "mcuboot_keys_gen.c", render_keys_c(key, args.key))
    write_if_changed(out / "mcuboot_layout.ld", render_ld(lay))
    write_if_changed(out / "mcuboot_layout.mk", render_mk(lay))
    write_if_changed(out / "mcuboot_layout.json", json.dumps(lay, indent=2) + "\n")


if __name__ == "__main__":
    main()
