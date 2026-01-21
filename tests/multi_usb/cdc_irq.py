# tests/multi_usb/cdc_irq.py
#
# Test USB Host CDC with IRQ callbacks.
#
# Dependencies:
#   Instance 1 requires: usb.device.cdc (mip package)
#
# Install on device before running:
#   mpremote connect <device-serial-port> mip install usb-device-cdc
#
import time
import machine
from usbtest_util import wait_for_enum, setup_cdc_device

# Global state for IRQ handler on host
host_irq_triggered = False
host_irq_data = b""


# --- Instance 0: USB Host ---
def instance0():
    global host_irq_triggered, host_irq_data
    host_irq_triggered = False
    host_irq_data = b""

    # Host IRQ handler
    def on_rx(cdc_dev):
        global host_irq_triggered, host_irq_data
        available = cdc_dev.any()
        if available > 0:
            chunk = cdc_dev.read(available)
            host_irq_data += chunk
            print(f"IRQ: read {len(chunk)} bytes: {chunk}")
            host_irq_triggered = True

    host = machine.USBHost()
    host.active(True)
    print("Host active")

    multitest.next()
    multitest.broadcast("HOST_READY")
    multitest.wait("DEVICE_CONFIGURED")
    multitest.broadcast("HOST_ACTIVE")

    # Wait for CDC device to enumerate
    cdc = wait_for_enum(host, "cdc")
    if not cdc:
        print("CDC device not found")
        multitest.broadcast("HOST_ABORT")
        return
    print("CDC device found")

    multitest.wait("DEVICE_READY")

    # Register IRQ handler
    cdc.irq(handler=on_rx, trigger=cdc.IRQ_RX)
    print("IRQ handler registered")

    multitest.broadcast("SEND_DATA")
    multitest.wait("DATA_SENT")

    # Wait for IRQ handler to signal completion
    deadline = time.ticks_add(time.ticks_ms(), 3000)
    while not host_irq_triggered and time.ticks_diff(deadline, time.ticks_ms()) > 0:
        time.sleep_ms(50)

    if host_irq_triggered:
        print("IRQ triggered")
        print(f"Data received via IRQ: {host_irq_data}")
    else:
        print("TIMEOUT waiting for IRQ")

    cdc.irq(handler=None)
    print("Host finished")


# --- Instance 1: USB Device ---
def instance1():
    multitest.next()
    multitest.wait("HOST_READY")

    cdc_dev = setup_cdc_device()
    if cdc_dev is None:
        print("usb.device.cdc not available, skipping test")
        multitest.skip()
        return
    print("CDC device configured")

    multitest.broadcast("DEVICE_CONFIGURED")
    multitest.wait("HOST_ACTIVE")

    # Wait for USB host to enumerate the CDC device
    enumerated = False
    deadline = time.ticks_add(time.ticks_ms(), 3000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if cdc_dev.is_open():
            print("CDC device enumerated")
            enumerated = True
            break
        time.sleep_ms(100)

    if not enumerated:
        print("TIMEOUT: Device enumeration failed")
        return

    multitest.broadcast("DEVICE_READY")

    # Wait for host signal to send data, or abort signal
    try:
        multitest.wait("SEND_DATA")
    except Exception:
        try:
            multitest.wait("HOST_ABORT")
            return
        except Exception:
            return

    # Send data using proper CDC device API
    data_to_send = b"irq_test_data"
    try:
        bytes_sent = cdc_dev.write(data_to_send)
        print(f"CDC write returned: {bytes_sent} bytes")
        time.sleep_ms(200)
        multitest.broadcast("DATA_SENT")
    except Exception as e:
        print(f"Device write error: {e}")
        multitest.broadcast("DATA_SENT")
        return

    time.sleep_ms(500)
    print("Device finished")
