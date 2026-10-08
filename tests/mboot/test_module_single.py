# Tests for the mboot module with the single slot policy on the unix port, built with the file
# backed fake flash (ports/unix VARIANT=mboot_single). Run with:
#   cd tests && MICROPY_MICROPYTHON=<build>/micropython ./run-tests.py mboot/test_module_single.py
#
# With one slot there is no update slot and no swap: confirm() does nothing, state() is fixed,
# and the calls that need an update slot raise OSError(EPERM). The update log, the request
# handoff and the bootloader info work as with the other policies.
#
# The expected output (test_module_single.py.exp) is a list of "ok <name>" lines; a failing check
# prints "FAIL <name>: <detail>" instead.

try:
    import mboot
    import mboot_fake as ff
except ImportError:
    print("SKIP")
    raise SystemExit

import struct


class errno:
    EPERM = 1
    EINVAL = 22


DEV_BASE, DEV_SIZE, ERASE, WRITE_UNIT, ERASED, MAX_IMAGE = ff.geometry()
SLOTS = {name: (start, size) for name, start, size in mboot.slots()}
PRIMARY = SLOTS["primary"][0]

IMAGE_MAGIC = 0x96F3B83D
LOG_APP_CONFIRMED = 13
LOG_APP_UPGRADE_REQUESTED = 14

failures = 0


def check(name, cond, detail=""):
    global failures
    if cond:
        print("ok", name)
    else:
        failures += 1
        print("FAIL", name + ":", detail)


def oserror(name, err, func, *args):
    try:
        func(*args)
    except OSError as e:
        check(name, e.args[0] == err, "errno %r" % (e.args,))
        return
    except Exception as e:
        check(name, False, "unexpected " + repr(e))
        return
    check(name, False, "no exception")


def header(major, minor, rev, build):
    return struct.pack(
        "<IIHHIIBBHII", IMAGE_MAGIC, 0, 0x400, 0, 0x1000, 0, major, minor, rev, build, 0
    )


def setup():
    ff.erase_all()
    ff.write(PRIMARY, header(1, 0, 0, 0))
    ff.boot_initial()


setup()

names = sorted(SLOTS)
check(
    "slots has no secondary and no scratch",
    "secondary" not in SLOTS and "scratch" not in SLOTS,
    names,
)
check(
    "slots of the single slot layout",
    names == ["boot", "fs", "intent", "log", "primary", "seccnt"],
    names,
)
check("intent area is one erase unit", SLOTS["intent"][1] == ERASE, SLOTS["intent"])
check("primary slot is not sized for a spare unit", SLOTS["primary"][1] % ERASE == 0)

check("version primary", mboot.version() == (1, 0, 0, 0))
check("version update slot", mboot.version(1) is None)

s = mboot.state()
check(
    "state is fixed",
    (s["swap"], s["confirmed"], s["pending"]) == (mboot.SWAP_NONE, True, False),
    s,
)
check("state layout matches", not s["layout_mismatch"], s)

# confirm() does nothing: no flash write, no log record.
ff.clear_counters()
mboot.confirm()
mboot.confirm()
check("confirm does nothing", ff.counters()[1:3] == (0, 0), ff.counters())
check("confirm adds no log record", LOG_APP_CONFIRMED not in [e[1] for e in mboot.log()])

oserror("request_upgrade needs an update slot", errno.EPERM, mboot.request_upgrade)
oserror("request_upgrade permanent needs an update slot", errno.EPERM, mboot.request_upgrade, True)
oserror("Writer needs an update slot", errno.EPERM, mboot.Writer)
oserror("Writer permanent needs an update slot", errno.EPERM, mboot.Writer, True)
check(
    "a refused upgrade is not logged",
    LOG_APP_UPGRADE_REQUESTED not in [e[1] for e in mboot.log()],
)
check("refused calls write nothing", ff.counters()[1:3] == (0, 0), ff.counters())

# request_fsload works: the bootloader reads the file and writes the only slot.
before = ff.resets()
try:
    mboot.request_fsload("update.bin")
except SystemExit:
    pass
check("request_fsload resets", ff.resets() == before + 1)
ram = ff.request_ram()
magic, version, mode = struct.unpack_from("<IHH", ram, 0)
check("request_fsload mode is fsload", mode == 2, mode)

before = ff.resets()
try:
    mboot.request_dfu()
except SystemExit:
    pass
check("request_dfu resets", ff.resets() == before + 1)

print("done" if failures == 0 else "failed")
