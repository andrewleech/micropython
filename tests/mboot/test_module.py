# Tests for the mboot module on the unix port build with the file backed fake flash
# (ports/unix VARIANT=mboot). Run with:
#   cd tests && MICROPY_MICROPYTHON=<build>/micropython ./run-tests.py mboot/test_module.py
#
# The expected output (test_module.py.exp) is a list of "ok <name>" lines; a failing check prints
# "FAIL <name>: <detail>" instead.

try:
    import mboot
    import mboot_fake as ff
except ImportError:
    print("SKIP")
    raise SystemExit

import binascii
import struct


# errno values (the errno module does not provide all of them)
class errno:
    EIO = 5
    EBADF = 9
    ENODEV = 19
    EINVAL = 22
    ENOSPC = 28


DEV_BASE, DEV_SIZE, ERASE, WRITE_UNIT, ERASED, MAX_IMAGE = ff.geometry()

SLOTS = {name: (start, size) for name, start, size in mboot.slots()}
PRIMARY = SLOTS["primary"][0]
SECONDARY = SLOTS["secondary"][0]
SPARE = ERASE if SLOTS["secondary"][1] - SLOTS["primary"][1] == ERASE else 0
UPDATE = SECONDARY + SPARE
BOOT_START, BOOT_SIZE = SLOTS["boot"]
LOG_SIZE = SLOTS["log"][1]

IMAGE_MAGIC = 0x96F3B83D
IMAGE_F_NON_BOOTABLE = 0x10
INFO_MAGIC = 0x4E49424D

# Update log record types and sources (as in shared/mboot/include/mboot_updatelog.h).
LOG_APP_CONFIRMED = 13
LOG_APP_UPGRADE_REQUESTED = 14
LOG_APP_DFU_REQUESTED = 15
SRC_APP = 3

REQ_MAGIC = 0x5152424D
RET_KEY = 0x70AD0000

failures = 0


def check(name, cond, detail=""):
    global failures
    if cond:
        print("ok", name)
    else:
        failures += 1
        print("FAIL", name + ":", detail)


def raises(name, exc, func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except exc as e:
        return e
    except Exception as e:
        print("FAIL", name + ": unexpected", repr(e))
        global failures
        failures += 1
        return None
    print("FAIL", name + ": no exception")
    failures += 1
    return None


def oserror(name, err, func, *args, **kwargs):
    e = raises(name, OSError, func, *args, **kwargs)
    if e is not None:
        check(name, e.errno == err, "errno %r, expected %r" % (e.errno, err))


def resets(name, func, *args):
    # A reset is reported by the fake port as SystemExit.
    before = ff.resets()
    raises(name + " raises SystemExit", SystemExit, func, *args)
    check(name + " reset counted", ff.resets() == before + 1, "resets %d" % ff.resets())


def pattern(n, seed=0):
    block = bytes((i * 7 + seed) & 0xFF for i in range(256))
    return (block * (n // 256 + 1))[:n]


def header(major, minor, rev, build, img_size=0x1000, flags=0, magic=IMAGE_MAGIC):
    return struct.pack(
        "<IIHHIIBBHII", magic, 0, 0x400, 0, img_size, flags, major, minor, rev, build, 0
    )


def image(version, size, seed=1):
    return header(*version, img_size=size - 0x400) + pattern(size - 32, seed)


def setup(version=(1, 0, 0, 0), confirmed=True):
    # Flash as left by programming an initial image with "--pad --confirm".
    ff.erase_all()
    ff.write(PRIMARY, header(*version))
    if confirmed:
        ff.boot_initial()
    mboot.state()


def write_image(img, chunks=(1000, 3, 255, 256, 257, 4000)):
    w = mboot.Writer()
    pos = 0
    i = 0
    while pos < len(img):
        n = chunks[i % len(chunks)]
        w.write(img[pos : pos + n])
        pos += n
        i += 1
    return w


def log_types():
    return [e[1] for e in mboot.log()]


def request_struct():
    ram = ff.request_ram()
    magic, version, mode, seq, elems_len, flags, crc = struct.unpack_from("<IHHIHHI", ram, 0)
    elems = ram[20 : 20 + elems_len]
    head = bytearray(ram[:20])
    head[16:20] = b"\0\0\0\0"
    good_crc = binascii.crc32(elems, binascii.crc32(bytes(head))) & 0xFFFFFFFF
    return magic, version, mode, seq, elems_len, flags, crc == good_crc, elems


# ---- constants and map ----

check(
    "swap constants",
    (
        mboot.SWAP_NONE,
        mboot.SWAP_TEST,
        mboot.SWAP_PERM,
        mboot.SWAP_REVERT,
        mboot.SWAP_FAIL,
    )
    == (1, 2, 3, 4, 5),
)
check("fs constants", (mboot.FS_FAT, mboot.FS_LFS2, mboot.FS_RAW) == (1, 3, 4))
check("slots shape", all(isinstance(s, tuple) and len(s) == 3 for s in mboot.slots()))
check(
    "slots addresses",
    PRIMARY == DEV_BASE + 0x10000 and SECONDARY > PRIMARY,
    "%x %x" % (PRIMARY, SECONDARY),
)
check("slots spare", SPARE == ERASE, "spare %d" % SPARE)

# ---- version() ----

setup()
check("version primary", mboot.version() == (1, 0, 0, 0))
check("version slot 0", mboot.version(0) == (1, 0, 0, 0))
check("version secondary empty", mboot.version(1) is None)
raises("version slot 2", ValueError, mboot.version, 2)
raises("version slot -1", ValueError, mboot.version, -1)
ff.write(UPDATE, header(3, 4, 5, 123456))
check("version secondary at update base", mboot.version(1) == (3, 4, 5, 123456), mboot.version(1))
ff.erase_all()
ff.write(PRIMARY, header(1, 2, 3, 4, flags=IMAGE_F_NON_BOOTABLE))
check("version non-bootable", mboot.version() is None)
ff.erase_all()
ff.write(PRIMARY, header(1, 2, 3, 4, magic=0x12345678))
check("version bad magic", mboot.version() is None)
ff.erase_all()
ff.write(SECONDARY, header(9, 9, 9, 9))
check("version secondary header in spare sector ignored", mboot.version(1) is None)

# ---- bootloader_info() ----

setup()
check("bootloader_info absent", mboot.bootloader_info() is None)
info_addr = BOOT_START + BOOT_SIZE - 64


def put_info(magic=INFO_MAGIC, info_version=1, layout=None, api=1, version=b"mpy-test 1.2"):
    if layout is None:
        layout = ff.layout_id()
    ff.erase(info_addr - info_addr % ERASE, ERASE)
    ff.write(
        info_addr,
        struct.pack("<IHHII24s24s", magic, info_version, 3, layout, api, version, b"\xff" * 24),
    )


put_info()
d = mboot.bootloader_info()
check(
    "bootloader_info", d == {"version": "mpy-test 1.2", "layout_id": ff.layout_id(), "api": 1}, d
)
put_info(magic=0)
check("bootloader_info bad magic", mboot.bootloader_info() is None)
put_info(info_version=2)
check("bootloader_info bad info_version", mboot.bootloader_info() is None)
put_info(version=b"x" * 24)
check("bootloader_info unterminated version", mboot.bootloader_info()["version"] == "x" * 23)

# ---- state() ----

setup()
s = mboot.state()
check(
    "state confirmed idle",
    s
    == {
        "swap": 1,
        "confirmed": True,
        "pending": False,
        "secondary_valid_header": False,
        "layout_mismatch": False,
    },
    s,
)
setup(confirmed=False)
s = mboot.state()
check(
    "state unconfirmed without trailer",
    not s["confirmed"] and not s["pending"] and s["swap"] == mboot.SWAP_NONE,
    s,
)

# ---- Writer: normal flow ----

setup()
img = image((2, 0, 0, 0), 10003)
ff.clear_counters()
w = write_image(img)
n = w.finish()
check("writer finish returns byte count", n == len(img), n)
padded = img + b"\xff" * (-len(img) % WRITE_UNIT)
check("writer data in flash", ff.read(UPDATE, len(padded)) == padded)
check("writer no overprogram", ff.counters()[3] == 0, ff.counters())
s = mboot.state()
check(
    "state after writer",
    s["swap"] == mboot.SWAP_TEST
    and s["pending"]
    and s["confirmed"]
    and s["secondary_valid_header"],
    s,
)
check("version secondary after writer", mboot.version(1) == (2, 0, 0, 0))
check("version primary after writer", mboot.version(0) == (1, 0, 0, 0))
entry = mboot.log(1)[0]
check(
    "log upgrade requested",
    entry[1] == LOG_APP_UPGRADE_REQUESTED
    and entry[2] == 0
    and entry[3] == SRC_APP
    and entry[4] == (2, 0, 0, 0),
    entry,
)
check("spare sector stays erased", ff.read(SECONDARY, ERASE) == b"\xff" * ERASE)

# permanent writer
setup()
w = mboot.Writer(permanent=True)
w.write(image((2, 1, 0, 0), 5000))
w.finish()
check("writer permanent", mboot.state()["swap"] == mboot.SWAP_PERM, mboot.state())

# stale state from an earlier update is removed when the Writer opens
setup()
ff.write(UPDATE, header(7, 7, 7, 7))
mboot.request_upgrade()
check("stale pending set", mboot.state()["pending"])
ff.write(SECONDARY, b"\x00" * 16)
w = mboot.Writer()
s = mboot.state()
check("writer open clears pending", not s["pending"] and s["swap"] == mboot.SWAP_NONE, s)
check("writer open erases spare sector", ff.read(SECONDARY, ERASE) == b"\xff" * ERASE)
check("writer open leaves update header for later erase", mboot.version(1) == (7, 7, 7, 7))

# sectors are erased as the write reaches them
setup()
ff.write(UPDATE, b"\x00" * (3 * ERASE))
w = mboot.Writer()
w.write(pattern(ERASE + 100))
w.abort()
check(
    "abort erases first sector only",
    ff.read(UPDATE, ERASE) == b"\xff" * ERASE
    and ff.read(UPDATE + 2 * ERASE, ERASE) == b"\x00" * ERASE,
)
setup()
ff.write(UPDATE, b"\x00" * (3 * ERASE))
w = mboot.Writer()
data = image((2, 0, 0, 0), ERASE + 100)
w.write(data)
w.finish()
check("erase ahead data", ff.read(UPDATE, len(data)) == data)
check(
    "erase ahead stops at the last sector used",
    ff.read(UPDATE + 2 * ERASE, ERASE) == b"\x00" * ERASE,
)
check("erase ahead no overprogram", ff.counters()[3] == 0)

# capacity
setup()
w = mboot.Writer()
full = image((2, 0, 0, 0), MAX_IMAGE)
w.write(full)
oserror("writer past capacity", errno.ENOSPC, w.write, b"x")
check("writer full image finish", w.finish() == MAX_IMAGE)
check(
    "writer full image data",
    ff.read(UPDATE, 4096) == full[:4096]
    and ff.read(UPDATE + MAX_IMAGE - 4096, 4096) == full[-4096:],
)
setup()
w = mboot.Writer()
w.write(b"\x00" * 100)
ff.clear_counters()
oserror("writer oversize single write", errno.ENOSPC, w.write, bytes(MAX_IMAGE))
check("writer oversize writes nothing", ff.counters()[1:3] == (0, 0), ff.counters())
w.abort()

# ---- Writer: lifecycle and errors ----

# Swap using offset only resumes the swap of an image larger than one erase unit: the writer
# refuses a smaller one at finish, erases what it wrote and marks nothing pending. The other modes
# take it.
setup()
w = mboot.Writer()
w.write(image((2, 0, 0, 0), ERASE))
if SPARE:
    oserror("finish of an image of one erase unit", errno.EINVAL, w.finish)
    check("one unit image is erased", ff.read(UPDATE, 32) == b"\xff" * 32)
    check("one unit image is not pending", not mboot.state()["pending"])
    oserror("write after a refused finish", errno.EBADF, w.write, b"x")
    setup()
    w = mboot.Writer()
    w.write(image((2, 0, 0, 0), ERASE + 1))
    check("finish of an image of one unit and a byte", w.finish() == ERASE + 1)
    check("image of one unit and a byte is pending", mboot.state()["pending"])
else:
    check("finish of an image of one erase unit", w.finish() == ERASE)
    check("one unit image is pending", mboot.state()["pending"])

setup()
w = mboot.Writer()
w.write(image((2, 0, 0, 0), ERASE + 1000))
w.abort()
check("abort erases header", ff.read(UPDATE, 32) == b"\xff" * 32)
check("abort leaves nothing pending", not mboot.state()["pending"])
oserror("write after abort", errno.EBADF, w.write, b"x")
oserror("finish after abort", errno.EBADF, w.finish)
w.abort()
w.abort()

setup()
w = mboot.Writer()
w.write(b"junk" * (ERASE // 4 + 100))
oserror("finish without image header", errno.EINVAL, w.finish)
check("no pending after failed finish", not mboot.state()["pending"])
oserror("write after failed finish", errno.EBADF, w.write, b"x")

setup()
w = mboot.Writer()
w.write(image((2, 0, 0, 0), ERASE + 1000))
w.finish()
oserror("write after finish", errno.EBADF, w.write, b"x")
oserror("finish twice", errno.EBADF, w.finish)
oserror("abort after finish", errno.EBADF, w.abort)
check("finished image still pending", mboot.state()["pending"])

setup()
w1 = mboot.Writer()
w2 = mboot.Writer()
oserror("older writer invalidated", errno.EBADF, w1.write, b"x")
w2.write(image((2, 0, 0, 0), ERASE + 1000))
check("newer writer usable", w2.finish() == ERASE + 1000)

# context manager
setup()
with mboot.Writer() as w:
    w.write(image((2, 2, 2, 2), ERASE + 1000))
check("with success finishes", mboot.state()["pending"] and mboot.version(1) == (2, 2, 2, 2))
setup()
with mboot.Writer() as w:
    w.write(image((2, 2, 2, 2), ERASE + 1000))
    w.finish()
check("with after explicit finish", mboot.state()["pending"])
setup()
try:
    with mboot.Writer() as w:
        w.write(image((2, 2, 2, 2), ERASE + 1000))
        raise KeyError("boom")
except KeyError:
    pass
check("with exception aborts", not mboot.state()["pending"] and mboot.version(1) is None)
oserror("with exception leaves writer closed", errno.EBADF, w.write, b"x")

# flash errors
setup()
w = mboot.Writer()
ff.inject(1, 1)
oserror("flash write error", errno.EIO, w.write, pattern(300))
oserror("write after flash error", errno.EIO, w.write, b"x")
oserror("finish after flash error", errno.EIO, w.finish)
w.abort()
check("abort after flash error", ff.read(UPDATE, 32) == b"\xff" * 32)
check("no pending after flash error", not mboot.state()["pending"])
setup()
ff.inject(2, 1)
oserror("flash erase error at open", errno.EIO, mboot.Writer)
setup()
ff.inject(2, 2)
oserror("flash erase error at open (spare sector)", errno.EIO, mboot.Writer)
setup()
w = mboot.Writer()
w.write(image((2, 0, 0, 0), ERASE + 100))
ff.inject(1, 1)
oserror("flash write error at finish", errno.EIO, w.finish)
check("no pending after failed flush", not mboot.state()["pending"])

# ---- request_upgrade ----

setup()
oserror("request_upgrade without image", errno.EINVAL, mboot.request_upgrade)
check("request_upgrade without image writes no trailer", not mboot.state()["pending"])
ff.write(UPDATE, header(2, 0, 0, 0))
mboot.request_upgrade()
s = mboot.state()
check(
    "request_upgrade test",
    s["swap"] == mboot.SWAP_TEST and s["pending"] and s["secondary_valid_header"],
    s,
)
check("request_upgrade does not reset", ff.resets() == 0)
setup()
ff.write(UPDATE, header(2, 0, 0, 0))
mboot.request_upgrade(True)
check("request_upgrade permanent", mboot.state()["swap"] == mboot.SWAP_PERM)
setup()
ff.write(UPDATE, header(2, 0, 0, 0))
mboot.request_upgrade(permanent=False)
check("request_upgrade keyword", mboot.state()["swap"] == mboot.SWAP_TEST)
setup()
ff.write(SECONDARY, header(2, 0, 0, 0))
oserror("request_upgrade header only in spare sector", errno.EINVAL, mboot.request_upgrade)
setup()
ff.write(UPDATE, header(2, 0, 0, 0, flags=IMAGE_F_NON_BOOTABLE))
oserror("request_upgrade non-bootable", errno.EINVAL, mboot.request_upgrade)
setup()
ff.write(UPDATE, header(2, 0, 0, 0))
ff.inject(1, 1)
oserror("request_upgrade flash error", errno.EIO, mboot.request_upgrade)

# ---- confirm ----

setup()
w = write_image(image((2, 0, 0, 0), 6000))
w.finish()
ff.boot_swap()
check(
    "boot_swap exchanged images",
    mboot.version(0) == (2, 0, 0, 0) and mboot.version(1) == (1, 0, 0, 0),
)
s = mboot.state()
check(
    "state after swap", s["swap"] == mboot.SWAP_REVERT and s["pending"] and not s["confirmed"], s
)
n_log = len(mboot.log())
mboot.confirm()
s = mboot.state()
check(
    "state after confirm",
    s["swap"] == mboot.SWAP_NONE and s["confirmed"] and not s["pending"],
    s,
)
entry = mboot.log(1)[0]
check(
    "log app confirmed",
    entry[1] == LOG_APP_CONFIRMED and entry[3] == SRC_APP and entry[4] == (2, 0, 0, 0),
    entry,
)
check("log grew by one", len(mboot.log()) == n_log + 1)
mboot.confirm()
check(
    "confirm idempotent state",
    mboot.state()["confirmed"] and mboot.state()["swap"] == mboot.SWAP_NONE,
)
check("confirm idempotent writes no log", len(mboot.log()) == n_log + 1)

setup()
w = write_image(image((2, 0, 0, 0), 6000))
w.finish()
ff.boot_swap()
ff.inject(1, 1)
oserror("confirm flash error", errno.EIO, mboot.confirm)
s = mboot.state()
check(
    "confirm flash error leaves unconfirmed",
    not s["confirmed"] and s["swap"] == mboot.SWAP_REVERT,
    s,
)
mboot.confirm()
check("confirm after error", mboot.state()["confirmed"])

setup()
mboot.confirm()
check("confirm on confirmed image", mboot.state()["confirmed"] and log_types() == [])

# ---- state() errors and layout mismatch ----

setup()
ff.inject(0, 2)
oserror("state flash error", errno.EIO, mboot.state)
ff.inject(None)
check("state recovers", mboot.state()["confirmed"])

setup()
put_info(layout=ff.layout_id() ^ 1)
s = mboot.state()
check("state layout mismatch", s["layout_mismatch"] is True, s)
oserror("writer layout mismatch", errno.ENODEV, mboot.Writer)
ff.write(UPDATE, header(2, 0, 0, 0))
oserror("request_upgrade layout mismatch", errno.ENODEV, mboot.request_upgrade)
check("layout mismatch writes no trailer", mboot.state()["swap"] == mboot.SWAP_NONE)
put_info()
check("state layout match", mboot.state()["layout_mismatch"] is False)
mboot.request_upgrade()
check("request_upgrade after layout match", mboot.state()["pending"])

# ---- log() ----

setup()
check("log empty", mboot.log() == [])
for i in range(3):
    ff.erase(SECONDARY, SLOTS["secondary"][1])
    ff.write(UPDATE, header(2, i, 0, 0))
    mboot.request_upgrade()
entries = mboot.log()
check("log count", len(entries) == 3, len(entries))
check("log newest first", [e[4][1] for e in entries] == [2, 1, 0], entries)
check("log seq increments", entries[0][0] == entries[1][0] + 1 == entries[2][0] + 2, entries)
check("log limit", mboot.log(2) == entries[:2])
check("log n larger than records", mboot.log(100) == entries)
check("log n=0", mboot.log(0) == [])
check("log n=None", mboot.log(None) == entries)
raises("log negative", ValueError, mboot.log, -1)
check("log record shape", all(len(e) == 6 and len(e[4]) == 4 for e in entries))

# the log keeps the newest records after the active unit fills up
setup()
per_unit = ERASE // 32
for i in range(per_unit + 5):
    ff.erase(SECONDARY, SLOTS["secondary"][1])
    ff.write(UPDATE, header(2, i & 0xFF, 0, 0))
    mboot.request_upgrade()
entries = mboot.log()
check(
    "log after unit switch keeps newest",
    entries[0][4][1] == (per_unit + 4) & 0xFF and len(entries) == 5 + per_unit,
    (len(entries), entries[0]),
)
check(
    "log after unit switch seq ordered",
    all(entries[i][0] == entries[i + 1][0] + 1 for i in range(len(entries) - 1)),
)

# ---- reset and request construction ----

setup()
ff.set_request_ram(b"\xaa" * 1024)
ff.retention(RET_KEY | 1)
resets("reset", mboot.reset)
check("reset clears request region", ff.request_ram()[:1008] == bytes(1008), ff.request_ram()[:16])
check("reset keeps the fault-injection bytes", ff.request_ram()[1008:] == b"\xaa" * 16)
check("reset clears retention", ff.retention() == 0)

setup()
resets("request_dfu", mboot.request_dfu)
magic, version, mode, seq, elems_len, flags, crc_ok, elems = request_struct()
check(
    "dfu request header",
    (magic, version, mode, elems_len, flags) == (REQ_MAGIC, 1, 1, 0, 0),
    (hex(magic), version, mode, elems_len, flags),
)
check("dfu request crc", crc_ok)
check(
    "dfu retention word",
    ff.retention() & 0xFFFFFF00 == RET_KEY and not ff.retention() & 0x80,
    hex(ff.retention()),
)
check(
    "log dfu requested",
    mboot.log(1)[0][1] == LOG_APP_DFU_REQUESTED and mboot.log(1)[0][3] == SRC_APP,
)

setup()
path = "/flash/update.bin"
resets("request_fsload", mboot.request_fsload, path, (mboot.FS_LFS2, 0x90090000, 0x20000))
magic, version, mode, seq, elems_len, flags, crc_ok, elems = request_struct()
expect = (
    b"\x02\x0a\x01\x03"
    + struct.pack("<II", 0x90090000, 0x20000)
    + bytes((3, 1 + len(path), 1))
    + path.encode()
    + b"\x01\x00"
)
check(
    "fsload request header",
    (magic, version, mode, flags) == (REQ_MAGIC, 1, 2, 0),
    (hex(magic), version, mode, flags),
)
check("fsload request elements", elems == expect, elems)
check("fsload request crc", crc_ok)
check(
    "fsload retention word",
    ff.retention() & 0xFFFFFF00 == RET_KEY and ff.retention() & 0x80,
    hex(ff.retention()),
)

setup()
resets(
    "request_fsload raw mount",
    mboot.request_fsload,
    b"x",
    (mboot.FS_RAW, 0x90000000, 0x1000, 0x90002000, 0x800),
)
magic, version, mode, seq, elems_len, flags, crc_ok, elems = request_struct()
expect = (
    b"\x02\x12\x01\x04"
    + struct.pack("<IIII", 0x90000000, 0x1000, 0x90002000, 0x800)
    + b"\x03\x02\x01x\x01\x00"
)
check("fsload raw mount elements", elems == expect and crc_ok, elems)

setup()
resets(
    "request_fsload lfs2 arg2",
    mboot.request_fsload,
    "a",
    [mboot.FS_LFS2, 0x90000000, 0x10000, 4096],
)
elems = request_struct()[7]
check(
    "fsload mount with block size",
    elems.startswith(b"\x02\x0e\x01\x03") and len(elems) == (2 + 14) + (2 + 1 + 1) + 2,
    elems,
)

# The default mount is the filesystem area of the flash map, of the type the board's
# bootloader reads (littlefs2 in this build).
setup()
resets("request_fsload default mount", mboot.request_fsload, "a")
fs_start, fs_size = SLOTS["fs"]
check(
    "fsload default mount is the fs area",
    request_struct()[7]
    == b"\x02\x0a\x01\x03" + struct.pack("<II", fs_start, fs_size) + b"\x03\x02\x01a\x01\x00",
    request_struct()[7],
)

setup()
ok_resets = ff.resets()
raises("fsload empty path", ValueError, mboot.request_fsload, "", (1, 0, 0))
raises("fsload long path", ValueError, mboot.request_fsload, "a" * 255, (1, 0, 0))
raises("fsload NUL in path", ValueError, mboot.request_fsload, "a\0b", (1, 0, 0))
raises("fsload non-ASCII path", ValueError, mboot.request_fsload, "x\u00e9", (1, 0, 0))
raises("fsload path type", TypeError, mboot.request_fsload, 5, (1, 0, 0))
raises("fsload short mount", ValueError, mboot.request_fsload, "a", (1, 0))
raises("fsload long mount", ValueError, mboot.request_fsload, "a", (1, 0, 0, 0, 0, 0))
raises("fsload lfs1 refused", ValueError, mboot.request_fsload, "a", (2, 0, 0x1000))
raises("fsload unknown fs", ValueError, mboot.request_fsload, "a", (9, 0, 0x1000))
raises("fsload value range", ValueError, mboot.request_fsload, "a", (1, 1 << 32, 0x1000))
check(
    "rejected fsload requests do not reset",
    ff.resets() == ok_resets and ff.request_ram() == bytes(1024),
)
resets("fsload path of 254 bytes", mboot.request_fsload, "a" * 254, (1, 0, 0x1000))
check(
    "fsload 254 byte path request",
    request_struct()[6] and len(request_struct()[7]) == 2 + 10 + 2 + 1 + 254 + 2,
)

# machine.bootloader() forms
setup()
resets("enter_bootloader no args", ff.enter_bootloader)
check("enter_bootloader no args is dfu", request_struct()[2] == 1)
setup()
resets("enter_bootloader False", ff.enter_bootloader, False)
check("enter_bootloader False is dfu", request_struct()[2] == 1)
setup()
stream = (
    b"\x02\x0a\x01\x01" + struct.pack("<II", 0x90000000, 0x4000) + b"\x03\x03\x01ab" + b"\x01\x00"
)
resets("enter_bootloader elements", ff.enter_bootloader, stream)
magic, version, mode, seq, elems_len, flags, crc_ok, elems = request_struct()
check(
    "enter_bootloader elements is fsload", mode == 2 and elems == stream and crc_ok, (mode, elems)
)
setup()
raises("enter_bootloader truncated stream", ValueError, ff.enter_bootloader, stream[:-1])
raises("enter_bootloader END with length", ValueError, ff.enter_bootloader, b"\x01\x01\x00")
raises(
    "enter_bootloader overrunning element", ValueError, ff.enter_bootloader, b"\x03\x05ab\x01\x00"
)
raises(
    "enter_bootloader too long",
    ValueError,
    ff.enter_bootloader,
    b"\x07\xfe"
    + bytes(254)
    + b"\x07\xfe"
    + bytes(254)
    + b"\x07\xfe"
    + bytes(254)
    + b"\x07\xfe"
    + bytes(254)
    + b"\x01\x00",
)
check(
    "enter_bootloader failures do not reset", ff.resets() == 0 and ff.request_ram() == bytes(1024)
)

print("failures", failures)
