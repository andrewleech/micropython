# Test that GATTC operations handle mid-operation disconnection gracefully.
# Regression test: if the connection drops during service discovery, the
# central should get an error (not crash with TypeError from None conn_handle).

from micropython import const
import time, machine, bluetooth

TIMEOUT_MS = 5000

_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_PERIPHERAL_CONNECT = const(7)
_IRQ_PERIPHERAL_DISCONNECT = const(8)
_IRQ_GATTC_SERVICE_RESULT = const(9)
_IRQ_GATTC_SERVICE_DONE = const(10)

UUID_A = bluetooth.UUID(0x180D)
SERVICE_A = (
    UUID_A,
    (),
)
SERVICES = (SERVICE_A,)

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
    elif event == _IRQ_GATTC_SERVICE_RESULT:
        pass  # May or may not arrive before disconnect.
    elif event == _IRQ_GATTC_SERVICE_DONE:
        pass  # May arrive before or after disconnect depending on timing.

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
    ble.gatts_register_services(SERVICES)
    print("gap_advertise")
    ble.gap_advertise(20_000, b"\x02\x01\x06\x04\xffMPY")
    multitest.next()
    try:
        # Wait for central to connect.
        conn_handle = wait_for_event(_IRQ_CENTRAL_CONNECT, TIMEOUT_MS)

        # Wait for sync signal from central indicating discovery has started.
        multitest.wait("discover_started")

        # Disconnect while central is mid-discovery.
        print("gap_disconnect:", ble.gap_disconnect(conn_handle))
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

        # Start discovery then signal peripheral to disconnect us mid-operation.
        print("gattc_discover_services")
        ble.gattc_discover_services(conn_handle)
        multitest.broadcast("discover_started")

        # Wait for disconnection to happen.
        wait_for_event(_IRQ_PERIPHERAL_DISCONNECT, TIMEOUT_MS)

        # If we got here without crashing, the disconnect was handled gracefully.
        print("discovery_survived_disconnect")
    finally:
        ble.active(0)


ble = bluetooth.BLE()
ble.active(1)
ble.irq(irq)
