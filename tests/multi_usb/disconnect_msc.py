# tests/multi_usb/disconnect_msc.py
#
# Tests host reaction to MSC device disconnect.
#
# Dependencies:
#   Instance 1 requires firmware built with MICROPY_HW_ENABLE_USB_RUNTIME_DEVICE=1
#   and USBDevice.BUILTIN_CDC_MSC support (not installable via mip).

import time
import machine
import os
import vfs
from usbtest_util import wait_for_enum

HOST_MOUNT_POINT = "/usb"


# --- Instance 0: USB Host ---
def instance0():
    host = machine.USBHost()
    host.active(True)
    print("Host active")

    multitest.next()
    multitest.broadcast("HOST_READY")
    multitest.wait("DEVICE_READY")

    # Wait for MSC device
    msc = wait_for_enum(host, "msc", timeout_ms=7500)
    if not msc:
        print("MSC device not found")
        multitest.broadcast("HOST_ABORT")
        return
    print("MSC device found")

    print(f"Initial is_connected: {msc.is_connected()}")

    # Mount the MSC device
    mounted = False
    try:
        try:
            os.rmdir(HOST_MOUNT_POINT)
        except OSError:
            pass
        try:
            os.mkdir(HOST_MOUNT_POINT)
        except OSError:
            pass
        try:
            vfs.umount(HOST_MOUNT_POINT)
        except OSError:
            pass
        vfs.mount(msc, HOST_MOUNT_POINT)
        print("Mount OK")
        mounted = True
    except Exception as e:
        print(f"Mount FAIL: {e}")
        multitest.broadcast("HOST_ABORT")

    # Signal device to disconnect only if mount was successful
    if mounted:
        multitest.broadcast("DISCONNECT_NOW")
    else:
        return

    # Loop, checking connection status
    disconnect_detected = False
    deadline = time.ticks_add(time.ticks_ms(), 8000)

    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if not msc.is_connected():
            print("Disconnect detected")
            disconnect_detected = True
            break
        time.sleep_ms(100)

    if not disconnect_detected:
        print("TIMEOUT: Failed to detect disconnect")

    print(f"Final is_connected: {msc.is_connected()}")

    # Attempt to unmount
    if mounted:
        try:
            vfs.umount(HOST_MOUNT_POINT)
            print("Unmount OK")
        except Exception as e:
            print(f"Unmount failed: {e}")

    print("Host finished")


# --- Instance 1: USB Device ---
def instance1():
    multitest.next()
    multitest.wait("HOST_READY")

    # Configure TinyUSB with CDC+MSC
    usbd = machine.USBDevice()
    usbd.builtin_driver = machine.USBDevice.BUILTIN_CDC_MSC
    usbd.active(True)
    print("TinyUSB MSC active")

    # Give host time for enumeration
    time.sleep(5)
    multitest.broadcast("DEVICE_READY")

    # Wait for host signal
    try:
        multitest.wait("DISCONNECT_NOW")
    except Exception:
        try:
            multitest.wait("HOST_ABORT")
            return
        except Exception:
            return

    # Simulate disconnect by deactivating USB device
    time.sleep(1)
    print("Deactivating device")
    usbd.active(False)
    print("Device deactivated")
