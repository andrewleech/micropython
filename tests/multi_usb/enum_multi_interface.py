# tests/multi_usb/enum_multi_interface.py
# Assumes:
# 1. Device instance is configured (e.g., via boot.py or specific USBDevice setup)
#    to present *both* a CDC interface and an HID interface (e.g., keyboard or mouse).

import time
import machine

try:
    import uos as os
except ImportError:
    import os


# --- Instance 0: USB Host ---
def instance0():
    host = None

    try:
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

        # Wait for device enumeration with polling (give it a bit longer for multi-interface)
        print("Waiting up to 10s for device enumeration...")
        deadline = time.ticks_add(time.ticks_ms(), 10000)  # 10 second timeout
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            time.sleep_ms(200)

        # Check for CDC device
        cdc_found = False
        cdc_devices = host.cdc_devices()
        if cdc_devices:
            cdc = cdc_devices[0]
            print(f"CDC device found: {cdc}")
            print(f"CDC is_connected: {cdc.is_connected()}")
            # Optional: Minimal CDC interaction
            # cdc.write(b'ping')
            # print("CDC ping sent (no readback)")
            cdc_found = True
        else:
            print("ERROR: No CDC device found!")

        # Check for HID device
        hid_found = False
        hid_devices = host.hid_devices()
        if hid_devices:
            hid = hid_devices[0]  # Assume first one if multiple
            print(f"HID device found: {hid}")
            print(f"HID is_connected: {hid.is_connected()}")
            print(f"HID protocol: {hid.protocol}")
            print(f"HID usage_page: {hid.usage_page}")
            print(f"HID usage: {hid.usage}")
            # Optional: Minimal HID interaction
            # report = hid.get_report() # Might be None initially
            # print(f"HID initial get_report: {report}")
            hid_found = True
        else:
            print("ERROR: No HID device found!")

        # Final result
        if cdc_found and hid_found:
            print("Found both CDC and HID interfaces as expected.")
        else:
            print("FAILED to find both CDC and HID interfaces.")

        # Signal completion
        multitest.broadcast("HOST_FINISHED")
        print("Host finished")

    finally:
        # Cleanup resources
        if host:
            host.active(False)
            print("Host deactivated")


# --- Instance 1: USB Device ---
def instance1():
    try:
        # Configure as Composite CDC + HID device
        # This setup is complex and highly port/board specific.
        # It might involve: machine.USBDevice(composite_descriptor)
        # Or be the default configuration for the board.
        # We simply assume it's done correctly here.
        print("Device configured (assuming default or boot.py sets up CDC+HID)")

        # Synchronization point
        multitest.next()

        # Wait for host
        print("Device waiting for HOST_READY")
        multitest.wait("HOST_READY")
        print("HOST_READY received")

        # Give host ample time for enumeration of multiple interfaces with polling
        deadline = time.ticks_add(time.ticks_ms(), 10000)  # 10 second timeout
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            time.sleep_ms(200)

        print("Broadcasting DEVICE_READY")
        multitest.broadcast("DEVICE_READY")

        # Synchronization point
        multitest.next()

        # Wait for host to finish checks
        print("Device waiting for HOST_FINISHED")
        try:
            multitest.wait("HOST_FINISHED")
            print("HOST_FINISHED received.")
        except:
            # Check if we got an abort signal
            try:
                multitest.wait("HOST_ABORT")
                print("HOST_ABORT received.")
            except:
                print("Timeout waiting for signals")

        # Device side doesn't need active interaction for this test
        print("Device finished")

    finally:
        # No explicit cleanup needed for composite device
        pass
