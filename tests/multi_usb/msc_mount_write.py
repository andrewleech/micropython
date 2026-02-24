# tests/multi_usb/msc_mount_write.py
#
# Test MSC filesystem mount, file write, and read-back verification.
#
# Instance 0: USB Host
# Instance 1: USB Device with MSC support
#
# Dependencies:
#   Instance 1 requires firmware built with MICROPY_HW_ENABLE_USB_RUNTIME_DEVICE=1
#   and USBDevice.BUILTIN_CDC_MSC support (not installable via mip).

import time
import os
import vfs
from usbtest_util import wait_for_enum

MOUNT_POINT = "/usb"
TEST_FILE = MOUNT_POINT + "/test.txt"
TEST_DATA = "MicroPython MSC Test Data"


# --- Instance 0: USB Host ---
def instance0():
    import machine

    host = machine.USBHost()
    host.active(True)
    print("Host active")

    multitest.next()
    multitest.broadcast("HOST_READY")
    multitest.wait("DEVICE_READY")

    # Wait for MSC device
    msc = wait_for_enum(host, "msc")
    if not msc:
        print("NO MSC DEVICE")
        multitest.broadcast("DONE")
        return
    print("Found MSC device")

    # Mount
    print("Mounting...")
    try:
        try:
            os.mkdir(MOUNT_POINT)
        except OSError:
            pass
        try:
            vfs.umount(MOUNT_POINT)
        except OSError:
            pass
        vfs.mount(msc, MOUNT_POINT)
        print("Mount OK")
    except Exception as e:
        print(f"Mount FAIL: {e}")
        multitest.broadcast("DONE")
        return

    # Write file
    print("Writing...")
    try:
        with open(TEST_FILE, "w") as f:
            f.write(TEST_DATA)
        print("Write OK")
    except Exception as e:
        print(f"Write FAIL: {e}")
        vfs.umount(MOUNT_POINT)
        multitest.broadcast("DONE")
        return

    # Read back
    print("Reading...")
    try:
        with open(TEST_FILE, "r") as f:
            content = f.read()
        if content == TEST_DATA:
            print("Verify OK")
        else:
            print("Verify FAIL")
    except Exception as e:
        print(f"Read FAIL: {e}")

    # Unmount
    print("Unmounting...")
    try:
        vfs.umount(MOUNT_POINT)
        print("Unmount OK")
    except Exception as e:
        print(f"Unmount FAIL: {e}")

    multitest.broadcast("DONE")
    print("Host done")


# --- Instance 1: USB Device ---
def instance1():
    import machine

    multitest.next()
    multitest.wait("HOST_READY")

    # Configure TinyUSB with CDC+MSC
    usbd = machine.USBDevice()
    usbd.builtin_driver = machine.USBDevice.BUILTIN_CDC_MSC
    usbd.active(True)
    print("TinyUSB MSC active")

    multitest.broadcast("DEVICE_READY")
    multitest.wait("DONE")
    print("Device done")
