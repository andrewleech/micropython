# tests/multi_usb/disconnect_hid.py
#
# Tests host reaction to HID device disconnect during operation.
# Uses proper device deactivation instead of machine.reset().
#
# Dependencies:
#   Instance 1 requires: usb.device.keyboard (mip package)
#
# Install on device before running:
#   mpremote connect <device-serial-port> mip install usb-device-keyboard

import time
import machine
from usbtest_util import wait_for_enum, setup_keyboard_device


# --- Instance 0: USB Host ---
def instance0():
    host = machine.USBHost()
    host.active(True)
    print("Host active")

    multitest.next()
    multitest.broadcast("HOST_READY")
    multitest.wait("DEVICE_CONFIGURED")
    multitest.broadcast("HOST_ACTIVE")
    multitest.wait("DEVICE_READY")

    # Wait for HID device
    hid = wait_for_enum(host, "hid")
    if not hid:
        print("HID device not found")
        multitest.broadcast("HOST_ABORT")
        return
    print("HID device found")

    print(f"Initial is_connected: {hid.is_connected()}")

    # Signal device to disconnect
    multitest.broadcast("DISCONNECT_NOW")

    # Loop, checking connection status
    disconnect_detected = False
    deadline = time.ticks_add(time.ticks_ms(), 8000)

    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if not hid.is_connected():
            print("Disconnect detected")
            disconnect_detected = True
            break
        time.sleep_ms(100)

    if not disconnect_detected:
        print("TIMEOUT: Failed to detect disconnect")

    print(f"Final is_connected: {hid.is_connected()}")
    print("Host finished")


# --- Instance 1: USB Device ---
def instance1():
    multitest.next()
    multitest.wait("HOST_READY")

    kbd = setup_keyboard_device()
    if kbd is None:
        print("SKIP: usb.device.keyboard module not found.")
        multitest.skip()
        return
    print("Keyboard device configured")

    multitest.broadcast("DEVICE_CONFIGURED")
    multitest.wait("HOST_ACTIVE")

    # Wait for USB host to enumerate the HID device
    enumerated = False
    deadline = time.ticks_add(time.ticks_ms(), 3000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if kbd.is_open():
            print("HID device enumerated")
            enumerated = True
            break
        time.sleep_ms(100)

    if not enumerated:
        print("TIMEOUT: Device enumeration failed")
        return

    multitest.broadcast("DEVICE_READY")

    # Wait for host signal or abort
    try:
        multitest.wait("DISCONNECT_NOW")
    except Exception:
        try:
            multitest.wait("HOST_ABORT")
            return
        except Exception:
            return

    # Simulate disconnect by deactivating device
    time.sleep(1)
    print("Deactivating device")
    import usb.device

    usb.device.get().active(False)
    print("Device deactivated")
