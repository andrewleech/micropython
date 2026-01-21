# tests/multi_usb/error_handling.py
#
# Test USB Host error handling and edge cases.
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

    # Empty device lists before activation
    print(f"devices before active: {len(host.devices())}")
    print(f"cdc before active: {len(host.cdc_devices())}")
    print(f"msc before active: {len(host.msc_devices())}")
    print(f"hid before active: {len(host.hid_devices())}")

    host.active(True)
    print("Host active")

    multitest.next()
    multitest.broadcast("HOST_READY")
    multitest.wait("DEVICE_CONFIGURED")

    # Wait for CDC device
    cdc = wait_for_enum(host, "cdc")
    if not cdc:
        print("CDC device not found")
        return
    print("CDC device found")

    # Read with no data available should return empty bytes
    data = cdc.read(64)
    print(f"read no data: {data}")

    # any() should be 0
    print(f"any no data: {cdc.any()}")

    # Write should work
    written = cdc.write(b"test")
    print(f"write ok: {written > 0}")

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
