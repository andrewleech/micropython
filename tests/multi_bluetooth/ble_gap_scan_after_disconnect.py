# Test that scanning works reliably after a connection is disconnected.
# Regression test: scan after disconnect could fail with EPERM if Zephyr
# had pending connection cleanup work items.

from micropython import const
import time, machine, bluetooth

TIMEOUT_MS = 5000

_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_SCAN_RESULT = const(5)
_IRQ_SCAN_DONE = const(6)
_IRQ_PERIPHERAL_CONNECT = const(7)
_IRQ_PERIPHERAL_DISCONNECT = const(8)

waiting_events = {}


def irq(event, data):
    if event == _IRQ_CENTRAL_CONNECT:
        print("_IRQ_CENTRAL_CONNECT")
        waiting_events[event] = data[0]
    elif event == _IRQ_CENTRAL_DISCONNECT:
        print("_IRQ_CENTRAL_DISCONNECT")
    elif event == _IRQ_PERIPHERAL_CONNECT:
        print("_IRQ_PERIPHERAL_CONNECT")
        waiting_events[event] = data[0]
    elif event == _IRQ_PERIPHERAL_DISCONNECT:
        print("_IRQ_PERIPHERAL_DISCONNECT")
    elif event == _IRQ_SCAN_RESULT:
        waiting_events[event] = True
    elif event == _IRQ_SCAN_DONE:
        waiting_events[event] = True

    if event not in waiting_events:
        waiting_events[event] = None


def wait_for_event(event, timeout_ms):
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
        if event in waiting_events:
            return waiting_events.pop(event)
        machine.idle()
    raise ValueError("Timeout waiting for {}".format(event))


# Acting in peripheral role.
def instance0():
    multitest.globals(BDADDR=ble.config("mac"))
    print("gap_advertise")
    ble.gap_advertise(20_000, b"\x02\x01\x06\x04\xffMPY")
    multitest.next()
    try:
        # First connection cycle.
        wait_for_event(_IRQ_CENTRAL_CONNECT, TIMEOUT_MS)
        wait_for_event(_IRQ_CENTRAL_DISCONNECT, TIMEOUT_MS)

        # Re-advertise so central can find us again via scan.
        print("gap_advertise")
        ble.gap_advertise(20_000, b"\x02\x01\x06\x04\xffMPY")

        # Wait for central to complete its scan and reconnect.
        wait_for_event(_IRQ_CENTRAL_CONNECT, TIMEOUT_MS)
        wait_for_event(_IRQ_CENTRAL_DISCONNECT, TIMEOUT_MS)
    finally:
        ble.active(0)


# Acting in central role.
def instance1():
    multitest.next()
    try:
        # Connect to peripheral.
        print("gap_connect")
        ble.gap_connect(*BDADDR)
        conn_handle = wait_for_event(_IRQ_PERIPHERAL_CONNECT, TIMEOUT_MS)

        # Disconnect.
        print("gap_disconnect:", ble.gap_disconnect(conn_handle))
        wait_for_event(_IRQ_PERIPHERAL_DISCONNECT, TIMEOUT_MS)

        # Immediately attempt to scan. This is the regression case: if pending
        # connection cleanup work items aren't drained, this fails with EPERM.
        print("gap_scan_start")
        ble.gap_scan(2000, 30000, 30000)
        wait_for_event(_IRQ_SCAN_RESULT, TIMEOUT_MS)
        print("scan_result_received")
        wait_for_event(_IRQ_SCAN_DONE, TIMEOUT_MS)
        print("scan_done")

        # Connect again to verify full recovery.
        print("gap_connect")
        ble.gap_connect(*BDADDR)
        conn_handle = wait_for_event(_IRQ_PERIPHERAL_CONNECT, TIMEOUT_MS)
        print("gap_disconnect:", ble.gap_disconnect(conn_handle))
        wait_for_event(_IRQ_PERIPHERAL_DISCONNECT, TIMEOUT_MS)
    finally:
        ble.active(0)


ble = bluetooth.BLE()
ble.active(1)
ble.irq(irq)
