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
Host side checks and negative test images for MCUboot signed images.

mboot_sign.py has the layout, the imgtool helpers and the signing. This module adds a parser and
a host-side reference of the bootloader validation order (classify, following
shared/mboot/src/validate.c), and the negative test images derived from a signed image
(VARIANT_SPECS, make_variant). tests/mboot/make_variants.py writes them out.
"""

import contextlib
import hashlib
import io
import struct
import sys
from collections import namedtuple
from pathlib import Path

TOP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOP / "tools"))

from mboot_sign import (  # noqa: E402
    HEADER_FMT,
    TLV_LAYOUT_ID,
    TLV_SEC_CNT,
    SignError,
    public_key_spki,
)
from cryptography.exceptions import InvalidSignature  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed  # noqa: E402
from imgtool import image as imgimage  # noqa: E402
from imgtool import keys as imgkeys  # noqa: E402
from imgtool import version as imgversion  # noqa: E402

DEFAULT_OTHER_KEY = TOP / "tests" / "mboot" / "keys" / "other-ecdsa-p256.pem"

# Image format (lib/mcuboot/boot/bootutil/include/bootutil/image.h).
IMAGE_MAGIC = 0x96F3B83D
HEADER_SIZE_MIN = 32
TLV_INFO_MAGIC = 0x6907
TLV_PROT_INFO_MAGIC = 0x6908
TLV_KEYHASH = 0x01
TLV_SHA256 = 0x10
TLV_ECDSASIG = 0x22
TLV_CUSTOM_MIN = 0x00A0
# Header flags of features the bootloader is not built with: encrypted, non-bootable, RAM load,
# compressed.
FLAGS_NOT_TARGET = 0x04 | 0x08 | 0x10 | 0x20 | 0x200 | 0x400 | 0x800

# Result code names of the bootloader validation (the MBOOT_RES_ERR_* codes of
# shared/mboot/include/mboot_types.h without the prefix).
RES_OK = "OK"
RES_ERR_HEADER = "ERR_HEADER"
RES_ERR_HASH = "ERR_HASH"
RES_ERR_SIG = "ERR_SIG"
RES_ERR_DOWNGRADE = "ERR_DOWNGRADE"
RES_ERR_TOO_BIG = "ERR_TOO_BIG"
RES_ERR_NOT_TARGET = "ERR_NOT_TARGET"
RES_ERR_LAYOUT = "ERR_LAYOUT"


class ImageError(SignError):
    """An image defect, code is the result code name (the RES_* constants above)."""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


# ---------------------------------------------------------------------------
# Image parsing and the host side check order of the bootloader validation

TLV = namedtuple("TLV", ["tag", "off", "value"])


class ParsedImage:
    def __init__(self):
        self.hdr_size = self.ptlv_size = self.img_size = self.flags = 0
        self.version = (0, 0, 0, 0)
        self.prot_off = self.tlv_off = self.tlv_total = 0
        self.prot = []
        self.tlv = []

    @property
    def body_end(self):
        return self.hdr_size + self.img_size

    @property
    def total(self):
        """Header, image, protected and unprotected TLV area as declared by the header."""
        return self.tlv_off + self.tlv_total

    def tlv_value(self, tag, protected=False):
        for e in self.prot if protected else self.tlv:
            if e.tag == tag:
                return e.value
        return None

    def counter(self):
        v = self.tlv_value(TLV_SEC_CNT, True)
        return None if v is None else struct.unpack("<I", v)[0]


def _parse_tlvs(data, off, end):
    entries = []
    while off + 4 <= end:
        tag, length = struct.unpack_from("<HH", data, off)
        if off + 4 + length > end:
            break
        entries.append(TLV(tag, off, bytes(data[off + 4 : off + 4 + length])))
        off += 4 + length
    return entries


def parse_image(data):
    """Parse header and TLV areas. Raises ImageError(ERR_HEADER) for a damaged structure; a TLV
    area cut short is parsed as far as it goes (the rest reads as erased flash)."""
    if len(data) < HEADER_SIZE_MIN:
        raise ImageError(RES_ERR_HEADER, "shorter than the image header")
    img = ParsedImage()
    (magic, _load, hdr, ptlv, size, flags, major, minor, rev, build, _pad) = struct.unpack_from(
        HEADER_FMT, data
    )
    if magic != IMAGE_MAGIC:
        raise ImageError(RES_ERR_HEADER, "bad image magic 0x%08x" % magic)
    img.hdr_size, img.ptlv_size, img.img_size, img.flags = hdr, ptlv, size, flags
    img.version = (major, minor, rev, build)
    if hdr < HEADER_SIZE_MIN or img.body_end + ptlv > len(data):
        raise ImageError(RES_ERR_HEADER, "header and protected TLV area exceed the image")
    img.prot_off = img.body_end
    if ptlv:
        magic, total = struct.unpack_from("<HH", data, img.prot_off)
        if magic != TLV_PROT_INFO_MAGIC or total != ptlv:
            raise ImageError(RES_ERR_HEADER, "bad protected TLV info")
        img.prot = _parse_tlvs(data, img.prot_off + 4, img.prot_off + ptlv)
    img.tlv_off = img.prot_off + ptlv
    if img.tlv_off + 4 > len(data):
        raise ImageError(RES_ERR_HEADER, "TLV area missing")
    magic, img.tlv_total = struct.unpack_from("<HH", data, img.tlv_off)
    if magic != TLV_INFO_MAGIC:
        raise ImageError(RES_ERR_HEADER, "bad TLV info magic")
    img.tlv = _parse_tlvs(data, img.tlv_off + 4, min(img.tlv_off + img.tlv_total, len(data)))
    return img


def image_regions(img):
    """(start, end, name) of the parts of an image, for diff reporting."""
    regions = [(0, HEADER_SIZE_MIN, "header"), (HEADER_SIZE_MIN, img.hdr_size, "header padding")]
    regions.append((img.hdr_size, img.body_end, "body"))
    if img.ptlv_size:
        regions.append((img.prot_off, img.prot_off + 4, "protected TLV info"))
        for e in img.prot:
            regions.append((e.off, e.off + 4 + len(e.value), "protected TLV 0x%02x" % e.tag))
    regions.append((img.tlv_off, img.tlv_off + 4, "TLV info"))
    names = {TLV_SHA256: "SHA256", TLV_KEYHASH: "KEYHASH", TLV_ECDSASIG: "ECDSASIG"}
    for e in img.tlv:
        name = "TLV " + names.get(e.tag, "0x%02x" % e.tag)
        regions.append((e.off, e.off + 4 + len(e.value), name))
    return regions


def classify(
    data,
    pub,
    layout_id=None,
    header_size=None,
    max_image_size=None,
    base=None,
    rollback=None,
):
    """Return (result code name, message) for an image, following the order of the bootloader
    validation (shared/mboot/src/validate.c): header and target checks, size, layout id, hash,
    signature, then the downgrade check.

    pub is a cryptography public key. base is (version tuple, counter) of the installed image and
    rollback "version" or "counter" selects the comparison, both are optional."""
    try:
        img = parse_image(data)
    except ImageError as exc:
        return exc.code, str(exc)
    if header_size is not None and img.hdr_size != header_size:
        return RES_ERR_HEADER, "header size 0x%x, expected 0x%x" % (img.hdr_size, header_size)
    if img.flags & FLAGS_NOT_TARGET:
        return RES_ERR_NOT_TARGET, "header flags 0x%x" % img.flags
    if max_image_size is not None and img.total > max_image_size:
        return RES_ERR_TOO_BIG, "image 0x%x bytes, limit 0x%x" % (img.total, max_image_size)
    if layout_id is not None:
        value = img.tlv_value(TLV_LAYOUT_ID, True)
        if value is None:
            return RES_ERR_LAYOUT, "no layout id TLV"
        if value != layout_id.to_bytes(4, "big"):
            return RES_ERR_LAYOUT, "layout id %s, expected %08x" % (value.hex(), layout_id)
    digest = hashlib.sha256(data[: img.tlv_off]).digest()
    if img.tlv_value(TLV_SHA256) != digest:
        return RES_ERR_HASH, "SHA256 TLV does not match the image"
    if img.tlv_value(TLV_KEYHASH) != hashlib.sha256(public_key_spki(pub)).digest():
        return RES_ERR_SIG, "key hash TLV missing or not the key"
    sig = img.tlv_value(TLV_ECDSASIG)
    if sig is None:
        return RES_ERR_SIG, "no signature TLV"
    try:
        pub.verify(sig, digest, ec.ECDSA(Prehashed(hashes.SHA256())))
    except InvalidSignature:
        return RES_ERR_SIG, "signature does not verify"
    if base is not None and rollback == "version" and img.version[:3] < base[0][:3]:
        return RES_ERR_DOWNGRADE, "version %d.%d.%d below %d.%d.%d" % (
            img.version[:3] + base[0][:3]
        )
    if base is not None and rollback == "counter":
        counter = img.counter() or 0
        if counter < base[1]:
            return RES_ERR_DOWNGRADE, "security counter %d below %d" % (counter, base[1])
    return RES_OK, "valid"


# ---------------------------------------------------------------------------
# Re-signing and the negative test images

VARIANT_SPECS = [
    # name, what differs from the good image, acceptable result codes, rollback policy
    # the expectation holds under ("any" for all)
    ("good", "the signed image, unchanged", [RES_OK], "any"),
    (
        "bad_sig",
        "one bit flipped in the last byte of the ECDSA signature TLV",
        [RES_ERR_SIG],
        "any",
    ),
    ("bad_hash", "one bit flipped in the middle of the image body", [RES_ERR_HASH], "any"),
    (
        "wrong_key",
        "signed by the other test key, key hash TLV of that key",
        [RES_ERR_SIG],
        "any",
    ),
    (
        "truncated_half",
        "first half of the file",
        [RES_ERR_HEADER, RES_ERR_SIG],
        "any",
    ),
    (
        "truncated_tlv",
        "cut in the middle of the unprotected TLV area, signature TLV missing",
        [RES_ERR_HEADER, RES_ERR_SIG],
        "any",
    ),
    (
        "oversize",
        "valid signature, body extended so the image is larger than both slots",
        [RES_ERR_TOO_BIG],
        "any",
    ),
    (
        "old_version",
        "re-signed with a lower version and (when above 0) a lower security counter",
        [RES_ERR_DOWNGRADE],
        "version",
    ),
    (
        "old_counter",
        "re-signed with a lower security counter and a higher version",
        [RES_ERR_DOWNGRADE],
        "counter",
    ),
    (
        "wrong_layout",
        "re-signed with the layout id TLV 0x00A1 inverted",
        [RES_ERR_LAYOUT],
        "any",
    ),
    ("no_layout", "re-signed without the layout id TLV", [RES_ERR_LAYOUT], "any"),
    ("bad_header_magic", "one bit flipped in the header magic", [RES_ERR_HEADER], "any"),
]
VARIANT_MODES = [spec[0] for spec in VARIANT_SPECS]

_KEEP = object()


def oversize_length(primary_size, secondary_size):
    """Length of the oversize image: one byte over the larger slot, rounded up to 1 KiB. The
    signature is made anew and its DER length varies by a byte or two, so the file length varies
    by the same amount."""
    limit = max(primary_size, secondary_size or 0)
    return (limit + 1 + 1023) // 1024 * 1024


def lower_version(version):
    major, minor, rev = version[:3]
    if rev:
        return (major, minor, rev - 1)
    if minor:
        return (major, minor - 1, 0)
    if major:
        return (major - 1, 0, 0)
    raise SignError("image version is 0.0.0, cannot derive a lower version")


def _filler(n):
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.sha256(b"mcuboot-oversize" + struct.pack("<I", i)).digest()
        i += 1
    return bytes(out[:n])


def resign(good, key, version=_KEEP, counter=_KEEP, layout_tlv=_KEEP, extra_body=0):
    """Rebuild the image in good from its header padding and body with the given key. Version,
    security counter (None removes the TLV), layout id TLV value (None removes it) and body
    length can be overridden, everything else is taken from the image."""
    img = parse_image(good)
    if img.flags:
        raise SignError("cannot re-sign an image with header flags 0x%x" % img.flags)
    custom = {}
    for e in img.prot:
        if e.tag == TLV_SEC_CNT:
            continue
        if e.tag < TLV_CUSTOM_MIN:
            raise SignError("cannot re-sign an image with protected TLV 0x%02x" % e.tag)
        custom[e.tag] = e.value
    if layout_tlv is not _KEEP:
        custom.pop(TLV_LAYOUT_ID, None)
        if layout_tlv is not None:
            custom[TLV_LAYOUT_ID] = layout_tlv
    if version is _KEEP:
        version = img.version
    if counter is _KEEP:
        counter = img.counter()
    body = bytes(good[img.hdr_size : img.body_end]) + _filler(extra_body)
    new = imgimage.Image(
        version=imgversion.SemiSemVersion(*version),
        header_size=img.hdr_size,
        pad_header=True,
        slot_size=0,
        security_counter=counter,
    )
    new.payload = bytearray(good[: img.hdr_size]) + body
    new.image_size = len(body)
    with contextlib.redirect_stdout(io.StringIO()):
        new.create(imgkeys.load(str(key)), "hash", None, custom_tlvs=custom)
    return bytes(new.payload)


def _flip(data, offset, mask=0x01):
    out = bytearray(data)
    out[offset] ^= mask
    return bytes(out)


def make_variant(mode, good, key, other_key=DEFAULT_OTHER_KEY, oversize_len=None):
    """Return the negative test image `mode` (see VARIANT_SPECS) derived from the signed image
    good. key is the private key that signed good, used for the variants that need a valid
    signature."""
    img = parse_image(good)
    if mode == "good":
        return bytes(good)
    if mode == "bad_sig":
        sig = [e for e in img.tlv if e.tag == TLV_ECDSASIG]
        if not sig:
            raise SignError("image has no ECDSA signature TLV")
        return _flip(good, sig[0].off + 4 + len(sig[0].value) - 1)
    if mode == "bad_hash":
        if not img.img_size:
            raise SignError("image body is empty")
        return _flip(good, img.hdr_size + img.img_size // 2)
    if mode == "wrong_key":
        return resign(good, other_key)
    if mode == "truncated_half":
        return bytes(good[: len(good) // 2])
    if mode == "truncated_tlv":
        return bytes(good[: img.tlv_off + 4 + (img.tlv_total - 4) // 2])
    if mode == "oversize":
        if oversize_len is None or oversize_len <= len(good):
            raise SignError("oversize needs a target length above the image length")
        return resign(good, key, extra_body=oversize_len - len(good))
    if mode in ("old_version", "old_counter", "wrong_layout", "no_layout"):
        return _make_resigned_variant(mode, good, img, key)
    if mode == "bad_header_magic":
        return _flip(good, 0)
    raise SignError("unknown variant %r (known: %s)" % (mode, ", ".join(VARIANT_MODES)))


def _make_resigned_variant(mode, good, img, key):
    counter = img.counter()
    if mode == "old_version":
        new_counter = counter - 1 if counter else counter
        return resign(good, key, version=lower_version(img.version) + (0,), counter=new_counter)
    if mode == "old_counter":
        if not counter:
            raise SignError("old_counter needs an image with a security counter above 0")
        version = img.version[:2] + (img.version[2] + 1, img.version[3])
        return resign(good, key, version=version, counter=counter - 1)
    layout = img.tlv_value(TLV_LAYOUT_ID, True)
    if layout is None:
        raise SignError("image has no layout id TLV 0x%04x to change" % TLV_LAYOUT_ID)
    if mode == "wrong_layout":
        return resign(good, key, layout_tlv=bytes(b ^ 0xFF for b in layout))
    return resign(good, key, layout_tlv=None)
