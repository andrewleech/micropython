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
Write the data of the fsload host tests (test_fsload.c): signed images, filesystem and gzip
containers holding them, the request element streams and the manifest cases.txt.

  make_testdata.py <outdir>

The files that must be installed (case names install_*) and the files that must be refused
without a flash write (case names refused_*) are listed in build() below. <outdir>/cases.txt has
one line per case:

  <name> <expected> <device file> <element file> <image file or ->

and <outdir>/pubkey.c is the imgtool getpub output of the signing key.
"""

import os
import random
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOP = HERE.parents[2]
sys.path.insert(0, str(HERE))

import containers  # noqa: E402

IMGTOOL = [sys.executable, str(TOP / "lib/mcuboot/scripts/imgtool.py")]
KEY = TOP / "lib/mcuboot/root-ec-p256.pem"
OTHER_KEY = TOP / "tests/mcuboot/keys/other-ecdsa-p256.pem"

FS_BASE = 0x90000000
HEADER_SIZE = 0x400
SLOT_SIZE = 0x20000
# Largest image of the harness slots: 32 sectors of 4 KiB minus the trailer sectors (two for
# swap using offset, one for the single slot policy).
CAPACITY = 0x1E000
CAPACITY_SINGLE = 0x1F000

E_OK = "ok"
E_HEADER = 2
E_HASH = 3
E_SIG = 4
E_TOO_BIG = 6
E_REQUEST = 11
E_FS_MOUNT = 16
E_FS_OPEN = 17
E_FS_READ = 18
E_FS_GZIP = 19
E_FS_PATH = 20

END, MOUNT, FSLOAD, STATUS = 1, 2, 3, 4
FAT, LFS1, LFS2, RAW = 1, 2, 3, 4


def tlv(kind, payload=b""):
    return bytes([kind, len(payload)]) + payload


def elements(
    fs_type, base, length, path, arg2=None, arg3=None, mount_point=0, status=None, end=True
):
    mount = bytes([mount_point, fs_type]) + struct.pack("<II", base, length)
    if arg2 is not None:
        mount += struct.pack("<I", arg2)
        if arg3 is not None:
            mount += struct.pack("<I", arg3)
    data = tlv(MOUNT, mount) + tlv(FSLOAD, bytes([mount_point]) + path)
    if status is not None:
        data += tlv(STATUS, struct.pack("<I", status))
    if end:
        data += tlv(END)
    return data


def imgtool_sign(payload, key, version="1.2.3", slot_size=SLOT_SIZE):
    work = Path(os.environ.get("TMPDIR", "/tmp")) / "f1_testdata_work"
    work.mkdir(exist_ok=True)
    src = work / "payload.bin"
    out = work / "signed.bin"
    src.write_bytes(payload)
    subprocess.run(
        IMGTOOL
        + ["sign", "--key", str(key), "-v", version, "-H", hex(HEADER_SIZE), "--pad-header"]
        # A security counter and a layout id TLV make a protected TLV area, as in the
        # images of a real build.
        + ["-s", "2", "--custom-tlv", "0x00A1", "0x1234abcd"]
        + ["--align", "16", "--max-align", "16", "-S", hex(slot_size), str(src), str(out)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return out.read_bytes()


def payload_bytes(n, seed):
    rng = random.Random(seed)
    # A vector table at the start, then text-like and random data so that deflate has work.
    words = struct.pack("<II", 0x20010000, 0x08020401) + bytes(
        rng.randrange(256) for _ in range(56)
    )
    body = bytearray()
    while len(body) < n:
        body += bytes(rng.randrange(256) for _ in range(rng.choice([8, 64, 512])))
        body += b"MicroPython MCUboot fsload test data " * rng.choice([1, 4, 16])
    return words + bytes(body[: n - len(words)])


def tlv_area(img):
    """(offset of the TLV info, end offset) of the unprotected TLV area of a signed image."""
    hdr_size, prot, img_size = struct.unpack_from("<HHI", img, 8)
    off = hdr_size + img_size + prot
    magic, tot = struct.unpack_from("<HH", img, off)
    assert magic == 0x6907
    return off, off + tot


def flip_bit(data, offset, bit=0):
    out = bytearray(data)
    out[offset] ^= 1 << bit
    return bytes(out)


def find_tlv(img, kind):
    start, end = tlv_area(img)
    off = start + 4
    while off + 4 <= end:
        t, n = struct.unpack_from("<HH", img, off)
        if t == kind:
            return off + 4, n
        off += 4 + n
    raise KeyError(kind)


class Suite:
    def __init__(self, out):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.lines = []

    def case(self, name, expected, device, elems, image=None):
        (self.out / f"{name}.dev").write_bytes(device)
        (self.out / f"{name}.elems").write_bytes(elems)
        image_name = "-"
        if image is not None:
            image_name = f"{name}.img"
            (self.out / image_name).write_bytes(image)
        exp = (
            ",".join(str(e) for e in expected)
            if isinstance(expected, (list, tuple))
            else str(expected)
        )
        self.lines.append(f"{name} {exp} {name}.dev {name}.elems {image_name}")

    def finish(self):
        (self.out / "cases.txt").write_text("\n".join(self.lines) + "\n")


def fat_middle_loop(img, path):
    """The FAT image with a cluster in the middle of the file's chain linked back to its first."""
    fat = containers._Fat(img)
    first = fat.find(path)[0]
    chain = fat.chain(first)
    buf = bytearray(img)
    fat.set(buf, chain[len(chain) // 2], chain[0])
    return bytes(buf)


def raw_device(window, offset=0x1000):
    return b"\xff" * offset + window, FS_BASE + offset


def build(out):
    s = Suite(out)

    good = imgtool_sign(payload_bytes(20000, 1), KEY, "2.0.0")
    initial = imgtool_sign(payload_bytes(9000, 2), KEY, "1.0.0")
    (s.out / "initial.bin").write_bytes(initial)
    pub = subprocess.run(
        IMGTOOL + ["getpub", "-k", str(KEY)], check=True, capture_output=True, text=True
    ).stdout
    (s.out / "pubkey.c").write_text(pub)

    bad_sig = flip_bit(good, find_tlv(good, 0x22)[0] + 10)
    bad_hash = flip_bit(good, HEADER_SIZE + 100)
    wrong_key = imgtool_sign(payload_bytes(20000, 1), OTHER_KEY, "2.0.0")
    # The signature of a key that is not in the key table fails at the key lookup.
    start, end = tlv_area(good)
    truncated_half = good[: len(good) // 2]
    truncated_tlv = good[: end - 10]
    oversize = imgtool_sign(payload_bytes(CAPACITY + 8192, 3), KEY, "2.0.0", slot_size=0x40000)
    not_image = bytes(random.Random(5).randrange(256) for _ in range(4000))
    assert len(oversize) > CAPACITY

    def fat_case(name, expected, files, path, image=None, size=4 << 20, fat=16, **kw):
        dev = containers.make_fat(files, size=size, fat=fat)
        s.case(name, expected, dev, elements(FAT, FS_BASE, len(dev), path), image)

    def lfs_case(
        name, expected, files, path, image=None, size=1 << 20, block_size=512, arg2=None, **kw
    ):
        dev = containers.make_lfs2(files, size=size, block_size=block_size)
        s.case(
            name,
            expected,
            dev,
            elements(LFS2, FS_BASE, len(dev), path, block_size if arg2 is None else arg2),
            image,
        )

    def raw_case(name, expected, window, image=None, pad=0, window_len=None):
        dev, base = raw_device(window + b"\xff" * pad)
        s.case(
            name,
            expected,
            dev,
            elements(RAW, base, len(window) + pad if window_len is None else window_len, b"x"),
            image,
        )

    gz_good = containers.make_gzip(good)

    # ---- files that must be installed ----
    fat_case("install_fat16", E_OK, {"/update.bin": good}, b"/update.bin", good)
    fat_case("install_fat16_case", E_OK, {"/update.bin": good}, b"/UPDATE.BIN", good)
    fat_case(
        "install_fat16_lfn", E_OK, {"/firmware.signed.bin": good}, b"/firmware.signed.bin", good
    )
    fat_case("install_fat16_nested", E_OK, {"/a/b/c/update.bin": good}, b"/a/b/c/update.bin", good)
    fat_case(
        "install_fat12", E_OK, {"/update.bin": good}, b"/update.bin", good, size=1 << 20, fat=12
    )
    fat_case(
        "install_fat16_large_volume",
        E_OK,
        {"/update.bin": good},
        b"/update.bin",
        good,
        size=7 << 20,
    )
    lfs_case("install_lfs2_512", E_OK, {"/update.bin": good}, b"/update.bin", good, block_size=512)
    lfs_case("install_lfs2_128", E_OK, {"/update.bin": good}, b"/update.bin", good, block_size=128)
    lfs_case(
        "install_lfs2_4096_default",
        E_OK,
        {"/update.bin": good},
        b"/update.bin",
        good,
        block_size=4096,
        arg2=0,
    )
    lfs_case("install_lfs2_nested", E_OK, {"/d1/d2/update.bin": good}, b"/d1/d2/update.bin", good)
    raw_case("install_raw", E_OK, good, good)
    raw_case("install_raw_padded", E_OK, good, good, pad=100000 - len(good))
    seg_dev = b"\xff" * 0x1000 + good[:7777] + b"\xff" * 0x800 + good[7777:]
    s.case(
        "install_raw_two_segments",
        E_OK,
        seg_dev,
        elements(
            RAW, FS_BASE + 0x1000, 7777, b"x", FS_BASE + 0x1000 + 7777 + 0x800, len(good) - 7777
        ),
        good,
    )
    raw_case("install_gzip_raw", E_OK, gz_good, good)
    raw_case("install_gzip_raw_padded", E_OK, gz_good, good, pad=5000)
    fat_case("install_gzip_fat16", E_OK, {"/update.bin.gz": gz_good}, b"/update.bin.gz", good)
    lfs_case("install_gzip_lfs2", E_OK, {"/update.bin.gz": gz_good}, b"/update.bin.gz", good)
    gz_stored = containers.make_gzip(good, level=0)
    raw_case("install_gzip_stored_blocks", E_OK, gz_stored, good)
    gz_fields = containers.make_gzip(
        good, fextra=b"abc", fname=b"update.bin", fcomment=b"c", fhcrc=True
    )
    raw_case("install_gzip_header_fields", E_OK, gz_fields, good)

    # ---- files and requests that must be refused without a flash write ----
    fat_case("refused_not_an_image", E_HEADER, {"/update.bin": not_image}, b"/update.bin")
    fat_case("refused_bad_sig", E_SIG, {"/update.bin": bad_sig}, b"/update.bin")
    fat_case("refused_bad_hash", E_HASH, {"/update.bin": bad_hash}, b"/update.bin")
    fat_case("refused_wrong_key", E_SIG, {"/update.bin": wrong_key}, b"/update.bin")
    fat_case("refused_truncated_half", E_HEADER, {"/update.bin": truncated_half}, b"/update.bin")
    fat_case("refused_truncated_tlv", E_HEADER, {"/update.bin": truncated_tlv}, b"/update.bin")
    fat_case("refused_oversize", E_TOO_BIG, {"/update.bin": oversize}, b"/update.bin")
    fat_case("refused_missing_file", E_FS_OPEN, {"/other.bin": good}, b"/update.bin")
    fat_case("refused_is_directory", E_FS_OPEN, {"/dir/update.bin": good}, b"/dir")
    fat_case("refused_dotdot", E_FS_PATH, {"/dir/update.bin": good}, b"/dir/../dir/update.bin")
    fat_case("refused_dot", E_FS_PATH, {"/update.bin": good}, b"/./update.bin")
    fat_case("refused_nul_in_path", E_FS_PATH, {"/update.bin": good}, b"/upd\x00ate.bin")
    fat_case("refused_control_char", E_FS_PATH, {"/update.bin": good}, b"/upd\x07ate.bin")
    fat_case("refused_non_ascii", E_FS_PATH, {"/update.bin": good}, b"/upd\xe9ate.bin")
    fat_case("refused_path_254", E_FS_OPEN, {"/update.bin": good}, b"/" + b"a" * 253)
    dev = containers.make_fat({"/update.bin": good}, size=4 << 20)
    s.case(
        "refused_garbage_volume",
        E_FS_MOUNT,
        bytes(random.Random(7).randrange(256) for _ in range(65536)),
        elements(FAT, FS_BASE, 65536, b"/update.bin"),
    )
    s.case("refused_empty_device", E_FS_MOUNT, b"", elements(FAT, FS_BASE, 65536, b"/update.bin"))
    # A loop after the last cluster the file size reaches is never followed. With a size field
    # larger than the chain the loop is followed and the image is followed by repeated data;
    # only the length of the image is installed.
    s.case(
        "install_fat16_loop_after_end",
        E_OK,
        containers.fat_cluster_loop(dev, "/update.bin"),
        elements(FAT, FS_BASE, len(dev), b"/update.bin"),
        good,
    )
    s.case(
        "install_fat16_loop_in_trailing_data",
        E_OK,
        containers.fat_cluster_loop(dev, "/update.bin", size=2 * len(good)),
        elements(FAT, FS_BASE, len(dev), b"/update.bin"),
        good,
    )
    s.case(
        "refused_fat_chain_loop",
        [E_HEADER, E_HASH],
        fat_middle_loop(dev, "/update.bin"),
        elements(FAT, FS_BASE, len(dev), b"/update.bin"),
    )
    dirdev = containers.make_fat({"/dir/update.bin": good}, size=4 << 20)
    looped = containers.fat_dir_loop(dirdev, "/dir")
    s.case(
        "install_fat16_dir_loop_name_found",
        E_OK,
        looped,
        elements(FAT, FS_BASE, len(dirdev), b"/dir/update.bin"),
        good,
    )
    s.case(
        "refused_fat_dir_loop",
        E_FS_OPEN,
        looped,
        elements(FAT, FS_BASE, len(dirdev), b"/dir/missing.bin"),
    )
    many = {"/dir/f%04d.bin" % i: b"x" for i in range(200)}
    manydev = containers.make_fat(many, size=4 << 20)
    s.case(
        "refused_fat_dir_many_entries_missing",
        E_FS_OPEN,
        manydev,
        elements(FAT, FS_BASE, len(manydev), b"/dir/nothing.bin"),
    )

    lfs_dev = containers.make_lfs2({"/update.bin": good}, size=1 << 20, block_size=512)
    s.case(
        "refused_lfs2_missing",
        E_FS_OPEN,
        lfs_dev,
        elements(LFS2, FS_BASE, len(lfs_dev), b"/nothing.bin", 512),
    )
    s.case(
        "refused_lfs2_wrong_block_size",
        E_FS_MOUNT,
        lfs_dev,
        elements(LFS2, FS_BASE, len(lfs_dev), b"/update.bin", 1024),
    )
    s.case(
        "refused_lfs2_block_size_not_multiple",
        E_FS_MOUNT,
        lfs_dev,
        elements(LFS2, FS_BASE, len(lfs_dev), b"/update.bin", 100),
    )
    s.case(
        "refused_lfs2_dotdot",
        E_FS_PATH,
        lfs_dev,
        elements(LFS2, FS_BASE, len(lfs_dev), b"/a/../update.bin", 512),
    )
    for seed in range(4):
        corrupt = containers.lfs2_corrupt_metadata(lfs_dev, 512, seed)
        s.case(
            f"refused_lfs2_corrupt_metadata_{seed}",
            [E_FS_MOUNT, E_FS_OPEN, E_FS_READ, E_HEADER, E_HASH, E_SIG],
            corrupt,
            elements(LFS2, FS_BASE, len(corrupt), b"/update.bin", 512),
        )
    for field in ("block_size", "block_count", "name_max", "file_max"):
        bad = containers.lfs2_bad_superblock(lfs_dev, field)
        s.case(
            f"refused_lfs2_bad_superblock_{field}",
            E_FS_MOUNT,
            bad,
            elements(LFS2, FS_BASE, len(bad), b"/update.bin", 512),
        )
    s.case(
        "refused_lfs1_not_offered",
        E_FS_MOUNT,
        lfs_dev,
        elements(LFS1, FS_BASE, len(lfs_dev), b"/update.bin", 512),
    )

    raw_case("refused_raw_empty", E_HEADER, b"", pad=65536)
    raw_case("refused_raw_too_big", E_TOO_BIG, good, pad=CAPACITY_SINGLE + 4096 - len(good))
    for kind in ("bad_crc", "bad_isize", "truncated_trailer", "truncated_body", "reserved_flag"):
        raw_case(f"refused_gzip_{kind}", E_FS_GZIP, containers.corrupt_gzip(gz_good, kind))
    raw_case("refused_gzip_bad_method", E_HEADER, containers.corrupt_gzip(gz_good, "bad_method"))
    # A bomb of zeros is not an image and is refused at its header. An image header followed by
    # a long run of zeros is read until the output limit.
    raw_case("refused_gzip_bomb_zeros", E_HEADER, containers.gzip_bomb(16 << 20))
    bomb = containers.make_gzip(good + bytes(16 << 20))
    raw_case("refused_gzip_bomb", E_FS_GZIP, bomb)
    raw_case("refused_gzip_of_not_an_image", E_HEADER, containers.make_gzip(not_image))
    raw_case("refused_gzip_bad_sig", E_SIG, containers.make_gzip(bad_sig))
    raw_case("refused_gzip_oversize", E_TOO_BIG, containers.make_gzip(oversize))
    fat_case("refused_gzip_bomb_in_fat", E_FS_GZIP, {"/update.bin.gz": bomb}, b"/update.bin.gz")

    # Requests.
    dev, base = raw_device(good)
    ok_elems = elements(RAW, base, len(good), b"x")
    s.case("refused_req_no_end", E_REQUEST, dev, elements(RAW, base, len(good), b"x", end=False))
    s.case(
        "refused_req_no_fsload",
        E_REQUEST,
        dev,
        tlv(MOUNT, bytes([0, RAW]) + struct.pack("<II", base, len(good))) + tlv(END),
    )
    s.case("refused_req_no_mount", E_REQUEST, dev, tlv(FSLOAD, bytes([0]) + b"x") + tlv(END))
    s.case(
        "refused_req_wrong_mount_point",
        E_REQUEST,
        dev,
        tlv(MOUNT, bytes([1, RAW]) + struct.pack("<II", base, len(good)))
        + tlv(FSLOAD, bytes([0]) + b"x")
        + tlv(END),
    )
    s.case(
        "refused_req_mount_len_9",
        E_REQUEST,
        dev,
        tlv(MOUNT, bytes([0, RAW]) + struct.pack("<II", base, len(good))[:7])
        + tlv(FSLOAD, bytes([0]) + b"x")
        + tlv(END),
    )
    s.case(
        "refused_req_mount_len_22",
        E_REQUEST,
        dev,
        tlv(MOUNT, bytes([0, RAW]) + struct.pack("<IIIII", base, len(good), 0, 0, 0)[:20])
        + tlv(FSLOAD, bytes([0]) + b"x")
        + tlv(END),
    )
    s.case(
        "refused_req_unknown_fs_type",
        E_REQUEST,
        dev,
        tlv(MOUNT, bytes([0, 9]) + struct.pack("<II", base, len(good)))
        + tlv(FSLOAD, bytes([0]) + b"x")
        + tlv(END),
    )
    s.case(
        "refused_req_fs_type_0",
        E_REQUEST,
        dev,
        tlv(MOUNT, bytes([0, 0]) + struct.pack("<II", base, len(good)))
        + tlv(FSLOAD, bytes([0]) + b"x")
        + tlv(END),
    )
    s.case(
        "refused_req_outside_device",
        E_REQUEST,
        dev,
        elements(RAW, FS_BASE + (8 << 20) - 100, len(good), b"x"),
    )
    s.case("refused_req_below_device", E_REQUEST, dev, elements(RAW, FS_BASE - 100, 200, b"x"))
    s.case("refused_req_wraps", E_REQUEST, dev, elements(RAW, 0xFFFFF000, 0x2000, b"x"))
    s.case("refused_req_zero_len", E_REQUEST, dev, elements(RAW, base, 0, b"x"))
    s.case(
        "refused_req_unmapped_address", E_REQUEST, dev, elements(RAW, 0x20000000, len(good), b"x")
    )
    s.case(
        "refused_req_second_segment_outside",
        E_REQUEST,
        dev,
        elements(RAW, base, 100, b"x", 0x20000000, 100),
    )
    s.case(
        "refused_req_status_len_3",
        E_REQUEST,
        dev,
        ok_elems[:-2] + tlv(STATUS, b"\x00\x10\x00") + tlv(END),
    )
    s.case(
        "refused_req_empty_path",
        E_REQUEST,
        dev,
        tlv(MOUNT, bytes([0, RAW]) + struct.pack("<II", base, len(good)))
        + tlv(FSLOAD, bytes([0]))
        + tlv(END),
    )
    s.case("refused_req_empty_stream", E_REQUEST, dev, b"")
    s.case("refused_req_end_with_length", E_REQUEST, dev, bytes([END, 1, 0]))

    # STATUS element.
    s.case(
        "install_status", E_OK, dev, elements(RAW, base, len(good), b"x", status=0x20001000), good
    )
    s.case(
        "refused_status_failure", E_HEADER, dev, elements(RAW, base, 100, b"x", status=0x20001000)
    )
    s.case(
        "refused_status_in_flash_device",
        E_HEADER,
        dev,
        elements(RAW, base, 100, b"x", status=FS_BASE + 0x100),
    )
    # The address must be a word inside the declared RAM window (0x20000000..0x20010000 in
    # config/mcuboot_layout.h): the load succeeds and nothing is stored.
    for name, addr in (
        ("below_window", 0x1FFFFFFC),
        ("window_end", 0x20010000),
        ("unaligned", 0x20001002),
        ("zero", 0),
        ("peripheral", 0x40000000),
        ("slot_device", 0x08010000),
    ):
        s.case(
            "refused_status_" + name,
            E_OK,
            dev,
            elements(RAW, base, len(good), b"x", status=addr),
            good,
        )

    # The source window may not cover any area of the flash map except the filesystem area:
    # the target slots, the log and the intent record are refused before anything is read.
    slot = 0x08000000
    for name, addr, length, expected in (
        ("primary", slot + 0x10000, 0x1000, E_REQUEST),
        ("primary_tail", slot + 0x2F000, 0x2000, E_REQUEST),
        ("secondary", slot + 0x30000, 0x1000, [E_REQUEST, E_HEADER]),
        ("log", slot + 0x51000, 0x100, E_REQUEST),
        ("intent", slot + 0x53000, 0x100, [E_REQUEST, E_HEADER]),
        ("before_primary_into_it", slot + 0x0F000, 0x2000, E_REQUEST),
    ):
        s.case("refused_req_window_" + name, expected, dev, elements(RAW, addr, length, b"x"))
    s.case(
        "refused_req_second_segment_in_primary",
        E_REQUEST,
        dev,
        elements(RAW, base, 100, b"x", slot + 0x10000, 0x1000),
    )
    s.case(
        "refused_req_window_unused_flash",
        E_HEADER,
        dev,
        elements(RAW, slot + 0x60000, 0x1000, b"x"),
    )

    s.finish()
    return len(s.lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(f"{build(sys.argv[1])} cases in {sys.argv[1]}")
