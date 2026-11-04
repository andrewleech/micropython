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
Create, inspect and unpack DFU files.

Two file formats are handled, both ending in the 16 byte DFU suffix of the USB DFU 1.1
specification (a CRC-32 over the file, the USB vendor and product IDs to match, and the format
version):

  plain  One firmware payload, as downloaded by `dfu-util -D`.
  dfuse  The STMicroelectronics DfuSe format (UM0391): a list of targets (DFU alternate
         settings), each holding elements, which are binary blobs with a memory address.

Used as a module, DfuFile.dfuse() and DfuFile.plain() build a file and DfuFile.read() loads one.
Only the Python standard library is needed.

  create_dfu.py create -o out.dfu --usb 0483:df11 0x08000000:firmware.bin
  create_dfu.py create -o out.dfu --plain firmware.bin
  create_dfu.py info out.dfu
  create_dfu.py extract out.dfu -d outdir
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import zlib
from dataclasses import dataclass, field

# DFU suffix: bcdDevice, idProduct, idVendor, bcdDFU, "UFD", bLength, dwCRC.
_SUFFIX = struct.Struct("<4H3sBI")
SUFFIX_LEN = _SUFFIX.size
_SUFFIX_SIG = b"UFD"

# bcdDFU of the suffix. Readers accept 0x0101 which some tools write for DFU 1.1.
VERSION_PLAIN = 0x0100
VERSION_DFUSE = 0x011A
_VERSIONS_PLAIN = (VERSION_PLAIN, 0x0101)

# DfuSe prefix: "DfuSe", format version, total size of prefix and targets, target count.
_PREFIX = struct.Struct("<5sBIB")
_PREFIX_SIG = b"DfuSe"
# DfuSe target header: "Target", alternate setting, name is valid, name, size of the
# elements, element count.
_TARGET = struct.Struct("<6sBI255sII")
_TARGET_SIG = b"Target"
_NAME_LEN = 255
# DfuSe element header: address, size.
_ELEMENT = struct.Struct("<II")

WILDCARD_ID = 0xFFFF


class DfuError(Exception):
    pass


@dataclass
class Element:
    addr: int
    data: bytes


@dataclass
class Target:
    """The elements loaded to one DFU alternate setting."""

    elements: list[Element]
    alt: int = 0
    name: str | None = None


@dataclass
class DfuFile:
    """A DFU file: the targets of a DfuSe file or the payload of a plain file, and the suffix
    fields. A vendor or product ID of 0xFFFF matches any device."""

    targets: list[Target] | None = None
    payload: bytes | None = None
    vid: int = WILDCARD_ID
    pid: int = WILDCARD_ID
    device: int = 0xFFFF
    elements: list[Element] = field(init=False, repr=False, default_factory=list)

    def __post_init__(self):
        if (self.targets is None) == (self.payload is None):
            raise DfuError("a DFU file holds either targets or a plain payload")
        if self.targets is not None:
            self.elements = [e for t in self.targets for e in t.elements]

    @classmethod
    def dfuse(cls, targets, vid=WILDCARD_ID, pid=WILDCARD_ID, device=0xFFFF):
        return cls(targets=list(targets), vid=vid, pid=pid, device=device)

    @classmethod
    def plain(cls, payload, vid=WILDCARD_ID, pid=WILDCARD_ID, device=0xFFFF):
        return cls(payload=bytes(payload), vid=vid, pid=pid, device=device)

    def to_bytes(self, align=1):
        """Serialise the file. In a DfuSe file every element is padded with 0xFF to a multiple of
        align bytes, as flash controllers that program in words need."""
        if self.payload is not None:
            body, version = self.payload, VERSION_PLAIN
        else:
            body, version = _encode_dfuse(self.targets, align), VERSION_DFUSE
        body += struct.pack(
            "<4H3sB", self.device, self.pid, self.vid, version, _SUFFIX_SIG, SUFFIX_LEN
        )
        return body + struct.pack("<I", _crc(body))

    def write(self, path, align=1):
        with open(path, "wb") as f:
            f.write(self.to_bytes(align))

    @classmethod
    def from_bytes(cls, data):
        if len(data) < SUFFIX_LEN:
            raise DfuError("too short to be a DFU file")
        device, pid, vid, version, sig, length, crc = _SUFFIX.unpack(data[-SUFFIX_LEN:])
        if sig != _SUFFIX_SIG or length != SUFFIX_LEN:
            raise DfuError("no DFU suffix")
        if crc != _crc(data[:-4]):
            raise DfuError("CRC mismatch")
        body = data[:-SUFFIX_LEN]
        if version == VERSION_DFUSE:
            return cls(targets=_decode_dfuse(body), vid=vid, pid=pid, device=device)
        if version in _VERSIONS_PLAIN:
            return cls(payload=body, vid=vid, pid=pid, device=device)
        raise DfuError(f"unsupported DFU suffix version 0x{version:04x}")

    @classmethod
    def read(cls, path):
        with open(path, "rb") as f:
            return cls.from_bytes(f.read())


def _crc(data):
    # The DFU CRC is the standard CRC-32 without the final inversion.
    return ~zlib.crc32(data) & 0xFFFFFFFF


def _encode_dfuse(targets, align):
    blob = b""
    for target in targets:
        elements = b""
        for element in target.elements:
            data = element.data + b"\xff" * (-len(element.data) % align)
            elements += _ELEMENT.pack(element.addr, len(data)) + data
        name = (target.name or "").encode()
        if len(name) >= _NAME_LEN:
            raise DfuError("target name is too long")
        blob += (
            _TARGET.pack(
                _TARGET_SIG,
                target.alt,
                1 if target.name else 0,
                name,
                len(elements),
                len(target.elements),
            )
            + elements
        )
    return _PREFIX.pack(_PREFIX_SIG, 1, _PREFIX.size + len(blob), len(targets)) + blob


def _decode_dfuse(body):
    if len(body) < _PREFIX.size:
        raise DfuError("truncated DfuSe prefix")
    sig, version, size, num_targets = _PREFIX.unpack_from(body)
    if sig != _PREFIX_SIG or version != 1:
        raise DfuError("not a DfuSe file")
    if size != len(body):
        raise DfuError(f"DfuSe size is {size}, the file has {len(body)} bytes")
    pos = _PREFIX.size
    targets = []
    for _ in range(num_targets):
        if pos + _TARGET.size > size:
            raise DfuError("truncated DfuSe target")
        sig, alt, named, name, tsize, num_elements = _TARGET.unpack_from(body, pos)
        if sig != _TARGET_SIG:
            raise DfuError("bad DfuSe target signature")
        pos += _TARGET.size
        end = pos + tsize
        if end > size:
            raise DfuError("DfuSe target extends beyond the file")
        elements = []
        for _ in range(num_elements):
            if pos + _ELEMENT.size > end:
                raise DfuError("truncated DfuSe element")
            addr, esize = _ELEMENT.unpack_from(body, pos)
            pos += _ELEMENT.size
            if pos + esize > end:
                raise DfuError("DfuSe element extends beyond its target")
            elements.append(Element(addr, body[pos : pos + esize]))
            pos += esize
        if pos != end:
            raise DfuError("DfuSe target size does not match its elements")
        name = name.split(b"\0", 1)[0].decode(errors="replace") if named else None
        targets.append(Target(elements, alt, name))
    if pos != size:
        raise DfuError("trailing data after the DfuSe targets")
    return targets


# ---------------------------------------------------------------------------
# Command line


def _int(text):
    try:
        return int(text, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid number: {text!r}")


def _usb_id(text):
    try:
        vid, pid = (int(x, 16) for x in text.split(":"))
        if not (0 <= vid <= 0xFFFF and 0 <= pid <= 0xFFFF):
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected VID:PID in hex, got {text!r}")
    return vid, pid


def _element_arg(text):
    addr, sep, path = text.partition(":")
    if not sep:
        raise argparse.ArgumentTypeError(f"expected ADDRESS:FILE, got {text!r}")
    return _int(addr), path


def _read_file(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as exc:
        raise DfuError(f"{path}: {exc.strerror}")


def cmd_create(args):
    vid, pid = args.usb
    if args.plain:
        if len(args.inputs) != 1:
            raise DfuError("--plain takes one input file")
        dfu = DfuFile.plain(_read_file(args.inputs[0]), vid, pid, args.device)
    else:
        elements = [Element(a, _read_file(p)) for a, p in map(_element_arg, args.inputs)]
        dfu = DfuFile.dfuse([Target(elements, args.alt, args.name)], vid, pid, args.device)
    dfu.write(args.output, args.align)
    print(f"{args.output}: {os.path.getsize(args.output)} bytes")


def cmd_info(args):
    dfu = DfuFile.read(args.file)
    print(f"{args.file}: {'plain' if dfu.payload is not None else 'DfuSe'}")
    print(f"  usb {dfu.vid:04x}:{dfu.pid:04x}, device 0x{dfu.device:04x}")
    if dfu.payload is not None:
        print(f"  payload {len(dfu.payload)} bytes")
    for i, target in enumerate(dfu.targets or ()):
        print(f"  target {i}: alt {target.alt}, name {target.name!r}")
        for e in target.elements:
            print(f"    0x{e.addr:08x} {len(e.data)} bytes")


def cmd_extract(args):
    dfu = DfuFile.read(args.file)
    stem = os.path.join(args.dir, os.path.basename(args.file))
    os.makedirs(args.dir, exist_ok=True)
    if dfu.payload is not None:
        outputs = [(f"{stem}.bin", dfu.payload)]
    else:
        outputs = [
            (f"{stem}.alt{t.alt}.0x{e.addr:08x}.bin", e.data)
            for t in dfu.targets
            for e in t.elements
        ]
    for path, data in outputs:
        with open(path, "wb") as f:
            f.write(data)
        print(f"{path}: {len(data)} bytes")


def build_parser():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create", help="build a DFU file")
    p.add_argument("-o", "--output", required=True)
    p.add_argument(
        "--usb",
        type=_usb_id,
        default=(WILDCARD_ID, WILDCARD_ID),
        metavar="VID:PID",
        help="USB IDs in hex the file is for, default any device",
    )
    p.add_argument("--device", type=_int, default=0xFFFF, help="bcdDevice to match, default any")
    p.add_argument("--plain", action="store_true", help="plain DFU file of one input file")
    p.add_argument("--alt", type=_int, default=0, help="DfuSe alternate setting")
    p.add_argument("--name", help="DfuSe target name")
    p.add_argument(
        "--align",
        type=_int,
        default=1,
        help="pad each DfuSe element to a multiple of this many bytes",
    )
    p.add_argument("inputs", nargs="+", metavar="INPUT", help="ADDRESS:FILE, or FILE with --plain")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("info", help="show the contents of a DFU file")
    p.add_argument("file")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("extract", help="write the contents of a DFU file to binary files")
    p.add_argument("file")
    p.add_argument("-d", "--dir", default=".")
    p.set_defaults(func=cmd_extract)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (DfuError, OSError) as exc:
        sys.stderr.write(f"create_dfu: error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
