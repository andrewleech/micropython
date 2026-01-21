# tests/multi_usb/enum_multi_interface.py
#
# Test enumeration of multi-interface USB device (CDC + HID).
#
# Instance 0: USB Host (ESP32-S3 with USBHOST variant)
# Instance 1: USB Device (STM32 with TinyUSB CDC+HID)
#
# Dependencies:
#   Instance 1 requires: usb.device.cdc, usb.device.keyboard (mip packages)
#
# Install on device before running:
#   mpremote connect <device-serial-port> mip install usb-device-cdc usb-device-keyboard

import time
import machine


# --- Instance 0: USB Host ---
def instance0():
    host = machine.USBHost()
    host.active(True)
    print("Host active")

    multitest.next()
    multitest.broadcast("HOST_READY")
    multitest.wait("DEVICE_READY")

    # Wait for device enumeration
    deadline = time.ticks_add(time.ticks_ms(), 5000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        cdc_devices = host.cdc_devices()
        hid_devices = host.hid_devices()
        if cdc_devices and hid_devices:
            break
        time.sleep_ms(200)

    # Check for CDC device
    cdc_found = False
    cdc_devices = host.cdc_devices()
    if cdc_devices:
        cdc = cdc_devices[0]
        print(f"CDC is_connected: {cdc.is_connected()}")
        cdc_found = True
    else:
        print("ERROR: No CDC device found")

    # Check for HID device
    hid_found = False
    hid_devices = host.hid_devices()
    if hid_devices:
        hid = hid_devices[0]
        print(f"HID is_connected: {hid.is_connected()}")
        hid_found = True
    else:
        print("ERROR: No HID device found")

    if cdc_found and hid_found:
        print("Both CDC and HID found")
    else:
        print("FAILED to find both interfaces")

    multitest.broadcast("HOST_FINISHED")
    print("Host finished")


# --- Instance 1: USB Device ---
def instance1():
    multitest.next()
    multitest.wait("HOST_READY")

    # Configure as Composite CDC + HID (keyboard) device
    import sys

    if "/lib" not in sys.path or sys.path.index("/lib") > 0:
        if "/lib" in sys.path:
            sys.path.remove("/lib")
        sys.path.insert(0, "/lib")

    try:
        import usb.device
        from usb.device.cdc import CDCInterface
        from usb.device.keyboard import KeyboardInterface

        cdc = CDCInterface()
        kbd = KeyboardInterface()
        usb.device.get().init(cdc, kbd, builtin_driver=False)
        print("CDC+HID device configured")
    except ImportError as e:
        print(f"usb.device modules not available: {e}")
        multitest.skip()
        return

    multitest.broadcast("DEVICE_READY")

    multitest.wait("HOST_FINISHED")
    print("Device finished")
