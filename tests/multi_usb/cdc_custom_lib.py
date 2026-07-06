# Prerequisite: Requires the device instance (instance1) to have the
# usb-device-cdc library installed. Install using mpremote connected to the device:
# mpremote mip install usb-device-cdc
#
# tests/multi_usb/cdc_custom_lib.py
# Assumes device instance has 'usb.device.cdc' available (e.g., frozen).

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

    # --- Host Write -> Device Read ---
    host_msg = b"HostToDeviceCustom"
    bytes_written = cdc.write(host_msg)
    print(f"Bytes written: {bytes_written}")
    time.sleep_ms(100)
    multitest.broadcast("HOST_DATA_SENT")
    multitest.wait("DEVICE_READ_CONFIRMED")

    # --- Device Write -> Host Read ---
    multitest.broadcast("SEND_DEVICE_DATA")
    multitest.wait("DEVICE_DATA_SENT")

    data_from_device = b""
    deadline = time.ticks_add(time.ticks_ms(), 3000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        available = cdc.any()
        if available > 0:
            chunk = cdc.read(available)
            if chunk:
                data_from_device += chunk
                if len(data_from_device) >= 18:  # Length of b'DeviceToHostCustom'
                    break
        time.sleep_ms(50)

    expected_device_msg = b"DeviceToHostCustom"
    if data_from_device == expected_device_msg:
        print(f"Host received: {data_from_device}")
    else:
        print(f"ERROR: Got {data_from_device}, expected {expected_device_msg}")

    print("Host finished")


# --- Instance 1: USB Device ---
def instance1():
    multitest.next()
    multitest.wait("HOST_READY")

    cdc_dev = setup_cdc_device()
    if cdc_dev is None:
        print("SKIP: usb.device.cdc module not found.")
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

    # Wait for host data or abort
    try:
        multitest.wait("HOST_DATA_SENT")
    except Exception:
        try:
            multitest.wait("HOST_ABORT")
            return
        except Exception:
            return

    # --- Device Read <- Host Write ---
    data_from_host = b""
    try:
        chunk = cdc_dev.read(18)  # Length of b'HostToDeviceCustom'
        if chunk:
            data_from_host = bytes(chunk)

        expected_host_msg = b"HostToDeviceCustom"
        if data_from_host == expected_host_msg:
            print(f"Device received: {data_from_host}")
        else:
            print(f"ERROR: Got {data_from_host}, expected {expected_host_msg}")
    except Exception as e:
        print(f"Device read error: {e}")

    multitest.broadcast("DEVICE_READ_CONFIRMED")

    # --- Device Write -> Host Read ---
    multitest.wait("SEND_DEVICE_DATA")

    device_msg = b"DeviceToHostCustom"
    try:
        bytes_written = cdc_dev.write(device_msg)
        print(f"Device write returned: {bytes_written}")
        time.sleep_ms(100)
    except Exception as e:
        print(f"Device write error: {e}")

    multitest.broadcast("DEVICE_DATA_SENT")
    print("Device finished")
