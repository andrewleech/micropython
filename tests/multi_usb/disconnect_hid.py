# tests/multi_usb/disconnect_hid.py
# Tests host reaction to HID device disconnect.
# Assumes device instance has 'usb.device.keyboard' available.

import time
import machine

try:
    import uos as os
except ImportError:
    import os


# --- Instance 0: USB Host ---
def instance0():
    # Initialize host
    host = machine.USBHost()
    host.active(True)
    print("Host active")

    # Signal device
    multitest.broadcast("HOST_READY")
    print("Waiting for device")
    multitest.wait("DEVICE_READY")

    # Wait for HID keyboard device
    hid = None
    print("Searching for HID keyboard device...")
    for i in range(10):  # Timeout ~5s
        hid_devices = host.hid_devices()
        if hid_devices:
            for d in hid_devices:
                if d.protocol == d.PROTOCOL_KEYBOARD:
                    hid = d
                    print(f"HID keyboard found: {hid}")
                    break
            if hid:
                break
        print(f"Attempt {i + 1}: Keyboard not found, waiting...")
        time.sleep_ms(500)

    if not hid:
        print("HID keyboard not found - ABORTING")
        multitest.broadcast("HOST_ABORT")
        return

    print(f"Initial is_connected: {hid.is_connected()}")

    # Signal device to disconnect
    multitest.broadcast("DISCONNECT_NOW")
    print("Signaled device to disconnect.")

    # Loop, checking connection status
    print("Entering connection check loop...")
    disconnect_detected = False
    deadline = time.ticks_add(time.ticks_ms(), 8000)  # 8 second timeout

    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if not hid.is_connected():
            print("SUCCESS: hid.is_connected() returned False.")
            disconnect_detected = True
            break

        # Optional: Try interacting (get_report) and catch exceptions?
        try:
            # This might raise an error after disconnect, or return None
            hid.request_report()  # Requesting might fail
            report = hid.get_report()  # Getting might fail or return None
            # print(f"Report while checking: {report}")
            pass
        except Exception as e:
            print(f"Exception during HID interaction (may indicate disconnect): {e}")
            if not hid.is_connected():
                print("SUCCESS: hid.is_connected() returned False after exception.")
                disconnect_detected = True
                break

        time.sleep_ms(100)

    if not disconnect_detected:
        print("TIMEOUT: Failed to detect device disconnect.")

    # Check is_connected one last time
    print(f"Final is_connected check: {hid.is_connected()}")

    print("Host finished")


# --- Instance 1: USB Device ---
def instance1():
    # Prerequisite check
    try:
        import usb.device.keyboard

        print("usb.device.keyboard imported successfully")
    except ImportError:
        print("SKIP: usb.device.keyboard module not found.")
        multitest.skip()
        return

    # Wait for host
    print("Device waiting for HOST_READY")
    multitest.wait("HOST_READY")
    print("HOST_READY received")

    # Configure as HID Keyboard
    print("Configuring as HID Keyboard...")
    try:
        kbd = usb.device.keyboard.Keyboard()
        print("Keyboard device configured")
    except Exception as e:
        print(f"Error configuring Keyboard: {e}")
        print("SKIP: Failed to initialize Keyboard device.")
        multitest.skip()
        return

    # Give host time for enumeration
    print("Waiting 3s for host enumeration...")
    time.sleep(3)
    print("Broadcasting DEVICE_READY")
    multitest.broadcast("DEVICE_READY")

    # Wait for host signal or abort
    print("Device waiting for DISCONNECT_NOW")
    try:
        multitest.wait("DISCONNECT_NOW")
        received_signal = "DISCONNECT_NOW"
    except:
        try:
            multitest.wait("HOST_ABORT")
            received_signal = "HOST_ABORT"
        except:
            received_signal = "TIMEOUT"

    if received_signal == "HOST_ABORT":
        print("HOST_ABORT received, finishing.")
        return
    elif received_signal != "DISCONNECT_NOW":
        print(f"Unexpected signal or timeout: {received_signal}, finishing.")
        return

    print("DISCONNECT_NOW received.")

    # Simulate disconnect by deactivating device properly
    print("Simulating disconnect via device deactivation in 1 second...")
    time.sleep(1)
    print("Deactivating device now.")

    # Proper way to simulate disconnect - deactivate the device
    # This should trigger the host to detect the disconnect
    if hasattr(hid_dev, 'deinit'):
        hid_dev.deinit()
    elif hasattr(hid_dev, 'close'):
        hid_dev.close()

    print("Device deactivated.")
