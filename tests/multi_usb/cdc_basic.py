# tests/multi_usb/cdc_basic.py
#
# Test USB Host CDC communication between two MicroPython boards.
#
# Hardware setup:
#   - Instance 0: USB Host (e.g., STM32F4 with USB Host support)
#   - Instance 1: USB Device (e.g., ESP32-S3 with native USB)
#   - Native USB ports connected together via USB cable
#
# Dependencies:
#   Instance 0 requires: machine.USBHost (built-in)
#   Instance 1 requires: usb.device.cdc (mip package)
#
# Install dependencies on instance 1 (USB Device) before running:
#   mpremote connect <device-serial-port> mip install usb-device-cdc
#
import time
import machine
from usbtest_util import wait_for_enum, setup_cdc_device


# --- Instance 0: USB Host ---
def instance0():
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

    print(f"is_connected: {cdc.is_connected()}")
    print(f"any (before recv): {cdc.any()}")

    # Signal device to send data
    multitest.broadcast("SEND_DATA")
    multitest.wait("DATA_SENT")

    # Read data with timeout
    data = b""
    deadline = time.ticks_add(time.ticks_ms(), 3000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        available = cdc.any()
        if available > 0:
            chunk = cdc.read(available)
            if chunk:
                data += chunk
                if b"hello" in data:
                    break
        else:
            time.sleep_ms(50)

    if not data:
        print("TIMEOUT waiting for data from device")
    else:
        print(f"Received from device: {data}")

    # Send data back to device
    bytes_written = cdc.write(b"world")
    if bytes_written != 5:
        print(f"FAIL: Expected 5 bytes written, got {bytes_written}")
    print(f"Bytes written: {bytes_written}")
    time.sleep_ms(100)
    multitest.broadcast("DATA_SENT_BACK")

    time.sleep_ms(500)
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

    # Wait for host signal to send data or abort
    try:
        multitest.wait("SEND_DATA")
    except Exception:
        try:
            multitest.wait("HOST_ABORT")
            return
        except Exception:
            return

    # Send data to host
    try:
        bytes_sent = cdc_dev.write(b"hello")
        if bytes_sent != 5:
            print(f"FAIL: Expected 5 bytes sent, got {bytes_sent}")
        print(f"CDC write returned: {bytes_sent} bytes")
        time.sleep_ms(100)
        multitest.broadcast("DATA_SENT")
    except Exception as e:
        print(f"Device write error: {e}")
        multitest.broadcast("DATA_SENT")
        return

    multitest.wait("DATA_SENT_BACK")

    # Read data from host
    data = b""
    try:
        data = cdc_dev.read(5)
        if data:
            data = bytes(data)
            if b"world" in data:
                print(f"Received from host: {data}")
            else:
                print(f"Did not receive 'world' (Received: {data})")
        else:
            print("TIMEOUT waiting for data from host")
    except Exception as e:
        print(f"Device read error: {e}")

    print("Device finished")
