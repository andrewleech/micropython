# tests/multi_usb/enum_device_info.py
#
# Test USB Host device enumeration and property access.
#
# Instance 0: USB Host
# Instance 1: USB Device (CDC)
#
# Dependencies:
#   Instance 1 requires: usb.device.cdc (mip package)

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

    # Wait for device enumeration
    dev = wait_for_enum(host, "device")
    if not dev:
        print("No device found")
        return

    # Check devices() returns a tuple
    devices = host.devices()
    print(f"devices type: {type(devices).__name__}")
    print(f"devices count: {len(devices)}")

    # Check VID/PID are integers
    vid = dev.vid()
    pid = dev.pid()
    print(f"vid type: {type(vid).__name__}")
    print(f"pid type: {type(pid).__name__}")
    print(f"vid nonzero: {vid != 0}")
    print(f"pid nonzero: {pid != 0}")

    # Check string properties return str or None
    mfr = dev.manufacturer()
    prod = dev.product()
    serial = dev.serial()
    print(f"manufacturer type: {type(mfr).__name__}")
    print(f"product type: {type(prod).__name__}")
    print(f"serial type: {type(serial).__name__}")

    # Wait for CDC too
    cdc = wait_for_enum(host, "cdc")
    if cdc:
        print(f"cdc is_connected: {cdc.is_connected()}")
        # Verify CDC devices returns tuple
        cdc_devs = host.cdc_devices()
        print(f"cdc_devices type: {type(cdc_devs).__name__}")
    else:
        print("No CDC device found")

    # Check MSC and HID return empty tuples
    msc_devs = host.msc_devices()
    hid_devs = host.hid_devices()
    print(f"msc_devices empty: {len(msc_devs) == 0}")
    print(f"hid_devices empty: {len(hid_devs) == 0}")

    multitest.broadcast("HOST_FINISHED")
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
    multitest.wait("HOST_FINISHED")
    print("Device finished")
