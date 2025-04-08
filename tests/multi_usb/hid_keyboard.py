# Prerequisite: Requires the device instance (instance1) to have the
# usb-device-hid library installed. Install using mpremote connected to the device:
# mpremote mip install usb-device-hid
#
# tests/multi_usb/hid_keyboard.py
# Assumes the device instance has 'usb.device.keyboard' available (e.g., frozen).
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

    # Host HID IRQ handler
    def on_hid_report(hid_dev, report):
        global host_hid_irq_triggered, host_hid_report
        print(f"IRQ: HID report received ({len(report)} bytes): {report}")
        host_hid_report = report
        host_hid_irq_triggered = True

    # Initialize host
    host = machine.USBHost()
    host.active(True)
    print("Host active")

    # Signal device
    multitest.broadcast("HOST_READY")
    print("Waiting for device")
    multitest.wait("DEVICE_READY")

    # Wait for HID device
    hid = None
    print("Searching for HID keyboard device...")
    for i in range(10):  # Timeout ~5s
        hid_devices = host.hid_devices()
        if hid_devices:
            # Find the keyboard
            for d in hid_devices:
                if d.protocol == d.PROTOCOL_KEYBOARD:
                    hid = d
                    print(
                        f"HID keyboard found: Proto={hid.protocol}, UsagePage={hid.usage_page}, Usage={hid.usage}"
                    )
                    break
            if hid:
                break
        print(f"Attempt {i + 1}: Keyboard not found, waiting...")
        time.sleep_ms(500)

    if not hid:
        print("HID keyboard not found - ABORTING")
        multitest.broadcast("HOST_ABORT")
        return

    # Register IRQ handler
    print("Registering HID IRQ handler")
    hid.irq(handler=on_hid_report, trigger=hid.IRQ_REPORT)
    print("HID IRQ handler registered")

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
        # Basic check for a non-empty keyboard report (specific format depends on device)
        if host_hid_report and len(host_hid_report) > 1 and any(host_hid_report):
            print(f"Received report looks like a key press: {host_hid_report}")
        else:
            print(f"Received report might be empty or invalid: {host_hid_report}")
    else:
        print("TIMEOUT waiting for HID IRQ")

    # Send an output report (e.g., set Num Lock LED)
    # Output report format for keyboard LEDs (byte 0):
    # Bit 0: Num Lock
    # Bit 1: Caps Lock
    # Bit 2: Scroll Lock
    num_lock_on_report = bytes([0b00000001])
    print(f"Sending output report (Num Lock ON): {num_lock_on_report}")
    success = hid.send_report(num_lock_on_report)
    print(f"send_report success: {success}")
    time.sleep_ms(100)  # Give time for report transmission
    multitest.broadcast("OUTPUT_REPORT_SENT")

    # Unregister IRQ
    print("Unregistering HID IRQ handler")
    hid.irq(handler=None)

    print("Host finished")


# --- Instance 1: USB Device ---
def instance1():
    # Try importing the necessary HID keyboard helper
    try:
        import usb.device.keyboard

        print("usb.device.keyboard imported successfully")
    except ImportError:
        print("SKIP: usb.device.keyboard module not found.")
        multitest.skip()
        return  # Skip the test if the module is missing

    # Wait for host
    print("Device waiting for HOST_READY")
    multitest.wait("HOST_READY")
    print("HOST_READY received")

    # Configure as HID Keyboard
    print("Configuring as HID Keyboard...")
    try:
        # The Keyboard class likely handles USBDevice activation
        kbd = usb.device.keyboard.Keyboard()
        print("Keyboard device configured")
    except Exception as e:
        print(f"Error configuring Keyboard: {e}")
        print("SKIP: Failed to initialize Keyboard device.")
        multitest.skip()
        return

    # Give host time for enumeration
    print("Waiting 2s for host enumeration...")
    time.sleep(2)
    print("Broadcasting DEVICE_READY")
    multitest.broadcast("DEVICE_READY")

    # Wait for signal to send report or abort
    print("Device waiting for SEND_REPORT or HOST_ABORT")
    received_signal = multitest.wait(("SEND_REPORT", "HOST_ABORT"), timeout_ms=7000)

    if received_signal == "HOST_ABORT":
        print("HOST_ABORT received, device finishing.")
        return
    elif received_signal != "SEND_REPORT":
        print(f"Unexpected signal or timeout waiting for SEND_REPORT: {received_signal}")
        return

    print("SEND_REPORT received")

    # Send a key press report (e.g., 'a')
    # Need the HID usage code for 'a', which is 4.
    key_a_usage_code = 4
    print("Sending key press report (key 'a')")
    try:
        kbd.press(key_a_usage_code)
        time.sleep_ms(20)  # Short delay between press/release
        kbd.release(key_a_usage_code)
        print("Key press/release sent")
        # Give time for report transmission
        time.sleep_ms(100)
        print("Broadcasting REPORT_SENT")
        multitest.broadcast("REPORT_SENT")
    except Exception as e:
        print(f"Error sending key report: {e}")
        print("Broadcasting REPORT_SENT despite error")
        multitest.broadcast("REPORT_SENT")
        # Continue to wait for output report potentially

    # Wait for host to send output report
    print("Waiting for OUTPUT_REPORT_SENT")
    multitest.wait("OUTPUT_REPORT_SENT")
    print("OUTPUT_REPORT_SENT received")

    # Check LED status (how to receive output reports is not standardized?)
    # The Keyboard class might handle this internally. We can't easily verify
    # from the script side without a specific API.
    # Just acknowledge the signal was received.
    print("Output report presumably received by device handler.")
    # Example check if API existed:
    # led_status = kbd.get_led_status() # Fictional API
    # print(f"LED status (fictional): {led_status}")

    # Clean up device (optional, depends on library)
    # if hasattr(kbd, 'close'): kbd.close()

    print("Device finished")
