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
Sign and package MicroPython images for the MCUboot bootloader.

A wrapper around imgtool. imgtool is imported as a module from lib/mcuboot/scripts, the copy
that matches the bootutil sources in the tree; a system installed imgtool is never used. The
imgtool argument lines and the flash layout numbers come from the mboot_layout.json written by
tools/mboot_gen.py.

Subcommands (sign is the default):

  sign       Create a signed update image. With --external digest, write the digest to be
             signed by an HSM instead. With --external apply, insert the signature the HSM
             returned.
  initial    Create the image for the first programming of the primary slot: padded to the slot
             size, trailer magic written, image_ok set. A .hex output extension selects Intel
             HEX at the primary slot address, anything else gives a padded binary.
  dfu        Wrap a signed image into a DfuSe file for the secondary slot.
  check-key  Report whether a key is one of the public test keys.

The signing key has to be one of the bootloader keys of the layout. Every image written is
checked against that key (imgtool verify) and against the layout (header size, size limits,
layout id TLV), and is removed if a check fails.
"""

import argparse
import contextlib
import hashlib
import io
import json
import struct
import subprocess
import sys
import tempfile
from collections import namedtuple
from pathlib import Path

TOP = Path(__file__).resolve().parent.parent
IMGTOOL_DIR = TOP / "lib" / "mcuboot" / "scripts"

sys.path.insert(0, str(IMGTOOL_DIR))

try:
    import click
    from cryptography.exceptions import UnsupportedAlgorithm
    from cryptography.hazmat.primitives import serialization
    from imgtool import image as imgimage
    from imgtool import keys as imgkeys
    from imgtool import main as imgmain
    from intelhex import IntelHex
except ImportError as exc:
    sys.exit(
        "mboot_sign: cannot import imgtool dependencies (%s); install the packages in %s"
        % (exc, IMGTOOL_DIR / "requirements.txt")
    )

DEFAULT_KEY = TOP / "tests" / "mboot" / "keys" / "test-ecdsa-p256.pem"

# Image format (lib/mcuboot/boot/bootutil/include/bootutil/image.h).
HEADER_FMT = "<IIHHIIBBHII"  # magic, load addr, hdr size, ptlv size, img size, flags, version, pad
TLV_SEC_CNT = 0x50
TLV_LAYOUT_ID = 0x00A1  # custom TLV carrying MBOOT_LAYOUT_ID


class SignError(Exception):
    pass


# ---------------------------------------------------------------------------
# Keys


def _load_cryptography_public(path):
    data = Path(path).read_bytes()
    try:
        key = serialization.load_pem_private_key(data, None).public_key()
    except ValueError:
        key = serialization.load_pem_public_key(data)
    except TypeError:
        raise SignError("%s: password protected keys are not supported" % path)
    return key


def load_public_key(path):
    """Return the cryptography public key of a private or public PEM file."""
    try:
        return _load_cryptography_public(path)
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise SignError("%s: cannot load key: %s" % (path, exc))


def public_key_spki(key):
    return key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def public_fingerprint(path):
    """SHA-256 of the DER SubjectPublicKeyInfo, the value imgtool puts in the key hash TLV."""
    return hashlib.sha256(public_key_spki(load_public_key(path))).hexdigest()


def known_test_keys(top=TOP):
    """Return {fingerprint: path} for the public test keys (lib/mcuboot/*.pem and
    tests/mboot/keys/*.pem). A production build refuses these."""
    found = {}
    pems = sorted(Path(top).glob("lib/mcuboot/*.pem"))
    pems += sorted(Path(top).glob("tests/mboot/keys/*.pem"))
    for pem in pems:
        try:
            found.setdefault(public_fingerprint(pem), pem)
        except SignError:
            continue
    return found


def check_key(path, production, top=TOP):
    """Reject known public test keys in production mode, warn about them otherwise.

    Returns the matching test key path or None."""
    match = known_test_keys(top).get(public_fingerprint(path))
    if match is None:
        return None
    if production:
        raise SignError(
            "%s is a public test key (same public key as %s); refusing to use it with "
            "MBOOT_PRODUCTION=1" % (path, Path(match).relative_to(top))
        )
    sys.stderr.write("WARNING: signing with a public test key (%s)\n" % path)
    return match


# ---------------------------------------------------------------------------
# Layout (output of tools/mboot_gen.py)

Layout = namedtuple(
    "Layout",
    [
        "layout_id",  # int, 32 bit
        "header_size",
        "primary_addr",
        "primary_size",
        "secondary_addr",  # None for policy single
        "secondary_size",
        "max_image_size",
        "min_image_size",  # an image of fewer bytes is refused by the update path, 0 for none
        "dfu_addr",  # CPU address of the update stream (DFU alt 0), None if DFU is disabled
        "dfu_vid",
        "dfu_pid",
        "sign_args",  # imgtool sign arguments without key, input and output
        "initial_args",
        "key_id",
        "key_files",  # public key PEM files of the bootloader, absolute paths
        "key_hashes",  # SHA-256 of their SPKI, hex
    ],
)

LAYOUT_SCHEMA = 1


def layout_from_json(doc):
    """Map the mboot_layout.json document to a Layout. Nothing else reads that schema
    (schema 1): layout_id, header_size, max_image_size, min_image_size, areas.primary/secondary {addr, size},
    dfu {update_addr, vid, pid}, keys {files, key_id, pub_sha256}, imgtool {sign_args,
    initial_args}. Key files are relative to the repository root unless absolute."""
    try:
        if doc["schema"] != LAYOUT_SCHEMA:
            raise SignError("layout schema %r, expected %d" % (doc["schema"], LAYOUT_SCHEMA))
        areas = doc["areas"]
        secondary = areas.get("secondary")
        dfu = doc.get("dfu") or {}
        return Layout(
            layout_id=int(doc["layout_id"], 16),
            header_size=doc["header_size"],
            primary_addr=areas["primary"]["addr"],
            primary_size=areas["primary"]["size"],
            secondary_addr=secondary["addr"] if secondary else None,
            secondary_size=secondary["size"] if secondary else None,
            max_image_size=doc["max_image_size"],
            min_image_size=doc["min_image_size"],
            dfu_addr=dfu.get("update_addr"),
            dfu_vid=dfu.get("vid"),
            dfu_pid=dfu.get("pid"),
            sign_args=list(doc["imgtool"]["sign_args"]),
            initial_args=list(doc["imgtool"]["initial_args"]),
            key_id=doc["keys"]["key_id"],
            key_files=[TOP / f for f in doc["keys"]["files"]],
            key_hashes=list(doc["keys"]["pub_sha256"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SignError("layout JSON does not have the expected content: %r" % (exc,))


def load_layout(path):
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError) as exc:
        raise SignError("%s: cannot read layout: %s" % (path, exc))
    return layout_from_json(doc)


def check_known_key(layout, key_path):
    """The bootloader only accepts images signed by one of its built-in public keys."""
    if public_fingerprint(key_path) not in layout.key_hashes:
        raise SignError(
            "%s is not one of the bootloader keys (MBOOT_PUBKEYS), the image would be rejected"
            % key_path
        )


# ---------------------------------------------------------------------------
# imgtool argument handling


def get_option(args, names):
    for i, a in enumerate(args[:-1]):
        if a in names:
            return args[i + 1]
    return None


def set_option(args, names, value):
    args = list(args)
    for i, a in enumerate(args[:-1]):
        if a in names:
            args[i + 1] = str(value)
            return args
    return args + [names[0], str(value)]


def resolve_args(layout, args, version=None, counter=None):
    """Apply command line overrides to an imgtool argument list, then check it against the
    layout."""
    args = list(args)
    if version is not None:
        args = set_option(args, ("--version", "-v"), version)
    if counter is not None:
        args = set_option(args, ("--security-counter", "-s"), counter)
    for names, expect, what in (
        (("--header-size", "-H"), layout.header_size, "header size"),
        (("--slot-size", "-S"), layout.primary_size, "slot size"),
    ):
        value = get_option(args, names)
        if value is None or int(value, 0) != expect:
            raise SignError(
                "imgtool arguments and layout disagree on the %s (%s vs 0x%x)"
                % (what, value, expect)
            )
    return args


def imgtool_sign(args):
    """Run `imgtool sign` in-process."""
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            imgmain.sign.main(args=args, prog_name="imgtool sign", standalone_mode=False)
    except click.ClickException as exc:
        raise SignError("imgtool sign: %s" % exc.format_message())


# ---------------------------------------------------------------------------
# Output checks

ImageInfo = namedtuple("ImageInfo", ["hdr_size", "total", "version", "counter", "prot"])


def read_image_file(path):
    if Path(path).suffix.lower() == ".hex":
        return IntelHex(str(path)).tobinstr()
    return Path(path).read_bytes()


def image_info(data):
    """Header size, total size (header, body and both TLV areas), version tuple, security counter
    (None without the TLV) and protected TLVs ({tag: value}) of an image that imgtool verify
    accepted."""
    (_magic, _load, hdr, ptlv, size, _flags, major, minor, rev, build, _pad) = struct.unpack_from(
        HEADER_FMT, data
    )
    off = hdr + size
    prot = {}
    pos = off + 4
    while pos + 4 <= off + ptlv:
        tag, length = struct.unpack_from("<HH", data, pos)
        prot[tag] = bytes(data[pos + 4 : pos + 4 + length])
        pos += 4 + length
    tlv_total = struct.unpack_from("<2xH", data, off + ptlv)[0]
    counter = prot.get(TLV_SEC_CNT)
    return ImageInfo(
        hdr,
        off + ptlv + tlv_total,
        (major, minor, rev, build),
        None if counter is None else struct.unpack("<I", counter)[0],
        prot,
    )


def checked_image(path, layout, key_path):
    """Check an image written by imgtool: hash and signature against the key, then header size,
    size limit and layout id against the layout. The file is removed if a check fails, so no bad
    image is left behind. Returns the ImageInfo."""
    try:
        result = imgimage.Image.verify(str(path), imgkeys.load(str(key_path)))[0]
        if result != imgimage.VerifyResult.OK:
            raise SignError("%s: imgtool verify: %s" % (path, result.name))
        info = image_info(read_image_file(path))
        if info.hdr_size != layout.header_size:
            raise SignError(
                "%s: header size 0x%x, layout has 0x%x" % (path, info.hdr_size, layout.header_size)
            )
        if info.total > layout.max_image_size:
            raise SignError(
                "%s: image is 0x%x bytes, limit is 0x%x"
                % (path, info.total, layout.max_image_size)
            )
        if info.total < layout.min_image_size:
            raise SignError(
                "%s: image is 0x%x bytes, the layout takes at least 0x%x (an image of at most one "
                "erase unit cannot be swapped safely with swap using offset)"
                % (path, info.total, layout.min_image_size)
            )
        if info.prot.get(TLV_LAYOUT_ID) != layout.layout_id.to_bytes(4, "big"):
            raise SignError("%s: layout id TLV is missing or not %08x" % (path, layout.layout_id))
    except SignError:
        Path(path).unlink()
        raise
    return info


# ---------------------------------------------------------------------------
# Commands


def _describe(path, info, key_path):
    print(
        "%s: %d bytes, version %d.%d.%d+%d, counter %s, key %s"
        % (path, info.total, *info.version, info.counter, public_fingerprint(key_path)[:8])
    )


def cmd_sign(args, layout):
    infile, outfile = args.input, args.output
    imgargs = resolve_args(layout, layout.sign_args, args.version, args.security_counter)
    if args.external == "digest":
        imgtool_sign(imgargs + ["--vector-to-sign", "digest", infile, outfile])
        print("%s: SHA-256 to be signed: %s" % (outfile, Path(outfile).read_bytes().hex()))
        return 0
    if args.external == "apply":
        key = args.pubkey or layout.key_files[layout.key_id]
        if not args.sig:
            raise SignError("--external apply needs --sig")
        imgargs += ["--fix-sig", str(args.sig), "--fix-sig-pubkey", str(key)]
    else:
        key = args.key
        imgargs += ["-k", str(key)]
    check_key(key, args.production)
    check_known_key(layout, key)
    imgtool_sign(imgargs + [infile, outfile])
    _describe(outfile, checked_image(outfile, layout, key), key)
    return 0


def cmd_initial(args, layout):
    imgargs = resolve_args(layout, layout.initial_args, args.version, args.security_counter)
    check_key(args.key, args.production)
    check_known_key(layout, args.key)
    erased = int(get_option(imgargs, ("--erased-val", "-R")) or "0xff", 0)
    imgargs += ["-k", str(args.key), "--hex-addr", hex(layout.primary_addr)]
    with tempfile.TemporaryDirectory() as tmp:
        # imgtool writes only the image and the trailer words. The binary is the whole slot
        # built from that same signed image, so both outputs carry the same signature.
        hex_path = Path(args.hex) if args.hex else Path(tmp) / "initial.hex"
        imgtool_sign(imgargs + [args.input, str(hex_path)])
        info = checked_image(hex_path, layout, args.key)
        ih = IntelHex(str(hex_path))
        if ih.minaddr() != layout.primary_addr:
            raise SignError("%s does not start at the primary slot address" % hex_path)
        if ih.maxaddr() >= layout.primary_addr + layout.primary_size:
            raise SignError("%s extends beyond the primary slot" % hex_path)
        ih.padding = erased
        binary = ih.tobinstr(start=layout.primary_addr, size=layout.primary_size)
    Path(args.output).write_bytes(binary)
    _describe(args.output, info, args.key)
    return 0


def cmd_dfu(args, layout):
    if layout.dfu_addr is None:
        raise SignError("the layout has no DFU update address (MBOOT_DFU=0)")
    cmd = [sys.executable, str(TOP / "tools" / "dfu.py")]
    cmd += ["-b", "0x%08x:%s" % (layout.dfu_addr, args.input)]
    if layout.dfu_vid is not None and layout.dfu_pid is not None:
        cmd += ["-D", "0x%04x:0x%04x" % (layout.dfu_vid, layout.dfu_pid)]
    cmd.append(args.output)
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if res.returncode:
        raise SignError("tools/dfu.py failed: %s" % res.stdout.strip())
    print("%s: DfuSe image for address 0x%08x" % (args.output, layout.dfu_addr))
    return 0


def cmd_check_key(args, layout):
    status = 0
    known = known_test_keys()
    for path in args.keys:
        match = known.get(public_fingerprint(path))
        if match is None:
            print("%s: not a known test key" % path)
        else:
            print("%s: public test key (%s)" % (path, Path(match).relative_to(TOP)))
            if args.production:
                status = 1
    return status


def build_parser():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd")

    def add_layout(p):
        p.add_argument("--layout", help="mboot_layout.json written by tools/mboot_gen.py")

    def add_signing(p):
        add_layout(p)
        p.add_argument("-k", "--key", default=str(DEFAULT_KEY), help="signing key (PEM)")
        p.add_argument(
            "--production",
            action="store_true",
            help="refuse the public test keys (MBOOT_PRODUCTION=1)",
        )
        p.add_argument("--version", help="override the image version of the layout, M.m.r+b")
        p.add_argument("--security-counter", help="override the security counter of the layout")
        p.add_argument("input")
        p.add_argument("output")

    p = sub.add_parser("sign", help="signed update image")
    add_signing(p)
    p.add_argument(
        "--external",
        choices=("digest", "apply"),
        help="digest: write the SHA-256 to be signed externally; apply: insert --sig (base64 DER "
        "as written by imgtool --sig-out) made with the private key of the public key --pubkey",
    )
    p.add_argument("--sig", help="externally made signature for --external apply")
    p.add_argument("--pubkey", help="public key (PEM) for --external apply, default the layout's")

    p = sub.add_parser("initial", help="primary slot image: output is the padded slot binary")
    add_signing(p)
    p.add_argument("--hex", help="also keep the sparse Intel HEX file at the primary slot address")

    p = sub.add_parser("dfu", help="DfuSe file for the secondary slot")
    add_layout(p)
    p.add_argument("input")
    p.add_argument("output")

    p = sub.add_parser("check-key", help="is this a public test key")
    p.add_argument("--production", action="store_true", help="exit 1 for a test key")
    p.add_argument("keys", nargs="+")
    return ap


COMMANDS = {
    "sign": cmd_sign,
    "initial": cmd_initial,
    "dfu": cmd_dfu,
    "check-key": cmd_check_key,
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] not in COMMANDS and argv[0] not in ("-h", "--help"):
        argv.insert(0, "sign")
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    try:
        layout = None
        if args.cmd != "check-key":
            if not args.layout:
                raise SignError("--layout is required")
            layout = load_layout(args.layout)
        return COMMANDS[args.cmd](args, layout)
    except SignError as exc:
        sys.stderr.write("mboot_sign: error: %s\n" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
