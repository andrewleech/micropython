# tests/multi_usb/disconnect_cdc.py
#
# Tests host reaction to CDC device disconnect during operation.
# Uses proper device deactivation instead of machine.reset().
#
# Dependencies:
#   Instance 1 requires: usb.device.cdc (mip package)
#
# Install on device before running:
#   mpremote connect <device-serial-port> mip install usb-device-cdc

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

    print(f"Initial is_connected: {cdc.is_connected()}")

    # Signal device to send some data and then disconnect
    multitest.broadcast("SEND_AND_DISCONNECT")

    # Loop, trying to read and checking connection status
    data_received = b""
    disconnect_detected = False
    deadline = time.ticks_add(time.ticks_ms(), 8000)

    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if not cdc.is_connected():
            print("Disconnect detected")
            disconnect_detected = True
            break

        try:
            available = cdc.any()
            if available > 0:
                chunk = cdc.read(available)
                if chunk:
                    data_received += chunk
                else:
                    if not cdc.is_connected():
                        print("Disconnect detected")
                        disconnect_detected = True
                        break
        except Exception as e:
            print(f"Exception during read: {e}")
            if not cdc.is_connected():
                print("Disconnect detected")
                disconnect_detected = True
                break

        time.sleep_ms(100)

    if not disconnect_detected:
        print("TIMEOUT: Failed to detect disconnect")

    print(f"Final data received: {data_received}")
    print(f"Final is_connected: {cdc.is_connected()}")
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

    # Wait for host signal or abort
    try:
        multitest.wait("SEND_AND_DISCONNECT")
    except Exception:
        try:
            multitest.wait("HOST_ABORT")
            return
        except Exception:
            return

    # Send some data before disconnecting
    try:
        msg = b"before_disconnect"
        cdc_dev.write(msg)
        time.sleep_ms(100)
        print("Data sent")
    except Exception as e:
        print(f"Device write error: {e}")

    # Simulate disconnect by deactivating device
    time.sleep(1)
    print("Deactivating device")
    import usb.device

    usb.device.get().active(False)
    print("Device deactivated")
