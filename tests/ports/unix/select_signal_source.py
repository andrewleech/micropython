# Test the select.poll() SOURCE_SIGNAL wake path (MP_STREAM_SET_EVENT_SOURCE), using
# SelectSignalStream, the synthetic stream defined in ports/unix/coverage.c that answers
# the ioctl itself, standing in for a real driver.

try:
    import time
    import select
    import _thread
    import io
    import micropython
    from micropython import const

    select.poll
    SelectSignalStream
except (ImportError, AttributeError, NameError):
    print("SKIP")
    raise SystemExit

_MP_STREAM_POLL = const(3)

# Below this bound, a wait is presumed to have blocked to its deadline (or been woken)
# rather than repeatedly re-checking readiness; at or above it, the wait is presumed to
# have taken the period-capped sweep. The sweep case's own tests use much shorter waits
# (tens of ms at ~1ms cadence) and assert well above this bound, so there is no overlap.
IDLE_POLL_COUNT_MAX = 5

# A signal-only entry gets the deadline block on every build this file runs on: the import
# guard above skips without _thread, and unix ties MICROPY_HAL_HAS_WAKE_OBJ to
# MICROPY_PY_THREAD, so a per-thread wake object always exists here and
# poll_set_signal_wake_is_reliable() grants the block on GIL and non-GIL alike. Asserted
# directly rather than probed: deriving the expected cadence by measuring it would let this
# file pass with the mechanism it is testing removed.


# Registering a fd-less stream installs its callback and counts the registration; the
# Unregister cadence (py/stream.h) reverses both when the entry leaves the poll set.
s = SelectSignalStream()
if s.last_op() is not None or s.arm_count() != 0:
    print("lifecycle FAIL: initial", s.last_op(), s.arm_count())
else:
    p = select.poll()
    p.register(s, select.POLLIN)
    reg_ok = s.last_op() == 0 and s.arm_count() == 1
    p.unregister(s)
    unreg_ok = s.last_op() == 2 and s.arm_count() == 0
    if reg_ok and unreg_ok:
        print("lifecycle ok")
    else:
        print("lifecycle FAIL:", reg_ok, unreg_ok)

# A stream declaring SOURCE_SIGNAL may be registered into more than one poll set while
# sharing one cb/ctx pair (the OP_UNREGISTER contract in py/stream.h), so it must count
# registrations and release them only once the count returns to zero.
s = SelectSignalStream()
p1 = select.poll()
p2 = select.poll()
p1.register(s, select.POLLIN)
p2.register(s, select.POLLIN)
both = s.arm_count() == 2
p1.unregister(s)
one = s.arm_count() == 1
p2.unregister(s)
zero = s.arm_count() == 0
if both and one and zero:
    print("shared arm count ok")
else:
    print("shared arm count FAIL:", both, one, zero)

# Readiness set before the wait begins is caught on the first pass, with no wake event
# needed: the deadline block must never be entered for readiness that already exists.
s = SelectSignalStream()
s.set_ready(select.POLLIN)
p = select.poll()
p.register(s, select.POLLIN)
t0 = time.ticks_ms()
r = p.poll(2000)
dt = time.ticks_diff(time.ticks_ms(), t0)
p.unregister(s)
if r and dt < 200:
    print("pending ready ok")
else:
    print("pending ready FAIL:", r, dt)

# A fd-less entry with nothing signalling it must still block for the full deadline, not
# return early nor spin: poll_count() bounds how many times its MP_STREAM_POLL ioctl was
# consulted, catching a busy-spin regression that a wall-clock timing check alone would
# miss (a spin that happens to still finish inside the timing window would pass a
# dt-only assertion).
s = SelectSignalStream()
p = select.poll()
p.register(s, select.POLLIN)
t0 = time.ticks_ms()
r = p.poll(150)
dt = time.ticks_diff(time.ticks_ms(), t0)
pc = s.poll_count()
p.unregister(s)
if r == [] and 150 <= dt < 500 and pc <= IDLE_POLL_COUNT_MAX:
    print("full timeout ok")
else:
    print("full timeout FAIL:", r, dt, pc)

# A raise from another thread (mp_event_signal(), via this stream's signal()) must end a
# blocking poll() promptly, well before its deadline, and without spinning while idle.
s = SelectSignalStream()
p = select.poll()
p.register(s, select.POLLIN)


# Takes the stream rather than reading the module global: the main thread rebinds that
# name several times below, and this runs on a worker after a delay.
def raise_after(stream, ms, mask):
    time.sleep_ms(ms)
    stream.signal(mask)


_thread.start_new_thread(raise_after, (s, 50, select.POLLIN))
t0 = time.ticks_ms()
r = p.poll(2000)
dt = time.ticks_diff(time.ticks_ms(), t0)
pc = s.poll_count()
p.unregister(s)
if r and dt < 500 and pc <= IDLE_POLL_COUNT_MAX:
    print("cross thread wake ok")
else:
    print("cross thread wake FAIL:", r, dt, pc)

# The callback installed at Register is a shared, global wake trigger, not state owned by
# whichever registration last set it: unregistering the stream from one poll set must not
# stop a signal from waking a different poll set that still has it registered.
s = SelectSignalStream()
p1 = select.poll()
p2 = select.poll()
p1.register(s, select.POLLIN)
p2.register(s, select.POLLIN)
p1.unregister(s)


_thread.start_new_thread(raise_after, (s, 50, select.POLLIN))
t0 = time.ticks_ms()
r = p2.poll(2000)
dt = time.ticks_diff(time.ticks_ms(), t0)
p2.unregister(s)
if r and dt < 500:
    print("unregister leaves sibling set ok")
else:
    print("unregister leaves sibling set FAIL:", r, dt)

# A signal-only entry alongside a real fd must not disturb the fd's own readiness, and
# must not itself report ready while its own ready mask is unset.
s = SelectSignalStream()
p = select.poll()
p.register(1, select.POLLOUT)  # stdout, always writable
p.register(s, select.POLLIN)
r = p.poll(0)
p.unregister(1)
p.unregister(s)
if len(r) == 1 and r[0][0] == 1:
    print("mixed set ok")
else:
    print("mixed set FAIL:", r)


# A registered entry with no file descriptor that also does not implement
# MP_STREAM_SET_EVENT_SOURCE at all declares no wake source (the same compatibility path
# as a stream written before SOURCE_SIGNAL existed).
class NoWakeSource(io.IOBase):
    def ioctl(self, cmd, arg):
        if cmd == _MP_STREAM_POLL:
            return 0
        return -1


# poll_set_all_have_wake_sources() is a property of the whole set, not of individual
# entries: one such entry forces the period-capped sweep for everything registered
# alongside it, even a signal-capable entry on the main thread that would otherwise
# qualify for a deadline block on its own (see "full timeout" above).
s = SelectSignalStream()
n = NoWakeSource()
p = select.poll()
p.register(s, select.POLLIN)
p.register(n, select.POLLIN)
t0 = time.ticks_ms()
r = p.poll(80)
dt = time.ticks_diff(time.ticks_ms(), t0)
pc = s.poll_count()
p.unregister(s)
p.unregister(n)
if r == [] and 80 <= dt < 300 and pc > IDLE_POLL_COUNT_MAX:
    print("bare entry forces sweep ok")
else:
    print("bare entry forces sweep FAIL:", r, dt, pc)

# A hybrid entry (its own fd plus SOURCE_SIGNAL) raised from another thread must wake
# promptly the same way a fd-less signal-only entry does, and without the fd (which stays
# empty throughout) causing it to spin while idle.
s = SelectSignalStream(True)
p = select.poll()
p.register(s, select.POLLIN)


_thread.start_new_thread(raise_after, (s, 50, select.POLLIN))
t0 = time.ticks_ms()
r = p.poll(2000)
dt = time.ticks_diff(time.ticks_ms(), t0)
pc = s.poll_count()
p.unregister(s)
s.close()
if r and dt < 500 and pc <= IDLE_POLL_COUNT_MAX:
    print("hybrid cross thread wake ok")
else:
    print("hybrid cross thread wake FAIL:", r, dt, pc)

# A raise whose wake-event token is drained by an intervening main-thread wait, between
# one poll() pass and the next, must still end the block. The token itself
# (mp_hal_wake_event_fd()) is a counting drain, not one this loop controls, so a
# scheduled callback's own time.sleep_ms() can take it before this loop's poll() call
# gets a chance to see it fire; the signal counter poll_set_take_signal() reads is the
# only record of the raise left once the token is gone, and only the pre-block ask's own
# `signalled` check (fed by the loop head's poll_set_take_signal() call, ahead of the
# next poll()) still consults that counter before this loop would otherwise block for the
# rest of the deadline.
s = SelectSignalStream()
p = select.poll()
p.register(s, select.POLLIN)


def drain_wake_event_mid_wait():
    time.sleep_ms(200)


def schedule_then_raise():
    time.sleep_ms(30)
    micropython.schedule(lambda _: drain_wake_event_mid_wait(), None)
    time.sleep_ms(30)
    s.signal(select.POLLIN)


_thread.start_new_thread(schedule_then_raise, ())
t0 = time.ticks_ms()
r = p.poll(2000)
dt = time.ticks_diff(time.ticks_ms(), t0)
p.unregister(s)
if r and dt < 500:
    print("signalled survives a drained token ok")
else:
    print("signalled survives a drained token FAIL:", r, dt)

# A hybrid entry (its own fd plus SOURCE_SIGNAL) wakes poll() when its fd fires, but its
# readiness answer still comes from its own ioctl, not from the fd's kernel revents: the
# stream never declared FD_IS_READINESS, so activity on the fd alone is not readiness.
# MP_STREAM_POLL answering not-ready drains the fd itself (the auto-drain a hybrid driver
# must perform per the wake-source invariant in py/stream.h), so by the time poll()
# returns there is nothing left for the caller's own drain_fd() to collect; poll_count()
# stays low because the drained fd then blocks for the rest of the deadline instead of
# waking the kernel poll() again on every remaining iteration.
s = SelectSignalStream(True)
p = select.poll()
p.register(s, select.POLLIN)
t0 = time.ticks_ms()
s.write_fd()
r = p.poll(150)
dt = time.ticks_diff(time.ticks_ms(), t0)
pc = s.poll_count()
drained = s.drain_fd()
p.unregister(s)
s.close()
if r == [] and 150 <= dt < 500 and drained == 0 and pc <= IDLE_POLL_COUNT_MAX:
    print("fd wake without ready ok")
else:
    print("fd wake without ready FAIL:", r, dt, drained, pc)

# The same hybrid entry reports ready once its own ready mask says so, ending the wait
# promptly and leaving its pipe drainable down to empty.
s = SelectSignalStream(True)
p = select.poll()
p.register(s, select.POLLIN)
t0 = time.ticks_ms()
s.write_fd()
s.set_ready(select.POLLIN)
r = p.poll(2000)
dt = time.ticks_diff(time.ticks_ms(), t0)
pc = s.poll_count()
first = s.drain_fd()
second = s.drain_fd()
p.unregister(s)
s.close()
if r and dt < 200 and first == 1 and second == 0 and pc <= IDLE_POLL_COUNT_MAX:
    print("fd wake with ready ok")
else:
    print("fd wake with ready FAIL:", r, dt, first, second, pc)

# poll() called from a worker thread gets the same deadline block as the main thread:
# each thread claims its own wake object on first blocking wait
# (mp_hal_wake_obj_this_thread(), ports/unix/unix_mphal.c), and poll_set_signal_wake_
# is_reliable() trusts that object unconditionally, since no other thread can claim or
# drain it. A signal raised mid-wait must still end the block promptly, with a
# poll_count() as low as the main-thread deadline-block cases above, not the sweep's
# per-tick cadence.
s = SelectSignalStream()
result = []


def poll_from_thread():
    p = select.poll()
    p.register(s, select.POLLIN)
    t0 = time.ticks_ms()
    r = p.poll(2000)
    dt = time.ticks_diff(time.ticks_ms(), t0)
    p.unregister(s)
    result.append((r, dt, s.poll_count()))


_thread.start_new_thread(poll_from_thread, ())
time.sleep_ms(50)
s.signal(select.POLLIN)
while not result:
    time.sleep_ms(10)
r, dt, pc = result[0]
if r and dt < 500 and pc <= IDLE_POLL_COUNT_MAX:
    print("worker thread wake ok")
else:
    print("worker thread wake FAIL:", r, dt, pc)

# _asyncio._ThreadSafeFlag (extmod/modasyncio.c) is the production consumer of the
# SOURCE_SIGNAL path this file otherwise exercises through the synthetic stream. It
# exposes no poll_count() of its own, so its declaration is checked by pairing it with
# SelectSignalStream in one poll() set: a single entry with no wake source drags the
# whole set down to the sweep cadence (see "bare entry forces sweep" above), so the
# probe's own poll_count staying at or below the deadline-block bound is only possible
# if the flag's entry also declared a real wake source.
try:
    import _asyncio

    _asyncio._ThreadSafeFlag
except (ImportError, AttributeError):
    print("SKIP")
    raise SystemExit

probe = SelectSignalStream()
flag = _asyncio._ThreadSafeFlag()
p = select.poll()
p.register(probe, select.POLLIN)
p.register(flag, select.POLLIN)
p.poll(80)
pc = probe.poll_count()
p.unregister(flag)
p.unregister(probe)
if pc <= IDLE_POLL_COUNT_MAX:
    print("threadsafeflag declares signal source ok")
else:
    print("threadsafeflag declares signal source FAIL:", pc)

# A set() called from another thread must reach the same registered callback as a
# same-thread set(), ending a blocking poll() promptly instead of only being found on
# the next period-capped sweep. The poll() stays on the main thread (paired with
# SelectSignalStream again, as above) rather than a worker thread, since only the main
# thread is entitled to the deadline block the poll_count bound below checks for
# (see poll_set_signal_wake_is_reliable() in extmod/modselect.c); what this test needs
# from the worker thread is only that set() called there reaches the callback safely,
# which the main thread's own poll_count() already reveals.
flag = _asyncio._ThreadSafeFlag()
probe = SelectSignalStream()
p = select.poll()
p.register(probe, select.POLLIN)
p.register(flag, select.POLLIN)


def set_after(ms):
    time.sleep_ms(ms)
    flag.set()


_thread.start_new_thread(set_after, (50,))
t0 = time.ticks_ms()
r = p.poll(2000)
dt = time.ticks_diff(time.ticks_ms(), t0)
pc = probe.poll_count()
p.unregister(flag)
p.unregister(probe)
if r and dt < 500 and pc <= IDLE_POLL_COUNT_MAX and flag.is_set():
    print("threadsafeflag cross thread wake ok")
else:
    print("threadsafeflag cross thread wake FAIL:", r, dt, pc)
