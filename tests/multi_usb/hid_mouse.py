# Prerequisite: Requires the device instance (instance1) to have the
# usb-device-mouse library installed. Install using mpremote connected to the device:
# mpremote mip install usb-device-mouse
#
# tests/multi_usb/hid_mouse.py
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
        # Mouse reports are often frequent, only print first one
        if not host_hid_irq_triggered:
            report = hid_dev.get_report()
            if report:
                print(f"IRQ: HID report ({len(report)} bytes): {report}")
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
        if host_hid_report and (len(host_hid_report) == 3 or len(host_hid_report) == 4):
            if len(host_hid_report) >= 3 and (host_hid_report[1] != 0 or host_hid_report[2] != 0):
                print("Mouse movement detected")
            else:
                print("No movement detected in report")
        else:
            print(f"Unexpected report length: {len(host_hid_report) if host_hid_report else 0}")
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
        from usb.device.mouse import MouseInterface

        mouse = MouseInterface()
        usb.device.get().init(mouse)
        print("Mouse device configured")
    except ImportError:
        print("SKIP: usb.device.mouse module not found.")
        multitest.skip()
        return

    multitest.broadcast("DEVICE_CONFIGURED")
    multitest.wait("HOST_ACTIVE")

    # Wait for USB host to enumerate the HID device
    enumerated = False
    deadline = time.ticks_add(time.ticks_ms(), 3000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if mouse.is_open():
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

    # Send a mouse movement report
    move_x = 5
    move_y = 3
    try:
        mouse.move_by(move_x, move_y)
        print("Mouse report sent")
        time.sleep_ms(100)
        multitest.broadcast("REPORT_SENT")
    except Exception as e:
        print(f"Error sending mouse report: {e}")
        multitest.broadcast("REPORT_SENT")

    print("Device finished")
