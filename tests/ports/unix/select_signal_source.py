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
_MP_STREAM_GET_FILENO = const(10)
_MP_STREAM_SET_EVENT_SOURCE = const(13)

# Below this bound, a wait is presumed to have blocked to its deadline (or been woken)
# rather than repeatedly re-checking readiness; at or above it, the wait is presumed to
# have taken the period-capped sweep. The sweep case's own tests use much shorter waits
# (tens of ms at ~1ms cadence) and assert well above this bound, so there is no overlap.
IDLE_POLL_COUNT_MAX = 5

# Whether a main-thread poll() on a signal-only entry actually gets the deadline block
# poll_set_signal_wake_is_reliable() is meant to grant it, probed at runtime rather than
# assumed from build config: it is false unconditionally on a GIL build (see its doc
# comment in extmod/modselect.c), and possibly false for other reasons this test does not
# need to enumerate. A fd-less entry with nothing ready or signalling it takes the
# deadline block when the entitlement holds, so its poll_count() over a short wait stays
# at or below IDLE_POLL_COUNT_MAX; otherwise it takes the period-capped sweep instead and
# poll_count() comes back well above that bound.
_probe = SelectSignalStream()
_pp = select.poll()
_pp.register(_probe, select.POLLIN)
_pp.poll(80)
SIGNAL_DEADLINE_BLOCK = _probe.poll_count() <= IDLE_POLL_COUNT_MAX
_pp.unregister(_probe)


def pc_matches_block_cadence(pc):
    if SIGNAL_DEADLINE_BLOCK:
        return pc <= IDLE_POLL_COUNT_MAX
    return pc > IDLE_POLL_COUNT_MAX


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
if r == [] and 150 <= dt < 500 and pc_matches_block_cadence(pc):
    print("full timeout ok")
else:
    print("full timeout FAIL:", r, dt, pc)

# A raise from another thread (mp_event_signal(), via this stream's signal()) must end a
# blocking poll() promptly, well before its deadline, and without spinning while idle.
s = SelectSignalStream()
p = select.poll()
p.register(s, select.POLLIN)


def raise_after(ms, mask):
    time.sleep_ms(ms)
    s.signal(mask)


_thread.start_new_thread(raise_after, (50, select.POLLIN))
t0 = time.ticks_ms()
r = p.poll(2000)
dt = time.ticks_diff(time.ticks_ms(), t0)
pc = s.poll_count()
p.unregister(s)
if r and dt < 500 and pc_matches_block_cadence(pc):
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


def raise_after2(ms, mask):
    time.sleep_ms(ms)
    s.signal(mask)


_thread.start_new_thread(raise_after2, (50, select.POLLIN))
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


def raise_after3(ms, mask):
    time.sleep_ms(ms)
    s.signal(mask)


_thread.start_new_thread(raise_after3, (50, select.POLLIN))
t0 = time.ticks_ms()
r = p.poll(2000)
dt = time.ticks_diff(time.ticks_ms(), t0)
pc = s.poll_count()
p.unregister(s)
s.close()
if r and dt < 500 and pc_matches_block_cadence(pc):
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
if r == [] and 150 <= dt < 500 and drained == 0 and pc_matches_block_cadence(pc):
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

# poll() called from a worker thread cannot rely on the deadline-block gate:
# poll_set_signal_wake_is_reliable() only trusts the thread mp_sched_thread_can_run_
# callbacks() names as the wake event's owner, which on the default unix build
# (MICROPY_PY_THREAD=1, MICROPY_PY_THREAD_GIL=0) is the main thread only. A poll() from
# any other thread therefore always falls back to the period-capped sweep, even for a
# signal-only entry that would qualify for a deadline block if polled from the main
# thread. A signal raised mid-wait must still be found by that sweep, and the sweep's
# per-tick MP_STREAM_POLL calls give it a poll_count() far above the deadline-block cases
# above.
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
if r and dt < 500 and pc > IDLE_POLL_COUNT_MAX:
    print("worker thread sweep ok")
else:
    print("worker thread sweep FAIL:", r, dt, pc)
