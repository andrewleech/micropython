# Prerequisite: Requires the device instance (instance1) to have the
# usb-device-hid library installed. Install using mpremote connected to the device:
# mpremote mip install usb-device-hid
#
# tests/multi_usb/hid_mouse.py
# Assumes the device instance has 'usb.device.mouse' available (e.g., frozen).
import time
import machine

try:
    import uos as os
except ImportError:
    import os

# Global state for host IRQ
host_hid_irq_triggered = False
host_hid_report = None


# --- Instance 0: USB Host ---
def instance0():
    global host_hid_irq_triggered, host_hid_report
    host_hid_irq_triggered = False
    host_hid_report = None
    host = None
    hid = None

    try:
        # Host HID IRQ handler
        def on_hid_report(hid_dev, report):
            global host_hid_irq_triggered, host_hid_report
            # Mouse reports are often frequent, only print first one
            if not host_hid_irq_triggered:
                print(f"IRQ: HID report received ({len(report)} bytes): {report}")
                host_hid_report = report
                host_hid_irq_triggered = True

        # Initialize host
        host = machine.USBHost()
        host.active(True)
        print("Host active")

        # Synchronization point
        multitest.next()

        # Signal device
        multitest.broadcast("HOST_READY")
        print("Waiting for device")
        multitest.wait("DEVICE_READY")

        # Synchronization point
        multitest.next()

        # Wait for HID mouse device with proper polling
        hid = None
        print("Searching for HID mouse device...")
        deadline = time.ticks_add(time.ticks_ms(), 5000)  # 5 second timeout
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            hid_devices = host.hid_devices()
            if hid_devices:
                # Find the mouse
                for d in hid_devices:
                    if d.protocol == d.PROTOCOL_MOUSE:
                        hid = d
                        print(
                            f"HID mouse found: Proto={hid.protocol}, UsagePage={hid.usage_page}, Usage={hid.usage}"
                        )
                        break
                if hid:
                    break
            time.sleep_ms(200)

        if not hid:
            print("HID mouse not found - ABORTING")
            multitest.broadcast("HOST_ABORT")
            return

        # Register IRQ handler
        print("Registering HID IRQ handler")
        hid.irq(handler=on_hid_report, trigger=hid.IRQ_REPORT)
        print("HID IRQ handler registered")

        # Synchronization point
        multitest.next()

        # Signal device to send report
        multitest.broadcast("SEND_REPORT")
        print("Waiting for device report...")
        multitest.wait("REPORT_SENT")
        print("Device report supposedly sent.")

        # Wait for IRQ handler to trigger
        print("Waiting for HID IRQ...")
        deadline = time.ticks_add(time.ticks_ms(), 3000)  # 3 sec timeout
        while not host_hid_irq_triggered and time.ticks_diff(deadline, time.ticks_ms()) > 0:
            time.sleep_ms(50)

        if host_hid_irq_triggered:
            print(f"HID IRQ triggered successfully.")
            # Basic check for a mouse report (usually 3 or 4 bytes)
            if host_hid_report and (len(host_hid_report) == 3 or len(host_hid_report) == 4):
                # Check if movement occurred (bytes 1 and 2 should be non-zero)
                if len(host_hid_report) >= 3 and (
                    host_hid_report[1] != 0 or host_hid_report[2] != 0
                ):
                    print(f"Received report looks like mouse movement: {host_hid_report}")
                else:
                    print(
                        f"Received report looks like mouse, but no movement detected: {host_hid_report}"
                    )
            else:
                print(f"Received report has unexpected length for mouse: {host_hid_report}")
        else:
            print("TIMEOUT waiting for HID IRQ")

        # Unregister IRQ
        print("Unregistering HID IRQ handler")
        hid.irq(handler=None)

        print("Host finished")

    finally:
        # Cleanup resources
        if host:
            host.active(False)
            print("Host deactivated")


# --- Instance 1: USB Device ---
def instance1():
    mouse = None

    try:
        # Try importing the necessary HID mouse helper
        try:
            import usb.device.mouse

            print("usb.device.mouse imported successfully")
        except ImportError:
            print("SKIP: usb.device.mouse module not found.")
            multitest.skip()
            return  # Skip the test if the module is missing

        # Synchronization point
        multitest.next()

        # Wait for host
        print("Device waiting for HOST_READY")
        multitest.wait("HOST_READY")
        print("HOST_READY received")

        # Configure as HID Mouse
        print("Configuring as HID Mouse...")
        try:
            # The Mouse class likely handles USBDevice activation
            mouse = usb.device.mouse.Mouse()
            print("Mouse device configured")
        except Exception as e:
            print(f"Error configuring Mouse: {e}")
            print("SKIP: Failed to initialize Mouse device.")
            multitest.skip()
            return

        # Give host time for enumeration with polling
        deadline = time.ticks_add(time.ticks_ms(), 3000)  # 3 second timeout
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            time.sleep_ms(100)

        print("Broadcasting DEVICE_READY")
        multitest.broadcast("DEVICE_READY")

        # Synchronization point
        multitest.next()

        # Wait for signal to send report or abort
        print("Device waiting for SEND_REPORT")
        try:
            multitest.wait("SEND_REPORT")
            print("SEND_REPORT received")
        except:
            # Check if we got an abort signal
            try:
                multitest.wait("HOST_ABORT")
                print("HOST_ABORT received, device finishing.")
                return
            except:
                print("Timeout waiting for signals")
                return

        # Synchronization point
        multitest.next()

        # Send a mouse movement report (e.g., move right and down)
        move_x = 5
        move_y = 3
        print(f"Sending mouse movement report (x={move_x}, y={move_y})")
        try:
            mouse.move(move_x, move_y)
            print("Mouse move report sent")
            # Give time for report transmission
            time.sleep_ms(100)
            print("Broadcasting REPORT_SENT")
            multitest.broadcast("REPORT_SENT")
        except Exception as e:
            print(f"Error sending mouse report: {e}")
            print("Broadcasting REPORT_SENT despite error")
            multitest.broadcast("REPORT_SENT")

        print("Device finished")

    finally:
        # Clean up device (optional, depends on library)
        # if hasattr(mouse, 'close'): mouse.close()
        pass
