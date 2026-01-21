# Prerequisite: Requires the device instance (instance1) to have the
# usb-device-keyboard library installed. Install using mpremote connected to the device:
# mpremote mip install usb-device-keyboard
#
# tests/multi_usb/hid_keyboard.py
import time
import machine
from usbtest_util import wait_for_enum

# Global state for host IRQ
host_hid_irq_triggered = False
host_hid_report = None


# --- Instance 0: USB Host ---
def instance0():
    global host_hid_irq_triggered, host_hid_report
    host_hid_irq_triggered = False
    host_hid_report = None

    # Host HID IRQ handler - receives HID device, need to fetch report
    def on_hid_report(hid_dev):
        global host_hid_irq_triggered, host_hid_report
        report = hid_dev.get_report()
        if report:
            print(f"IRQ: HID report ({len(report)} bytes): {report}")
            # Only save first non-empty report (key press, not release)
            if not host_hid_irq_triggered and any(report):
                host_hid_report = report
                host_hid_irq_triggered = True

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

    # Register IRQ handler
    hid.irq(handler=on_hid_report, trigger=hid.IRQ_REPORT)
    print("IRQ handler registered")

    multitest.broadcast("SEND_REPORT")
    multitest.wait("REPORT_SENT")

    # Wait for IRQ handler to trigger
    deadline = time.ticks_add(time.ticks_ms(), 3000)
    while not host_hid_irq_triggered and time.ticks_diff(deadline, time.ticks_ms()) > 0:
        time.sleep_ms(50)

    if host_hid_irq_triggered:
        print("HID IRQ triggered")
        if host_hid_report and len(host_hid_report) > 1 and any(host_hid_report):
            print("Key press detected")
        else:
            print(f"Report might be empty or invalid: {host_hid_report}")
    else:
        print("TIMEOUT waiting for HID IRQ")

    hid.irq(handler=None)
    print("Host finished")


# --- Instance 1: USB Device ---
def instance1():
    multitest.next()
    multitest.wait("HOST_READY")

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
        print("Keyboard device configured")
    except ImportError:
        print("SKIP: usb.device.keyboard module not found.")
        multitest.skip()
        return

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

    # Wait for signal to send report or abort
    try:
        multitest.wait("SEND_REPORT")
    except Exception:
        try:
            multitest.wait("HOST_ABORT")
            return
        except Exception:
            return

    # Send a key press report (e.g., 'a')
    # HID usage code for 'a' is 0x04
    try:
        kbd.send_keys([0x04])
        time.sleep_ms(20)
        kbd.send_keys([])  # Release all keys
        print("Key press sent")
        time.sleep_ms(100)
        multitest.broadcast("REPORT_SENT")
    except Exception as e:
        print(f"Error sending key report: {e}")
        multitest.broadcast("REPORT_SENT")

    print("Device finished")
