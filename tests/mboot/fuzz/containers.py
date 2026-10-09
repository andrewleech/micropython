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

"""Builders and mutators of filesystem and gzip containers for the fsload fuzz seeds.

Images are written by the real libraries through tools/mkimg.c (built with gcc on first use).
The mutators work directly on the on-disk structures of FAT and littlefs2 volumes. Every
function is deterministic for its arguments.
"""

import os
import pathlib
import random
import struct
import subprocess
import tempfile
import zlib

_HERE = pathlib.Path(__file__).resolve().parent
_TOP = _HERE.parents[2]
_TOOLS = _HERE / "tools"

_MKIMG_SOURCES = [
    _TOOLS / "mkimg.c",
    _TOOLS / "mkimg_ffconf.h",
    _TOP / "lib/oofatfs/ff.c",
    _TOP / "lib/oofatfs/ff.h",
    _TOP / "lib/oofatfs/ffunicode.c",
    _TOP / "lib/oofatfs/diskio.h",
    _TOP / "lib/littlefs/lfs2.c",
    _TOP / "lib/littlefs/lfs2.h",
    _TOP / "lib/littlefs/lfs2_util.c",
    _TOP / "lib/littlefs/lfs2_util.h",
]

SECTOR = 512

# Clusters FAT12 and FAT16 volumes may have at most (the limits lib/oofatfs uses).
_MAX_FAT12 = 0xFF5
_MAX_FAT16 = 0xFFF5


# ---- mkimg ----


def mkimg_path():
    """Return the path of the mkimg binary, building it with gcc when missing or stale."""
    build_dir = pathlib.Path(os.environ.get("MKIMG_BUILD_DIR", "/tmp/mboot_fuzz_tools"))
    exe = build_dir / "mkimg"
    newest = max(p.stat().st_mtime for p in _MKIMG_SOURCES)
    if exe.exists() and exe.stat().st_mtime >= newest:
        return str(exe)
    build_dir.mkdir(parents=True, exist_ok=True)
    tmp = build_dir / ("mkimg.%d.tmp" % os.getpid())
    cmd = [
        "gcc",
        "-std=gnu99",
        "-O1",
        "-I%s" % _TOP,
        "-I%s" % _TOOLS,
        '-DFFCONF_H="mkimg_ffconf.h"',
        "-DLFS2_NO_DEBUG",
        "-DLFS2_NO_WARN",
        "-DLFS2_NO_ERROR",
        "-o",
        str(tmp),
        str(_TOOLS / "mkimg.c"),
        str(_TOP / "lib/oofatfs/ff.c"),
        str(_TOP / "lib/oofatfs/ffunicode.c"),
        str(_TOP / "lib/littlefs/lfs2.c"),
        str(_TOP / "lib/littlefs/lfs2_util.c"),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    os.replace(tmp, exe)
    return str(exe)


def _run_mkimg(head, ops):
    """Run mkimg with a list of (dest, bytes or None) operations and return the image."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out.img")
        args = [mkimg_path(), head[0], out] + head[1:]
        for i, (dest, data) in enumerate(ops):
            if data is None:
                args.append("rm=" + dest)
            else:
                src = os.path.join(tmp, "f%d" % i)
                with open(src, "wb") as f:
                    f.write(data)
                args.append("%s=%s" % (dest, src))
        res = subprocess.run(args, capture_output=True, text=True)
        if res.returncode != 0:
            raise ValueError("mkimg %s failed: %s" % (head[0], res.stderr.strip()))
        with open(out, "rb") as f:
            return f.read()


def _ops(files):
    """Return files, a dict or a list of (path, bytes or None) pairs, as a list of pairs."""
    return list(files.items()) if isinstance(files, dict) else list(files)


def _fat32_min_volume(cluster_sectors):
    """Return the smallest FAT32 volume size in bytes for a cluster size, in whole MiB."""
    sectors = (_MAX_FAT16 + 1) * (cluster_sectors or 1) + 1100
    return -(-sectors * SECTOR // (1 << 20)) << 20


def make_fat(
    files,
    size=1 << 20,
    fat=16,
    cluster_sectors=None,
    *,
    trim=False,
    no_lfn=False,
    fragmented=False,
):
    """Return a bare FAT volume written by lib/oofatfs holding files {path: bytes}.

    files may also be a list of (path, bytes) pairs; a value of None deletes the path, and
    operations run in order. fat is 12, 16 or 32 (None: whatever f_mkfs picks). Paths may be
    nested and longer than 8.3. FAT32 needs 65526 clusters, so a smaller size is raised to the
    minimum (over 32 MiB), the image is cut after the last non-zero sector, and it is zero
    padded back to size; the boot sector still declares the whole volume. trim cuts any image
    that way. no_lfn restricts names to 8.3 (upper-cased). fragmented interleaves the files
    with deleted filler files so their cluster chains are not contiguous.
    """
    head = ["fat", str(size)]
    if fat is not None:
        head += ["--fat", str(fat)]
    pad_to = 0
    if fat == 32:
        if cluster_sectors is not None and cluster_sectors > 8:
            raise ValueError("FAT32 volumes with more than 8 sectors per cluster are too large")
        minimum = _fat32_min_volume(cluster_sectors)
        if size < minimum:
            head[1] = str(minimum)
            pad_to = size
            trim = True
    if cluster_sectors is not None:
        head += ["--cluster-sectors", str(cluster_sectors)]
    if no_lfn:
        head.append("--no-lfn")
    if trim:
        head.append("--trim")
    ops = _ops(files)
    if fragmented:
        total = sum(len(v) for _, v in ops if v)
        fillers = ["/FILL%03d.TMP" % i for i in range(2 * (total // 4096 + 4))]
        pre = [(name, b"\xa5" * 4096) for name in fillers]
        pre += [(name, None) for name in fillers[1::2]]
        ops = pre + ops + [(name, None) for name in fillers[0::2]]
    img = _run_mkimg(head, ops)
    if len(img) < pad_to:
        img += bytes(pad_to - len(img))
    return img


def make_lfs2(files, size=1 << 20, block_size=512):
    """Return a littlefs2 image written by lib/littlefs holding files {path: bytes}.

    files may also be a list of (path, bytes) pairs; a value of None deletes the path.
    Operations run in order. The configuration is read_size = prog_size = 32, cache_size = 128,
    lookahead_size = 32, block_cycles = -1; erased blocks are 0xFF.
    """
    return _run_mkimg(["lfs2", str(size), str(block_size)], _ops(files))


# ---- gzip ----


def _crc32_zeros(n):
    """Return the CRC-32 of n zero bytes."""
    crc = 0
    chunk = bytes(1 << 20)
    while n > 0:
        k = min(n, len(chunk))
        crc = zlib.crc32(chunk[:k], crc)
        n -= k
    return crc


def _deflate(data, level, strategy, wbits, blocks):
    """Return a raw deflate stream; blocks is a list of (kind, length) segments or None."""
    if blocks is None:
        c = zlib.compressobj(level, zlib.DEFLATED, -wbits, 8, strategy)
        return c.compress(data) + c.flush()
    out = bytearray()
    pos = 0
    for kind, length in blocks:
        seg = data[pos:] if length is None else data[pos : pos + length]
        pos += len(seg)
        if kind == "stored":
            c = zlib.compressobj(0, zlib.DEFLATED, -wbits, 8, zlib.Z_DEFAULT_STRATEGY)
        elif kind == "fixed":
            c = zlib.compressobj(level or 6, zlib.DEFLATED, -wbits, 8, zlib.Z_FIXED)
        elif kind == "dynamic":
            c = zlib.compressobj(level or 6, zlib.DEFLATED, -wbits, 8, strategy)
        else:
            raise ValueError("unknown block kind %r" % (kind,))
        out += c.compress(seg) + c.flush(zlib.Z_SYNC_FLUSH)
    if pos != len(data):
        raise ValueError("blocks cover %d of %d bytes" % (pos, len(data)))
    return bytes(out) + b"\x01\x00\x00\xff\xff"


def _z_str(value, name):
    """Return a zero terminated header string field."""
    raw = value.encode("latin-1") if isinstance(value, str) else bytes(value)
    if b"\0" in raw:
        raise ValueError("%s contains a NUL" % name)
    return raw + b"\0"


def make_gzip(
    data,
    level=9,
    *,
    strategy=zlib.Z_DEFAULT_STRATEGY,
    wbits=15,
    blocks=None,
    mtime=0,
    xfl=None,
    os_=3,
    fextra=None,
    fname=None,
    fcomment=None,
    fhcrc=False,
):
    """Return a gzip file of data with a hand built header and a correct CRC-32/ISIZE trailer.

    level and strategy go to zlib (level 0 gives stored blocks, zlib.Z_FIXED fixed Huffman
    blocks). wbits is the deflate window (9..15). blocks is a list of (kind, length) with kind
    'stored', 'fixed' or 'dynamic' (zlib's choice) and length None for the rest of data; each
    entry makes one deflate segment. fextra, fname and fcomment set the optional header fields
    (bytes or str); fhcrc adds the header CRC-16.
    """
    flg = 0
    fields = b""
    if fextra is not None:
        extra = bytes(fextra)
        if len(extra) > 0xFFFF:
            raise ValueError("fextra too long")
        flg |= 4
        fields += struct.pack("<H", len(extra)) + extra
    if fname is not None:
        flg |= 8
        fields += _z_str(fname, "fname")
    if fcomment is not None:
        flg |= 16
        fields += _z_str(fcomment, "fcomment")
    if fhcrc:
        flg |= 2
    if xfl is None:
        xfl = 2 if level == 9 else 4 if level == 1 else 0
    head = struct.pack("<BBBBIBB", 0x1F, 0x8B, 8, flg, mtime & 0xFFFFFFFF, xfl, os_) + fields
    if fhcrc:
        head += struct.pack("<H", zlib.crc32(head) & 0xFFFF)
    body = _deflate(data, level, strategy, wbits, blocks)
    return head + body + struct.pack("<II", zlib.crc32(data), len(data) & 0xFFFFFFFF)


def corrupt_gzip(gz, kind):
    """Return gz damaged: bad_crc, bad_isize, truncated_trailer/body, bad_method, reserved_flag."""
    b = bytearray(gz)
    if kind == "bad_crc":
        b[-8] ^= 0x01
    elif kind == "bad_isize":
        b[-4] ^= 0x01
    elif kind == "truncated_trailer":
        del b[-3:]
    elif kind == "truncated_body":
        del b[max(10, len(b) // 2) :]
    elif kind == "bad_method":
        b[2] = 7
    elif kind == "reserved_flag":
        b[3] |= 0x20
    else:
        raise ValueError("unknown corruption %r" % (kind,))
    return bytes(b)


_LEN_BASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31]
_LEN_BASE += [35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258]
_LEN_EXTRA = [0] * 8 + [1] * 4 + [2] * 4 + [3] * 4 + [4] * 4 + [5] * 4 + [0]
_DIST_BASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769]
_DIST_BASE += [1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577]
_DIST_EXTRA = [0, 0, 0, 0] + [i // 2 for i in range(2, 28)]
_CLEN_ORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]


def _len_symbol(length):
    """Return (symbol, extra_bits, extra_value) of a match length 3..258."""
    i = max(i for i, base in enumerate(_LEN_BASE) if base <= length)
    if length == 258:
        i = 28
    return 257 + i, _LEN_EXTRA[i], length - _LEN_BASE[i]


def _dist_symbol(dist):
    """Return (symbol, extra_bits, extra_value) of a match distance 1..32768."""
    i = max(i for i, base in enumerate(_DIST_BASE) if base <= dist)
    return i, _DIST_EXTRA[i], dist - _DIST_BASE[i]


def _huffman_lengths(freq):
    """Return {symbol: code length} of a Huffman code for {symbol: count}."""
    if len(freq) == 1:
        return {s: 1 for s in freq}
    nodes = sorted((n, s, (s,)) for s, n in freq.items())
    depth = {s: 0 for s in freq}
    while len(nodes) > 1:
        a, b = nodes[0], nodes[1]
        for s in a[2] + b[2]:
            depth[s] += 1
        nodes = sorted(nodes[2:] + [(a[0] + b[0], min(a[1], b[1]), a[2] + b[2])])
    return depth


def _canonical_codes(lengths):
    """Return {symbol: (code, length)} for canonical Huffman code lengths (RFC 1951)."""
    codes = {}
    code = 0
    prev = 0
    for length, sym in sorted((n, s) for s, n in lengths.items() if n):
        code <<= length - prev
        prev = length
        codes[sym] = (code, length)
        code += 1
    return codes


class _Bits:
    """LSB-first bit writer for deflate."""

    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.n = 0

    def put(self, value, nbits):
        """Append the low nbits of value, least significant bit first."""
        self.acc |= value << self.n
        self.n += nbits
        while self.n >= 8:
            self.out.append(self.acc & 0xFF)
            self.acc >>= 8
            self.n -= 8

    def put_code(self, code, nbits):
        """Append a Huffman code, most significant bit first."""
        rev = int(format(code, "0%db" % nbits)[::-1], 2)
        self.put(rev, nbits)

    def finish(self):
        """Pad to a byte boundary and return the bytes."""
        if self.n:
            self.out.append(self.acc & 0xFF)
            self.acc = 0
            self.n = 0
        return bytes(self.out)


def _dynamic_block(tokens):
    """Return a final dynamic Huffman block of ('lit', b) and ('match', len, dist) tokens."""
    lit_freq = {256: 1}
    dist_freq = {}
    for t in tokens:
        if t[0] == "lit":
            lit_freq[t[1]] = lit_freq.get(t[1], 0) + 1
        else:
            s = _len_symbol(t[1])[0]
            lit_freq[s] = lit_freq.get(s, 0) + 1
            d = _dist_symbol(t[2])[0]
            dist_freq[d] = dist_freq.get(d, 0) + 1
    if not dist_freq:
        dist_freq[0] = 1
    lit_len = _huffman_lengths(lit_freq)
    dist_len = _huffman_lengths(dist_freq)
    nlit = max(257, max(lit_len) + 1)
    ndist = max(dist_len) + 1
    seq = [lit_len.get(i, 0) for i in range(nlit)] + [dist_len.get(i, 0) for i in range(ndist)]

    # Run length encode the code lengths with symbols 17 and 18 for zero runs.
    rle = []
    i = 0
    while i < len(seq):
        run = 1
        while i + run < len(seq) and seq[i + run] == seq[i]:
            run += 1
        if seq[i] == 0 and run >= 3:
            run = min(run, 138)
            rle.append((18, 7, run - 11) if run >= 11 else (17, 3, run - 3))
        else:
            run = 1
            rle.append((seq[i], 0, 0))
        i += run
    clen_freq = {}
    for sym, _, _ in rle:
        clen_freq[sym] = clen_freq.get(sym, 0) + 1
    clen_len = _huffman_lengths(clen_freq)
    assert max(clen_len.values()) <= 7 and max(lit_len.values()) <= 15
    nclen = max(4, 1 + max(i for i, s in enumerate(_CLEN_ORDER) if clen_len.get(s, 0)))

    w = _Bits()
    w.put(1, 1)
    w.put(2, 2)
    w.put(nlit - 257, 5)
    w.put(ndist - 1, 5)
    w.put(nclen - 4, 4)
    for s in _CLEN_ORDER[:nclen]:
        w.put(clen_len.get(s, 0), 3)
    clen_codes = _canonical_codes(clen_len)
    for sym, nbits, val in rle:
        w.put_code(*clen_codes[sym])
        if nbits:
            w.put(val, nbits)
    lit_codes = _canonical_codes(lit_len)
    dist_codes = _canonical_codes(dist_len)
    for t in tokens:
        if t[0] == "lit":
            w.put_code(*lit_codes[t[1]])
        else:
            sym, nbits, val = _len_symbol(t[1])
            w.put_code(*lit_codes[sym])
            if nbits:
                w.put(val, nbits)
            sym, nbits, val = _dist_symbol(t[2])
            w.put_code(*dist_codes[sym])
            if nbits:
                w.put(val, nbits)
    w.put_code(*lit_codes[256])
    return w.finish()


def gzip_bomb(expanded, window=32768):
    """Return a gzip file of one dynamic block that inflates to `expanded` zero bytes.

    The matches are 258 bytes long at distance 1, which costs two bits per 258 bytes (the
    deflate limit is about 1032:1, so 16 MiB take about 16 KiB); the last full match reaches
    back `window` bytes when that many have been produced. The trailer is correct.
    """
    if not 1 <= window <= 32768:
        raise ValueError("window must be 1..32768")
    if expanded < 1:
        return make_gzip(b"")
    n258, rest = divmod(expanded - 1, 258)
    tokens = [("lit", 0)]
    if rest >= 3:
        tokens.append(("match", rest, 1))
    else:
        tokens += [("lit", 0)] * rest
    tokens = tokens[:1] + [("match", 258, 1)] * n258 + tokens[1:]
    if n258 and 1 + 258 * (n258 - 1) >= window:
        tokens[n258] = ("match", 258, window)
    head = struct.pack("<BBBBIBB", 0x1F, 0x8B, 8, 0, 0, 0, 3)
    trailer = struct.pack("<II", _crc32_zeros(expanded), expanded & 0xFFFFFFFF)
    return head + _dynamic_block(tokens) + trailer


# ---- FAT mutators ----


class _Fat:
    """Geometry and FAT access of a FAT12/16/32 volume with the boot sector at offset 0."""

    def __init__(self, img):
        self.img = img
        self.bps = struct.unpack_from("<H", img, 11)[0]
        self.spc = img[13]
        rsv = struct.unpack_from("<H", img, 14)[0]
        self.nfats = img[16]
        rootent = struct.unpack_from("<H", img, 17)[0]
        tot = struct.unpack_from("<H", img, 19)[0] or struct.unpack_from("<I", img, 32)[0]
        fatsz = struct.unpack_from("<H", img, 22)[0] or struct.unpack_from("<I", img, 36)[0]
        if self.bps == 0 or self.spc == 0 or self.nfats == 0 or fatsz == 0:
            raise ValueError("not a FAT volume")
        self.fat_start = rsv
        self.fatsz = fatsz
        self.root_sectors = (rootent * 32 + self.bps - 1) // self.bps
        self.root_start = rsv + self.nfats * fatsz
        self.data_start = self.root_start + self.root_sectors
        self.nclust = (tot - self.data_start) // self.spc
        self.type = 12 if self.nclust <= _MAX_FAT12 else 16 if self.nclust <= _MAX_FAT16 else 32
        self.root_cluster = struct.unpack_from("<I", img, 44)[0] if self.type == 32 else 0
        self.eoc = {12: 0xFF8, 16: 0xFFF8, 32: 0x0FFFFFF8}[self.type]

    def get(self, n):
        """Return the FAT entry of cluster n."""
        base = self.fat_start * self.bps
        if self.type == 12:
            v = struct.unpack_from("<H", self.img, base + n + n // 2)[0]
            return v >> 4 if n & 1 else v & 0xFFF
        if self.type == 16:
            return struct.unpack_from("<H", self.img, base + 2 * n)[0]
        return struct.unpack_from("<I", self.img, base + 4 * n)[0] & 0x0FFFFFFF

    def set(self, buf, n, value):
        """Set the FAT entry of cluster n in every FAT copy of the bytearray buf."""
        for copy in range(self.nfats):
            base = (self.fat_start + copy * self.fatsz) * self.bps
            if self.type == 12:
                off = base + n + n // 2
                v = struct.unpack_from("<H", buf, off)[0]
                v = (v & 0x000F) | (value << 4) if n & 1 else (v & 0xF000) | value
                struct.pack_into("<H", buf, off, v & 0xFFFF)
            elif self.type == 16:
                struct.pack_into("<H", buf, base + 2 * n, value)
            else:
                old = struct.unpack_from("<I", buf, base + 4 * n)[0]
                struct.pack_into("<I", buf, base + 4 * n, (old & 0xF0000000) | value)

    def chain(self, start):
        """Return the clusters of the chain starting at start, stopping at a repeat."""
        out = []
        n = start
        while 2 <= n < self.nclust + 2 and n not in out:
            out.append(n)
            n = self.get(n)
            if n >= self.eoc:
                break
        return out

    def cluster_bytes(self, n):
        """Return the data of cluster n."""
        off = (self.data_start + (n - 2) * self.spc) * self.bps
        return self.img[off : off + self.spc * self.bps]

    def dir_bytes(self, cluster):
        """Return the directory data of a cluster chain (0 = FAT12/16 root directory)."""
        if cluster == 0:
            off = self.root_start * self.bps
            return self.img[off : off + self.root_sectors * self.bps]
        return b"".join(self.cluster_bytes(n) for n in self.chain(cluster))

    def dir_offset(self, cluster, index):
        """Return the image offset of byte `index` of the directory data of a cluster chain."""
        if cluster == 0:
            return self.root_start * self.bps + index
        size = self.spc * self.bps
        n = self.chain(cluster)[index // size]
        return (self.data_start + (n - 2) * self.spc) * self.bps + index % size

    def entries(self, cluster):
        """Return (name, short name, attr, first cluster, size, image offset) per entry."""
        raw = self.dir_bytes(cluster)
        out = []
        lfn = {}
        for off in range(0, len(raw) - 31, 32):
            e = raw[off : off + 32]
            if e[0] == 0:
                break
            if e[0] == 0xE5:
                lfn = {}
                continue
            if e[11] == 0x0F:
                chars = e[1:11] + e[14:26] + e[28:32]
                lfn[e[0] & 0x1F] = chars.decode("utf-16-le").split("\0")[0]
                continue
            base = e[0:8].rstrip(b" ")
            if base[:1] == b"\x05":
                base = b"\xe5" + base[1:]
            ext = e[8:11].rstrip(b" ")
            if e[12] & 0x08:
                base = base.lower()
            if e[12] & 0x10:
                ext = ext.lower()
            short = (base + (b"." + ext if ext else b"")).decode("cp437")
            long_name = "".join(lfn[k] for k in sorted(lfn)) or short
            first = struct.unpack_from("<H", e, 26)[0]
            if self.type == 32:
                first |= struct.unpack_from("<H", e, 20)[0] << 16
            size = struct.unpack_from("<I", e, 28)[0]
            out.append((long_name, short, e[11], first, size, self.dir_offset(cluster, off)))
            lfn = {}
        return out

    def find(self, path):
        """Return (first cluster, size, is_directory, entry offset) of path ("/": the root)."""
        cluster = self.root_cluster
        parts = [p for p in path.split("/") if p]
        if not parts:
            return cluster, 0, True, None
        for i, part in enumerate(parts):
            for name, short, attr, first, size, entry in self.entries(cluster):
                if part.lower() in (name.lower(), short.lower()):
                    break
            else:
                raise FileNotFoundError(path)
            is_dir = bool(attr & 0x10)
            if i == len(parts) - 1:
                return first, size, is_dir, entry
            if not is_dir:
                raise NotADirectoryError(path)
            cluster = first
        raise AssertionError


def fat_chain(img, path):
    """Return the clusters of the chain of the file or directory at path (empty: no chain)."""
    fat = _Fat(img)
    first = fat.find(path)[0]
    return fat.chain(first) if first else []


def fat_cluster_loop(img, path, target=0, size=None):
    """Return img with the last cluster of the file at path linked back to its cluster `target`.

    The size in the directory entry is set to `size` when given. The loop is only followed
    when the size exceeds the clusters of the chain.
    """
    fat = _Fat(img)
    first, _, is_dir, entry = fat.find(path)
    if is_dir or first == 0:
        raise ValueError("%s is not a file with data" % path)
    chain = fat.chain(first)
    buf = bytearray(img)
    fat.set(buf, chain[-1], chain[target])
    if size is not None:
        struct.pack_into("<I", buf, entry + 28, size)
    return bytes(buf)


def fat_set_size(img, path, size):
    """Return img with the size in the directory entry of the file at path set to size."""
    entry = _Fat(img).find(path)[3]
    buf = bytearray(img)
    struct.pack_into("<I", buf, entry + 28, size)
    return bytes(buf)


def fat_dir_loop(img, dirpath):
    """Return img with the last cluster of the directory dirpath linked back to its first."""
    fat = _Fat(img)
    first, _, is_dir, _ = fat.find(dirpath)
    if not is_dir or first == 0:
        raise ValueError("%s is not a directory with a cluster chain" % dirpath)
    chain = fat.chain(first)
    buf = bytearray(img)
    fat.set(buf, chain[-1], chain[0])
    return bytes(buf)


# ---- littlefs2 mutators ----
#
# A metadata block is a little endian revision count followed by commits. A commit is a
# sequence of tags, each stored big endian and XORed with the previous tag in the block (the
# first with 0xffffffff), each followed by its data. It ends with a CRC tag (type 0x500) whose
# 4 data bytes are the CRC-32 of the block from byte 0 (first commit) or from the end of the
# previous commit (later commits), up to and including the 4 tag bytes of the CRC tag.
# The CRC is lfs2_crc() of lib/littlefs/lfs2_util.c: the reflected CRC-32 with polynomial
# 0x04c11db7 (0xedb88320 reflected), initial value 0xffffffff and no final XOR, which is
# zlib.crc32(data) ^ 0xffffffff. Tag layout: valid bit 31 (clear), type 11 bits, id 10 bits,
# size 10 bits. The superblock is the tag SUPERBLOCK (0x0ff, id 0, "littlefs") followed by an
# inline struct tag (0x201, id 0, 24 bytes): version, block_size, block_count, name_max,
# file_max, attr_max as little endian words.

_SUPERBLOCK_FIELDS = {"block_size": 1, "block_count": 2, "name_max": 3, "file_max": 4}


def _lfs2_crc(data, crc=0xFFFFFFFF):
    """Return lfs2_crc(crc, data)."""
    return (zlib.crc32(data, crc ^ 0xFFFFFFFF) ^ 0xFFFFFFFF) & 0xFFFFFFFF


def _lfs2_commits(block):
    """Return [(commit start, [(tag, data offset)], crc tag offset)] of the valid commits."""
    commits = []
    off = 4
    start = 0
    ptag = 0xFFFFFFFF
    tags = []
    while off + 4 <= len(block):
        tag = struct.unpack_from(">I", block, off)[0] ^ ptag
        size = tag & 0x3FF
        dsize = 4 + (0 if size == 0x3FF else size)
        if tag & 0x80000000 or off + dsize > len(block):
            break
        ptag = tag
        if (tag >> 20) & 0x780 == 0x500:
            if off + 8 > len(block):
                break
            stored = struct.unpack_from("<I", block, off + 4)[0]
            if stored != _lfs2_crc(block[start : off + 4], 0xFFFFFFFF):
                break
            commits.append((start, tags, off))
            ptag ^= (((tag >> 20) & 1) << 31) & 0xFFFFFFFF
            tags = []
            off += dsize
            start = off
            continue
        tags.append((tag, off + 4))
        off += dsize
    return commits


def _lfs2_fix_crc(block, start, crc_off):
    """Rewrite the CRC of the commit that begins at start and whose CRC tag is at crc_off."""
    crc = _lfs2_crc(bytes(block[start : crc_off + 4]))
    struct.pack_into("<I", block, crc_off + 4, crc)


def lfs2_corrupt_metadata(img, block_size, seed=0):
    """Return img with bits flipped in metadata blocks 0 and 1 so that their CRCs fail."""
    rng = random.Random(seed)
    buf = bytearray(img)
    for blk in (0, 1):
        lo = blk * block_size
        if lo + block_size > len(buf):
            break
        used = block_size
        while used > 16 and buf[lo + used - 1] == 0xFF:
            used -= 1
        if used <= 17:
            continue
        for _ in range(3):
            # Keep the revision, the first tag and the "littlefs" magic (bytes 0..15) intact.
            pos = lo + rng.randrange(16, used)
            buf[pos] ^= 1 << rng.randrange(8)
    return bytes(buf)


def lfs2_bad_superblock(img, field, value=None):
    """Return img with a superblock field (block_size, block_count, name_max, file_max) wrong.

    The field is rewritten in the superblock struct of metadata blocks 0 and 1, and the CRC of
    the commit holding it is recomputed so the blocks stay valid. The default value is twice
    the block size, twice the block count, 256 (one over the name limit) and 0x80000000
    (one over the file size limit).
    """
    if field not in _SUPERBLOCK_FIELDS:
        raise ValueError("unknown superblock field %r" % (field,))
    buf = bytearray(img)
    block_size = _lfs2_block_size(img)
    defaults = {
        "block_size": lambda sb: struct.unpack_from("<I", sb, 4)[0] * 2,
        "block_count": lambda sb: struct.unpack_from("<I", sb, 8)[0] * 2,
        "name_max": lambda sb: 256,
        "file_max": lambda sb: 0x80000000,
    }
    patched = 0
    for i in range(2):
        lo = i * block_size
        block = buf[lo : lo + block_size]
        for start, tags, crc_off in _lfs2_commits(block):
            for tag, data_off in tags:
                if tag >> 20 == 0x201 and (tag >> 10) & 0x3FF == 0 and tag & 0x3FF == 24:
                    sb = block[data_off : data_off + 24]
                    v = defaults[field](sb) if value is None else value
                    struct.pack_into("<I", block, data_off + 4 * _SUPERBLOCK_FIELDS[field], v)
                    _lfs2_fix_crc(block, start, crc_off)
                    patched += 1
        buf[lo : lo + block_size] = block
    if not patched:
        raise ValueError("no littlefs2 superblock found")
    return bytes(buf)


def _lfs2_block_size(img):
    """Return the block size of a littlefs2 image, found from its superblock."""
    for cand in (128, 256, 512, 1024, 2048, 4096, 8192):
        for i in range(2):
            block = img[i * cand : (i + 1) * cand]
            off = _lfs2_superblock_at(block)
            if off is not None and struct.unpack_from("<I", block, off + 4)[0] == cand:
                return cand
    raise ValueError("no littlefs2 superblock found")


def _lfs2_superblock_at(block):
    """Return the offset of the superblock struct data in a metadata block, or None."""
    for _, tags, _ in _lfs2_commits(bytes(block)):
        for tag, data_off in tags:
            if tag >> 20 == 0x201 and (tag >> 10) & 0x3FF == 0 and tag & 0x3FF == 24:
                return data_off
    return None
