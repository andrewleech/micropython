# tests/multi_usb/usbtest_util.py
#
# Shared helpers for USB host multi-tests.

import time


def wait_for_enum(host, device_type="cdc", timeout_ms=5000):
    """Poll for a USB host device. Returns device object or None."""
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if device_type == "cdc":
            devices = host.cdc_devices()
        elif device_type == "hid":
            devices = host.hid_devices()
        elif device_type == "msc":
            devices = host.msc_devices()
        elif device_type == "device":
            devices = host.devices()
        else:
            return None
        if devices:
            return devices[0]
        time.sleep_ms(200)
    return None


def setup_cdc_device():
    """Configure USB CDC device on instance1. Returns CDCInterface or None."""
    import sys

    if "/lib" not in sys.path or sys.path.index("/lib") > 0:
        if "/lib" in sys.path:
            sys.path.remove("/lib")
        sys.path.insert(0, "/lib")
    try:
        import usb.device
        from usb.device.cdc import CDCInterface

        cdc_dev = CDCInterface()
        usb.device.get().init(cdc_dev, builtin_driver=False)
        return cdc_dev
    except ImportError:
        return None


def setup_keyboard_device():
    """Configure USB keyboard device on instance1. Returns KeyboardInterface or None."""
    import sys

    if "/lib" not in sys.path or sys.path.index("/lib") > 0:
        if "/lib" in sys.path:
            sys.path.remove("/lib")
        sys.path.insert(0, "/lib")
    try:
        import usb.device
        from usb.device.keyboard import KeyboardInterface

        kbd = KeyboardInterface()
        usb.device.get().init(kbd)
        return kbd
    except ImportError:
        return None
