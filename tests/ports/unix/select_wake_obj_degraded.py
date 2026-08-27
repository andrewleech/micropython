# Test what select.poll() does for a thread the port could not give a wake object to.
#
# A claim that cannot be serviced hands back a valid but backing-less object rather than
# failing, so most callers degrade through code that already exists. The exception is an
# indefinite wait holding SOURCE_SIGNAL entries: nothing is left that could end it, so it
# raises instead of blocking forever.
#
# Runs in its own file because a wake object is returned rather than closed when a thread
# ends: a worker started by another test in the same process would leave a node behind for
# this one to reuse, and the claim would succeed without ever reaching the failure below.

try:
    import time
    import select
    import _thread

    select.poll
    SelectSignalStream
    select_force_wake_obj_failure
except (ImportError, AttributeError, NameError):
    print("SKIP")
    raise SystemExit

# Spelled out because MicroPython's errno module carries a subset that excludes it, while
# the raise itself uses MP_EMFILE (py/mperrno.h). 24 on both platforms this port runs on.
EMFILE = 24

# Only a worker thread can reach this: the main thread claims its object during HAL
# init, before any of this could ask the port to start failing.
results = {}
done = _thread.allocate_lock()


def worker():
    stream = SelectSignalStream()
    poller = select.poll()
    poller.register(stream, select.POLLIN)

    # Indefinite, with a registered signal source and no object able to carry a raise to
    # this thread. If the raise below is ever lost this hangs rather than fails, and the
    # test harness timeout is what reports it.
    try:
        poller.poll()
        results["indefinite"] = "returned"
    except OSError as er:
        results["indefinite"] = er.errno
    except BaseException as er:
        results["indefinite"] = repr(er)

    # The same set with a timeout the caller chose degrades silently instead, because that
    # timeout still ends the wait: nothing here can hang, so nothing needs to raise.
    try:
        t0 = time.ticks_ms()
        ready = poller.poll(120)
        elapsed = time.ticks_diff(time.ticks_ms(), t0)
        results["finite"] = (ready, elapsed >= 100)
    except BaseException as er:
        results["finite"] = repr(er)

    poller.unregister(stream)
    done.release()


done.acquire()
select_force_wake_obj_failure(True)
_thread.start_new_thread(worker, ())
done.acquire()
select_force_wake_obj_failure(False)

if results.get("indefinite") == EMFILE:
    print("degraded indefinite raises ok")
else:
    print("degraded indefinite raises FAIL:", results.get("indefinite"))

if results.get("finite") == ([], True):
    print("degraded finite still returns ok")
else:
    print("degraded finite still returns FAIL:", results.get("finite"))

# A set with no signal source never depended on a raise, so it is unaffected: the port is
# still failing every claim here, and a plain timeout must still be honoured.
t0 = time.ticks_ms()
empty = select.poll()
ready = empty.poll(60)
if ready == [] and time.ticks_diff(time.ticks_ms(), t0) >= 50:
    print("degraded without signal sources ok")
else:
    print("degraded without signal sources FAIL:", ready)
