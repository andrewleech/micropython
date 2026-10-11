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

"""Write the seed corpora of the fsload fuzz targets.

    gen_corpus.py --out DIR [--image SIGNED.bin]

One directory per fuzz target (fat, lfs, gz, stream, run) is created under DIR. The input
layouts are fixed by the harness. All integers are little endian:

    fat     [u8 sel][u8 pad][FAT image]
    lfs     [u8 sel][u8 bs][littlefs2 image]
    gz      [u8 limit_log2][gzip bytes]
    stream  [u8 mode][u16 data_len][data][plan of (u32 offset, u16 length) records]
    run     [u8 flags][u16 elems_len][element stream][device payload]

The image is a signed MCUboot image. Without --image one is made by imgtool from a fixed
pseudo random payload, with deterministic ECDSA so the corpus is the same on every run.
"""

import argparse
import contextlib
import io
import os
import pathlib
import random
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import containers as c  # noqa: E402

TOP = pathlib.Path(__file__).resolve().parents[3]

# Paths selected by the low three bits of sel in the fat and lfs inputs.
PATHS = [
    "/update.bin",
    "update.bin",
    "/UPDATE.BIN",
    "/dir/update.bin",
    "/a/b/c/update.bin",
    "/firmware.signed.bin",
    "/very long file name for the update image.bin",
    "/",
]

# Block sizes selected by the bs byte of the lfs input.
LFS_BLOCK_SIZES = [4096, 128, 256, 512, 1024, 2048, 4096, 8192]

DEVICE_BASE = 0x90000000
DEVICE_SIZE = 8 << 20
STATUS_ADDR = 0x20000800

ELEM_END, ELEM_MOUNT, ELEM_FSLOAD, ELEM_STATUS = 1, 2, 3, 4
FS_FAT, FS_LFS1, FS_LFS2, FS_RAW = 1, 2, 3, 4


def make_image(path):
    """Return the signed image: the file at path, or one made by imgtool from a fixed payload."""
    if path is not None:
        with open(path, "rb") as f:
            return f.read()
    sys.path.insert(0, str(TOP / "lib/mcuboot/scripts"))
    import tempfile

    from cryptography.hazmat.primitives.asymmetric import ec
    import imgtool.keys.ecdsa as imgtool_ecdsa
    from imgtool import main as imgtool_main

    class DeterministicEc:
        """The ec module with ECDSA signatures that depend only on key and message."""

        def __getattr__(self, name):
            return getattr(ec, name)

        @staticmethod
        def ECDSA(alg):
            return ec.ECDSA(alg, deterministic_signing=True)

    imgtool_ecdsa.ec = DeterministicEc()
    rng = random.Random(0x4D435542)
    payload = rng.randbytes(20 * 1024)
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "payload.bin")
        dst = os.path.join(tmp, "signed.bin")
        with open(src, "wb") as f:
            f.write(payload)
        args = ["sign", "-k", str(TOP / "lib/mcuboot/root-ec-p256.pem")]
        args += ["-v", "1.2.3", "-s", "1", "-H", "0x400", "--pad-header", "-S", "0x20000"]
        args += ["--align", "16", "--max-align", "16", src, dst]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            imgtool_main.imgtool.main(args=args, standalone_mode=False)
        with open(dst, "rb") as f:
            return f.read()


class Corpus:
    """Seed files written under one output directory, counted per target."""

    def __init__(self, out):
        self.out = pathlib.Path(out)
        self.counts = {}

    def add(self, target, name, data):
        """Write one seed."""
        d = self.out / target
        d.mkdir(parents=True, exist_ok=True)
        path = d / (name + ".bin")
        if path.exists() and name in self.counts.get(target + "/names", set()):
            raise ValueError("duplicate seed %s/%s" % (target, name))
        self.counts.setdefault(target + "/names", set()).add(name)
        path.write_bytes(bytes(data))
        self.counts[target] = self.counts.get(target, 0) + 1


# ---- fat ----


def fat_seed(sel, pad, img):
    """Return a fat input."""
    return bytes([sel, pad]) + img


def gen_fat(corpus, image):
    """Add the fat seeds."""
    # Where the image is stored for each selector. Selectors 1 and 2 look the name up with a
    # different spelling than the one stored.
    stored = {
        0: "/update.bin",
        1: "/update.bin",
        2: "/update.bin",
        3: "/dir/update.bin",
        4: "/a/b/c/update.bin",
        5: "/firmware.signed.bin",
        6: "/very long file name for the update image.bin",
    }
    volumes = [(12, 1 << 20), (16, 4 << 20), (32, 1 << 20)]
    for fat, size in volumes:
        for sel, path in stored.items():
            img = c.make_fat({path: image}, size=size, fat=fat, trim=True)
            corpus.add("fat", "fat%d_sel%d" % (fat, sel), fat_seed(sel, 0, img))
        # Exact spelling of sel 2 and a volume that is not trimmed.
        img = c.make_fat({"/UPDATE.BIN": image}, size=size, fat=fat, trim=True)
        corpus.add("fat", "fat%d_sel2_upper" % fat, fat_seed(2, 0, img))
        img = c.make_fat({"/update.bin": image}, size=size, fat=fat)
        corpus.add("fat", "fat%d_sel0_full" % fat, fat_seed(0, 0, img))
        img = c.make_fat({"/update.bin": image}, size=size, fat=fat, trim=True)
        corpus.add("fat", "fat%d_sel0_pad4" % fat, fat_seed(0, 4, img))
        corpus.add("fat", "fat%d_sel7_root" % fat, fat_seed(7, 0, img))

        # Names and layout.
        img = c.make_fat({"/UPDATE.BIN": image}, size=size, fat=fat, no_lfn=True, trim=True)
        corpus.add("fat", "fat%d_sfn_only" % fat, fat_seed(0, 0, img))
        files = {"/dir/%s.txt" % ("x" * i): b"filler %d" % i for i in range(1, 30)}
        files["/dir/update.bin"] = image
        img = c.make_fat(files, size=size, fat=fat, trim=True)
        corpus.add("fat", "fat%d_dir_many_lfn" % fat, fat_seed(3, 0, img))
        img = c.make_fat(
            {"/update.bin": image, "/other.bin": image[:1000]}, size=size, fat=fat, trim=True
        )
        corpus.add("fat", "fat%d_two_files" % fat, fat_seed(0, 0, img))
        img = c.make_fat({"/update.bin": b""}, size=size, fat=fat, trim=True)
        corpus.add("fat", "fat%d_empty_file" % fat, fat_seed(0, 0, img))
        img = c.make_fat({"/update.bin": image[:16]}, size=size, fat=fat, trim=True)
        corpus.add("fat", "fat%d_tiny_file" % fat, fat_seed(0, 0, img))

        # Fragmented clusters.
        img = c.make_fat(
            {"/update.bin": image},
            size=size,
            fat=fat,
            cluster_sectors=1,
            fragmented=True,
            trim=True,
        )
        chain = c.fat_chain(img, "/update.bin")
        if all(b == a + 1 for a, b in zip(chain, chain[1:])):
            raise RuntimeError("fat%d fragmented image is contiguous" % fat)
        corpus.add("fat", "fat%d_fragmented" % fat, fat_seed(0, 0, img))

        # Cluster chain loops and directory loops.
        img = c.make_fat({"/update.bin": image}, size=size, fat=fat, cluster_sectors=1, trim=True)
        for name, target in (("first", 0), ("mid", 20), ("self", -1)):
            looped = c.fat_cluster_loop(img, "/update.bin", target)
            corpus.add("fat", "fat%d_cluster_loop_%s" % (fat, name), fat_seed(0, 0, looped))
            looped = c.fat_cluster_loop(img, "/update.bin", target, size=300 << 10)
            corpus.add("fat", "fat%d_cluster_loop_%s_big" % (fat, name), fat_seed(0, 0, looped))
        big = c.fat_set_size(img, "/update.bin", 0xFFFFFFFF)
        corpus.add("fat", "fat%d_size_4g" % fat, fat_seed(0, 0, big))
        big = c.fat_set_size(img, "/update.bin", len(image) + 5000)
        corpus.add("fat", "fat%d_size_past_chain" % fat, fat_seed(0, 0, big))
        files = {"/dir/f%03d.bin" % i: b"x" for i in range(60)}
        img = c.make_fat(files, size=size, fat=fat, cluster_sectors=1, trim=True)
        corpus.add("fat", "fat%d_dir_loop_miss" % fat, fat_seed(3, 0, c.fat_dir_loop(img, "/dir")))
        files["/dir/update.bin"] = image
        img = c.make_fat(files, size=size, fat=fat, cluster_sectors=1, trim=True)
        corpus.add("fat", "fat%d_dir_loop_hit" % fat, fat_seed(3, 0, c.fat_dir_loop(img, "/dir")))
        files = {"/f%03d.bin" % i: b"x" for i in range(300)}
        img = c.make_fat(files, size=size, fat=fat, cluster_sectors=1, trim=True)
        if fat == 32:
            corpus.add("fat", "fat32_root_loop_miss", fat_seed(0, 0, c.fat_dir_loop(img, "/")))

        # Empty and truncated volumes.
        img = c.make_fat({}, size=size, fat=fat, trim=True)
        corpus.add("fat", "fat%d_empty_volume" % fat, fat_seed(0, 0, img))
        full = c.make_fat({"/update.bin": image}, size=size, fat=fat, trim=True)
        for keep in (0, 1, 100, 512, 1024, 4096, len(full) // 2, len(full) - 1):
            corpus.add("fat", "fat%d_truncated_%d" % (fat, keep), fat_seed(0, 0, full[:keep]))
        corpus.add("fat", "fat%d_not_a_fat" % fat, fat_seed(0, 0, bytes(2048)))

    # Cluster sizes. f_mkfs() only accepts combinations that give the cluster count of a type.
    cluster_volumes = {1: (1 << 20, 4 << 20), 2: (2 << 20,), 4: (4 << 20,), 8: (8 << 20,)}
    cluster_volumes[64] = (16 << 20,)
    for cs, sizes in cluster_volumes.items():
        for size in sizes:
            for sel, path in ((0, "/update.bin"), (3, "/dir/update.bin")):
                img = c.make_fat({path: image}, size=size, fat=None, cluster_sectors=cs, trim=True)
                corpus.add("fat", "cs%d_%dk_sel%d" % (cs, size >> 10, sel), fat_seed(sel, 0, img))
    for cs in (1, 2, 4):
        img = c.make_fat(
            {"/update.bin": image}, size=1 << 20, fat=32, cluster_sectors=cs, trim=True
        )
        corpus.add("fat", "fat32_cs%d" % cs, fat_seed(0, 0, img))


# ---- lfs ----


def lfs_seed(sel, bs, img):
    """Return an lfs input."""
    return bytes([sel, bs]) + img


def lfs_size(block_size):
    """Return the image size used for a block size."""
    return max(64 << 10, block_size * 32)


def gen_lfs(corpus, image):
    """Add the lfs seeds."""
    stored = {
        0: "/update.bin",
        1: "/update.bin",
        2: "/UPDATE.BIN",
        3: "/dir/update.bin",
        4: "/a/b/c/update.bin",
        5: "/firmware.signed.bin",
        6: "/very long file name for the update image.bin",
    }
    for bs_sel, bs in enumerate(LFS_BLOCK_SIZES):
        tag = "bs%d%s" % (bs, "_default" if bs_sel == 0 else "")
        size = lfs_size(bs)
        sels = stored if bs_sel in (0, 3, 6) else {0: stored[0], 3: stored[3], 4: stored[4]}
        for sel, path in sels.items():
            img = c.make_lfs2({path: image}, size=size, block_size=bs)
            corpus.add("lfs", "%s_sel%d" % (tag, sel), lfs_seed(sel, bs_sel, img))
        img = c.make_lfs2({"/update.bin": image}, size=size, block_size=bs)
        empty = c.make_lfs2({}, size=size, block_size=bs)
        corpus.add("lfs", "%s_empty_fs" % tag, lfs_seed(0, bs_sel, empty))
        corpus.add("lfs", "%s_sel2_missing" % tag, lfs_seed(2, bs_sel, img))
        # A block size argument that does not match the image.
        corpus.add("lfs", "%s_wrong_bs_arg" % tag, lfs_seed(0, (bs_sel % 7) + 1, img))
        for seed in range(3):
            corpus.add(
                "lfs",
                "%s_corrupt_metadata_%d" % (tag, seed),
                lfs_seed(0, bs_sel, c.lfs2_corrupt_metadata(img, bs, seed)),
            )
        for field in ("block_size", "block_count", "name_max", "file_max"):
            corpus.add(
                "lfs",
                "%s_bad_%s" % (tag, field),
                lfs_seed(0, bs_sel, c.lfs2_bad_superblock(img, field)),
            )
        corpus.add("lfs", "%s_truncated" % tag, lfs_seed(0, bs_sel, img[: len(img) // 2]))
        corpus.add("lfs", "%s_one_block" % tag, lfs_seed(0, bs_sel, img[:bs]))
        # Metadata that has been rewritten a few times.
        files = {"/f%03d.txt" % i: b"%d" % i for i in range(40)}
        files["/update.bin"] = image
        many = c.make_lfs2(files, size=size, block_size=bs)
        corpus.add("lfs", "%s_many_files" % tag, lfs_seed(0, bs_sel, many))
        bad = c.lfs2_bad_superblock(many, "name_max")
        corpus.add("lfs", "%s_many_files_bad_name_max" % tag, lfs_seed(0, bs_sel, bad))
        files = [("/update.old", image[:2000]), ("/update.bin", image), ("/update.old", None)]
        deleted = c.make_lfs2(files, size=size, block_size=bs)
        corpus.add("lfs", "%s_deleted_file" % tag, lfs_seed(0, bs_sel, deleted))


# ---- gz ----


def gz_seed(limit_log2, gz):
    """Return a gz input."""
    return bytes([limit_log2]) + gz


def gen_gz(corpus, image):
    """Add the gz seeds."""
    rng = random.Random(0x677A)
    zeros = bytes(65536)
    noise = rng.randbytes(8192)
    words = [b"update", b"image", b"flash", b"sector", b"boot", b"\n", b"0x400", b"mcuboot"]
    text = b" ".join(rng.choice(words) for _ in range(2500))
    # 11 is a limit of 32 KiB, which holds the image.
    for name, data, limit in (
        ("image", image, 11),
        ("image_big_limit", image, 23),
        ("image_small_limit", image, 6),
        ("image_exact_limit", image[: 1 << 14], 10),
        ("image_limit_plus_one", image[: (1 << 14) + 1], 10),
        ("zeros", zeros, 12),
        ("noise", noise, 9),
        ("text", text, 12),
        ("empty", b"", 0),
        ("one_byte", b"\x5a", 0),
    ):
        corpus.add("gz", name, gz_seed(limit, c.make_gzip(data)))
    for lvl in (0, 1, 6, 9):
        corpus.add("gz", "image_level%d" % lvl, gz_seed(11, c.make_gzip(image, lvl)))
    for name, strategy in (
        ("fixed", zlib.Z_FIXED),
        ("huffman_only", zlib.Z_HUFFMAN_ONLY),
        ("rle", zlib.Z_RLE),
        ("filtered", zlib.Z_FILTERED),
    ):
        corpus.add("gz", "image_%s" % name, gz_seed(11, c.make_gzip(image, 9, strategy=strategy)))
        corpus.add("gz", "text_%s" % name, gz_seed(12, c.make_gzip(text, 9, strategy=strategy)))
    for wbits in (9, 10, 12):
        corpus.add("gz", "text_window%d" % wbits, gz_seed(12, c.make_gzip(text, 9, wbits=wbits)))
    mixes = {
        "stored_fixed_dynamic": [("stored", 3000), ("fixed", 4000), ("dynamic", None)],
        "dynamic_stored": [("dynamic", 5000), ("stored", None)],
        "fixed_fixed_stored": [("fixed", 100), ("fixed", 100), ("stored", None)],
        "stored_big": [("stored", 70000), ("dynamic", None)],
    }
    for name, blocks in mixes.items():
        data = text + zeros + image
        corpus.add("gz", "mix_" + name, gz_seed(17, c.make_gzip(data, blocks=blocks)))
    blocks = [("stored", 2000), ("dynamic", None)]
    corpus.add("gz", "mix_image", gz_seed(11, c.make_gzip(image, blocks=blocks)))

    fields = {
        "fextra": dict(fextra=b"\x41\x70\x04\x00abcd"),
        "fextra_long": dict(fextra=bytes(range(256)) * 8),
        "fextra_empty": dict(fextra=b""),
        "fname": dict(fname="update.bin"),
        "fcomment": dict(fcomment="built by gen_corpus"),
        "fhcrc": dict(fhcrc=True),
        "all": dict(fextra=b"xy", fname="a", fcomment="b", fhcrc=True, mtime=0x5F000000),
        "mtime_xfl_os": dict(mtime=0xFFFFFFFF, xfl=0xFF, os_=0),
    }
    for name, kw in fields.items():
        corpus.add("gz", "header_" + name, gz_seed(11, c.make_gzip(image, **kw)))
    corpus.add("gz", "header_fname_long", gz_seed(11, c.make_gzip(image, fname="n" * 1000)))
    bad_hcrc = bytearray(c.make_gzip(image, fhcrc=True, fname="x"))
    bad_hcrc[10 + 2] ^= 1
    corpus.add("gz", "header_bad_fhcrc", gz_seed(11, bytes(bad_hcrc)))

    good = c.make_gzip(image)
    for kind in (
        "bad_crc",
        "bad_isize",
        "truncated_trailer",
        "truncated_body",
        "bad_method",
        "reserved_flag",
    ):
        corpus.add("gz", "corrupt_" + kind, gz_seed(11, c.corrupt_gzip(good, kind)))
    corpus.add("gz", "truncated_header", gz_seed(11, good[:7]))
    corpus.add("gz", "magic_only", gz_seed(11, b"\x1f\x8b"))
    corpus.add("gz", "not_gzip", gz_seed(11, image[:512]))
    corpus.add("gz", "trailing_garbage", gz_seed(11, good + b"\x00" * 7))

    # limit_log2 n gives a limit of 1 << (n + 4): 12 is 64 KiB, 16 is 1 MiB, 19 is 8 MiB, 20 is
    # 16 MiB.
    corpus.add("gz", "bomb_1m_limit_64k", gz_seed(12, c.gzip_bomb(1 << 20)))
    corpus.add("gz", "bomb_1m_limit_1m", gz_seed(16, c.gzip_bomb(1 << 20)))
    corpus.add("gz", "bomb_16m_limit_8m", gz_seed(19, c.gzip_bomb(16 << 20)))
    corpus.add("gz", "bomb_16m_limit_16m", gz_seed(20, c.gzip_bomb(16 << 20)))
    corpus.add("gz", "bomb_16m_small_window", gz_seed(20, c.gzip_bomb(16 << 20, 1024)))
    corpus.add("gz", "bomb_64k", gz_seed(12, c.gzip_bomb(65536)))
    corpus.add("gz", "no_input", bytes([11]))
    corpus.add("gz", "no_limit", b"")


# ---- stream ----


def stream_seed(mode, data, plan):
    """Return a stream input."""
    return (
        bytes([mode])
        + struct.pack("<H", len(data))
        + data
        + b"".join(struct.pack("<IH", off, length) for off, length in plan)
    )


def stream_plans(n):
    """Return named read plans for a stream of n bytes."""
    step = max(32, n // 24)
    fwd = [(off, min(step, max(n - off, 1))) for off in range(0, max(n, 1), step)]
    plans = {
        "forward": fwd,
        "backward": fwd[::-1],
        "ends": [(0, 1), (max(n - 1, 0), 1), (max(n - 32, 0), 32), (0, 32), (max(n - 1, 0), 1)],
        "whole": [(0, min(n, 0xFFFF) or 1), (0, 1)],
        "zigzag": [(0, 16), (max(n - 16, 0), 16), (16, 16), (max(n - 32, 0), 16), (0, 700)],
        "past_end": [(n, 1), (n + 1, 64), (0xFFFFFFFF, 0xFFFF), (n - 1 if n else 0, 2)],
    }
    return plans


def gen_stream(corpus, image):
    """Add the stream seeds."""
    rng = random.Random(0x5354)
    for n in (0, 1, 31, 32, 64, 1000, 1024, 1025, 20000):
        data = rng.randbytes(n) if n != 20000 else image[:n]
        for pname, plan in stream_plans(n).items():
            if n in (0, 1) and pname not in ("forward", "ends"):
                continue
            modes = (0, 1, 2, 3) if pname in ("forward", "backward", "ends") else (0, 1)
            for mode in modes:
                name = "n%d_mode%d_%s" % (n, mode, pname)
                corpus.add("stream", name, stream_seed(mode, data, plan))
    # Compressible data for the gzip modes, and a plan with many small reads.
    text = (b"abcdefgh" * 128)[:1000]
    small = [(off, 3) for off in range(0, 999, 37)]
    for mode in (1, 3):
        name = "compressible_mode%d_small_reads" % mode
        corpus.add("stream", name, stream_seed(mode, text, small))
    corpus.add("stream", "no_plan", stream_seed(0, text, []))
    corpus.add("stream", "empty", b"")
    corpus.add("stream", "short_length", bytes([0, 5, 0, 1, 2]))


# ---- run ----


def elem(kind, payload):
    """Return one element."""
    if len(payload) > 255:
        raise ValueError("element payload too long")
    return bytes([kind, len(payload)]) + payload


def mount(mp, fs_type, base, length, arg2=None, arg3=None):
    """Return a MOUNT element."""
    p = struct.pack("<BBII", mp, fs_type, base, length)
    if arg2 is not None:
        p += struct.pack("<I", arg2)
    if arg3 is not None:
        p += struct.pack("<I", arg3)
    return elem(ELEM_MOUNT, p)


def fsload(mp, path):
    """Return an FSLOAD element."""
    if isinstance(path, str):
        path = path.encode()
    return elem(ELEM_FSLOAD, bytes([mp]) + path)


def status(addr=STATUS_ADDR):
    """Return a STATUS element."""
    return elem(ELEM_STATUS, struct.pack("<I", addr))


END = bytes([ELEM_END, 0])


def run_seed(flags, elems, payload):
    """Return a run input; elems is the concatenated elements including the END."""
    return bytes([flags]) + struct.pack("<H", len(elems)) + elems + payload


def gen_run(corpus, image):
    """Add the run seeds."""
    fat = c.make_fat({"/update.bin": image}, size=4 << 20, fat=16, trim=True)
    fat12 = c.make_fat({"/update.bin": image}, size=1 << 20, fat=12, trim=True)
    fat32 = c.make_fat({"/update.bin": image}, size=1 << 20, fat=32, trim=True)
    lfs = c.make_lfs2({"/update.bin": image}, size=128 << 10, block_size=512)
    lfs4k = c.make_lfs2({"/dir/update.bin": image}, size=128 << 10, block_size=4096)
    gz = c.make_gzip(image)
    half = len(image) // 2
    B = DEVICE_BASE

    def rejected(flags, name, elems, payload=fat):
        suffix = "_single" if flags & 1 else ""
        corpus.add("run", "reject_" + name + suffix, run_seed(flags, elems, payload))

    for flags, tag in ((0, "swap"), (1, "single")):
        ok = {
            "fat16": (
                mount(1, FS_FAT, B, len(fat)) + fsload(1, "/update.bin") + status() + END,
                fat,
            ),
            "fat12": (mount(1, FS_FAT, B, len(fat12)) + fsload(1, "/update.bin") + END, fat12),
            "fat32": (mount(1, FS_FAT, B, len(fat32)) + fsload(1, "/update.bin") + END, fat32),
            "lfs2_bs512": (
                mount(1, FS_LFS2, B, len(lfs), 512) + fsload(1, "/update.bin") + status() + END,
                lfs,
            ),
            "lfs2_bs4096_nested": (
                mount(2, FS_LFS2, B, len(lfs4k), 4096) + fsload(2, "/dir/update.bin") + END,
                lfs4k,
            ),
            "lfs2_default_bs": (
                mount(1, FS_LFS2, B, len(lfs4k)) + fsload(1, "/dir/update.bin") + END,
                lfs4k,
            ),
            "raw_image": (
                mount(1, FS_RAW, B, len(image)) + fsload(1, "x") + status() + END,
                image,
            ),
            "raw_gzip": (mount(1, FS_RAW, B, len(gz)) + fsload(1, "x") + status() + END, gz),
        }
        for name, (elems, payload) in ok.items():
            corpus.add("run", "%s_%s" % (name, tag), run_seed(flags, elems, payload))
        # The filesystem is not at the start of the device payload.
        shifted = b"\xff" * 4096 + fat
        elems = mount(1, FS_FAT, B + 4096, len(fat)) + fsload(1, "/update.bin") + END
        corpus.add("run", "fat16_shifted_%s" % tag, run_seed(flags, elems, shifted))
        # Raw with two segments: the second half of the image is 64 KiB into the device.
        payload = image[:half] + b"\xff" * (0x10000 - half) + image[half:]
        elems = (
            mount(1, FS_RAW, B, half, B + 0x10000, len(image) - half)
            + fsload(1, "x")
            + status()
            + END
        )
        corpus.add("run", "raw_two_segments_%s" % tag, run_seed(flags, elems, payload))
        elems = mount(1, FS_RAW, B, half, B + 0x10000, 0) + fsload(1, "x") + END
        corpus.add("run", "raw_second_segment_empty_%s" % tag, run_seed(flags, elems, payload))
        # Two mounts, the second one is the one used.
        elems = (
            mount(1, FS_FAT, B, len(fat))
            + mount(2, FS_LFS2, B + 0x80000, len(lfs), 512)
            + fsload(2, "/update.bin")
            + END
        )
        payload = fat + b"\xff" * (0x80000 - len(fat)) + lfs
        corpus.add("run", "two_mounts_%s" % tag, run_seed(flags, elems, payload))

        good_mount = mount(1, FS_FAT, B, len(fat))
        mount_fields = struct.pack("<BBII", 1, FS_FAT, B, len(fat))
        good_fsload = fsload(1, "/update.bin")
        cases = {
            "missing_fsload": good_mount + END,
            "missing_mount": good_fsload + END,
            "empty_stream": END,
            "mount_len9": elem(ELEM_MOUNT, mount_fields[:9]) + good_fsload + END,
            "mount_len11": elem(ELEM_MOUNT, mount_fields + b"\0") + good_fsload + END,
            "mount_len19": elem(ELEM_MOUNT, mount_fields + bytes(9)) + good_fsload + END,
            "mount_len0": elem(ELEM_MOUNT, b"") + good_fsload + END,
            "mount_below_device": mount(1, FS_FAT, B - 0x1000, len(fat)) + good_fsload + END,
            "mount_past_device": mount(1, FS_FAT, B + DEVICE_SIZE, len(fat)) + good_fsload + END,
            "mount_crosses_end": mount(1, FS_FAT, B + DEVICE_SIZE - 512, len(fat))
            + good_fsload
            + END,
            "mount_zero_len": mount(1, FS_FAT, B, 0) + good_fsload + END,
            "mount_wraps": mount(1, FS_FAT, 0xFFFFF000, 0x2000) + good_fsload + END,
            "mount_huge_len": mount(1, FS_FAT, B, 0xFFFFFFFF) + good_fsload + END,
            "mount_ram_base": mount(1, FS_FAT, 0x20000000, len(fat)) + good_fsload + END,
            "unknown_fs_type": mount(1, 9, B, len(fat)) + good_fsload + END,
            "fs_type_zero": mount(1, 0, B, len(fat)) + good_fsload + END,
            "lfs1_type": mount(1, FS_LFS1, B, len(fat), 512) + good_fsload + END,
            "mount_point_mismatch": good_mount + fsload(2, "/update.bin") + END,
            "path_dotdot": good_mount + fsload(1, "/../update.bin") + END,
            "path_dotdot_mid": good_mount + fsload(1, "/dir/../update.bin") + END,
            "path_dotdot_only": good_mount + fsload(1, "..") + END,
            "path_254_missing": good_mount + fsload(1, "/" + "a" * 253) + END,
            "path_too_deep": good_mount + fsload(1, "/a" * 127) + END,
            "path_empty": good_mount + elem(ELEM_FSLOAD, bytes([1])) + END,
            "fsload_len0": good_mount + elem(ELEM_FSLOAD, b"") + END,
            "path_nul": good_mount + fsload(1, b"/update.bin\0x") + END,
            "path_nul_first": good_mount + fsload(1, b"\0update.bin") + END,
            "path_control_chars": good_mount + fsload(1, b"/up\ndate\x7f.bin") + END,
            "path_high_bytes": good_mount + fsload(1, b"/\xff\xfe.bin") + END,
            "status_len0": good_mount + good_fsload + elem(ELEM_STATUS, b"") + END,
            "status_len3": good_mount + good_fsload + elem(ELEM_STATUS, b"\0\0\0") + END,
            "status_len5": good_mount + good_fsload + elem(ELEM_STATUS, b"\0\0\0\0\0") + END,
            "status_unaligned": good_mount + good_fsload + status(STATUS_ADDR + 2) + END,
            "status_null": good_mount + good_fsload + status(0) + END,
            "status_in_flash": good_mount + good_fsload + status(B + 0x100) + END,
            "end_with_length": good_mount + good_fsload + bytes([ELEM_END, 3, 0, 0, 0]),
            "no_end": good_mount + good_fsload,
            "truncated_element": good_mount + good_fsload[:-3],
            "unknown_element": good_mount + elem(9, b"abc") + good_fsload + END,
            "zero_type_element": good_mount + bytes([0, 0]) + good_fsload + END,
        }
        for name, elems in cases.items():
            rejected(flags, name, elems)
        # elems_len larger or smaller than the real stream.
        base = good_mount + good_fsload + END
        for name, n in (("long", len(base) + 40), ("short", len(base) - 2), ("zero", 0)):
            seed = bytes([flags]) + struct.pack("<H", n) + base + fat
            corpus.add("run", "reject_elems_len_%s_%s" % (name, tag), seed)
        # Filesystem or file problems behind a well formed request.
        elems = good_mount + fsload(1, "/missing.bin") + END
        corpus.add("run", "reject_file_missing_%s" % tag, run_seed(flags, elems, fat))
        elems = mount(1, FS_FAT, B, len(fat)) + good_fsload + END
        looped = c.fat_cluster_loop(fat, "/update.bin", size=300 << 10)
        corpus.add("run", "reject_fat_loop_%s" % tag, run_seed(flags, elems, looped))
        corpus.add("run", "reject_fat_erased_%s" % tag, run_seed(flags, elems, b"\xff" * len(fat)))
        elems = mount(1, FS_LFS2, B, len(lfs), 512) + good_fsload + END
        corrupt = c.lfs2_corrupt_metadata(lfs, 512, 1)
        corpus.add("run", "reject_lfs_corrupt_%s" % tag, run_seed(flags, elems, corrupt))
        elems = mount(1, FS_LFS2, B, len(lfs), 100) + good_fsload + END
        corpus.add("run", "reject_lfs_bs100_%s" % tag, run_seed(flags, elems, lfs))
        elems = mount(1, FS_LFS2, B, len(lfs), 0xFFFFFFFF) + good_fsload + END
        corpus.add("run", "reject_lfs_bs_huge_%s" % tag, run_seed(flags, elems, lfs))
        bad = c.corrupt_gzip(gz, "bad_crc")
        elems = mount(1, FS_RAW, B, len(bad)) + fsload(1, "x") + END
        corpus.add("run", "reject_raw_gzip_bad_crc_%s" % tag, run_seed(flags, elems, bad))
        bomb = c.gzip_bomb(64 << 20)
        elems = mount(1, FS_RAW, B, len(bomb)) + fsload(1, "x") + END
        corpus.add("run", "reject_raw_gzip_bomb_%s" % tag, run_seed(flags, elems, bomb))
        tampered = bytearray(image)
        tampered[len(image) // 2] ^= 0x10
        elems = mount(1, FS_RAW, B, len(tampered)) + fsload(1, "x") + END
        corpus.add("run", "reject_raw_tampered_%s" % tag, run_seed(flags, elems, bytes(tampered)))
        elems = mount(1, FS_RAW, B, len(image) // 3) + fsload(1, "x") + END
        corpus.add("run", "reject_raw_truncated_%s" % tag, run_seed(flags, elems, image))


def main():
    """Generate every corpus; return the exit status."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--image", help="signed MCUboot image to use as the update file")
    args = ap.parse_args()

    image = make_image(args.image)
    corpus = Corpus(args.out)
    for target, gen in (
        ("fat", gen_fat),
        ("lfs", gen_lfs),
        ("gz", gen_gz),
        ("stream", gen_stream),
        ("run", gen_run),
    ):
        gen(corpus, image)
        print("%s: %d seeds" % (target, corpus.counts[target]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
