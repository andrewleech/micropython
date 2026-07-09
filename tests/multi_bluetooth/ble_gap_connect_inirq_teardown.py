# Test that ble.active(0) called from *inside* a BLE connect callback tears the
# stack down cleanly and does not corrupt the connection pool.
#
# The central (instance1) calls ble.active(0) from within _IRQ_PERIPHERAL_CONNECT.
# The teardown must be deferred to top-level rather than run re-entrantly inside
# the work processor; running it re-entrantly double-unrefs the connection (a
# refcount underflow that panics, or leaves the pool slot permanently allocated).
# After re-init, the central reconnects to the same peer: if the slot leaked this
# raises OSError(EINVAL) "conn exists".

from micropython import const
import time, machine, bluetooth

TIMEOUT_MS = 5000

_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_PERIPHERAL_CONNECT = const(7)
_IRQ_PERIPHERAL_DISCONNECT = const(8)

waiting_events = {}
trigger_teardown = False


def irq(event, data):
    if event == _IRQ_PERIPHERAL_CONNECT:
        print("_IRQ_PERIPHERAL_CONNECT")
        if trigger_teardown:
            # The trigger: synchronous deinit from inside the connect callback.
            print("in-IRQ active(0)")
            ble.active(0)
        else:
            waiting_events[event] = data[0]
    elif event == _IRQ_PERIPHERAL_DISCONNECT:
        print("_IRQ_PERIPHERAL_DISCONNECT")
        waiting_events[event] = True
    elif event == _IRQ_CENTRAL_CONNECT:
        print("_IRQ_CENTRAL_CONNECT")
        waiting_events[event] = data[0]
    elif event == _IRQ_CENTRAL_DISCONNECT:
        print("_IRQ_CENTRAL_DISCONNECT")
        waiting_events[event] = True


def wait_for_event(event, timeout_ms):
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
        if event in waiting_events:
            return waiting_events.pop(event)
        machine.idle()
    raise ValueError("Timeout waiting for {}".format(event))


def wait_inactive(timeout_ms):
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
        if not ble.active():
            return
        machine.idle()


# Peripheral role: connectable, tolerate an ungraceful teardown, re-advertise.
def instance0():
    multitest.globals(BDADDR=ble.config("mac"))
    print("gap_advertise")
    ble.gap_advertise(20_000, b"\x02\x01\x06\x04\xffMPY")
    multitest.next()
    try:
        wait_for_event(_IRQ_CENTRAL_CONNECT, TIMEOUT_MS)
        wait_for_event(_IRQ_CENTRAL_DISCONNECT, TIMEOUT_MS)
        # Re-advertise, then release the central to reconnect.
        print("gap_advertise")
        ble.gap_advertise(20_000, b"\x02\x01\x06\x04\xffMPY")
        multitest.broadcast("readvertised")
        wait_for_event(_IRQ_CENTRAL_CONNECT, TIMEOUT_MS)
        wait_for_event(_IRQ_CENTRAL_DISCONNECT, TIMEOUT_MS)
    finally:
        ble.active(0)


# Central role (DUT): in-IRQ teardown, then reconnect to the same peer.
def instance1():
    global trigger_teardown
    multitest.next()
    try:
        # Connect, tearing the stack down from inside the connect callback.
        trigger_teardown = True
        print("gap_connect")
        ble.gap_connect(*BDADDR)
        # The deferred teardown runs at top-level from the poll loop.
        wait_inactive(TIMEOUT_MS)
        trigger_teardown = False

        # Wait for the peer to re-advertise before reconnecting.
        multitest.wait("readvertised")

        # Re-init and reconnect to the same peer. A leaked pool slot would make
        # this raise OSError(EINVAL).
        ble.active(1)
        ble.irq(irq)
        print("gap_connect")
        try:
            ble.gap_connect(*BDADDR)
            conn_handle = wait_for_event(_IRQ_PERIPHERAL_CONNECT, TIMEOUT_MS)
            print("reconnect ok")
            print("gap_disconnect:", ble.gap_disconnect(conn_handle))
            wait_for_event(_IRQ_PERIPHERAL_DISCONNECT, TIMEOUT_MS)
        except OSError as e:
            print("reconnect failed OSError", e.errno)
    finally:
        ble.active(0)


ble = bluetooth.BLE()
ble.active(1)
ble.irq(irq)
