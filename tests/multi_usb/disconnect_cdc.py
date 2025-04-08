# tests/multi_usb/disconnect_cdc.py
# Tests host reaction to device disconnect during operation.

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

    # Wait for CDC device
    cdc = None
    print("Searching for CDC device...")
    for i in range(10):  # Timeout ~5s
        cdc_devices = host.cdc_devices()
        if cdc_devices:
            cdc = cdc_devices[0]
            print(f"CDC device found: {cdc}")
            break
        print(f"Attempt {i + 1}: Not found, waiting...")
        time.sleep_ms(500)

    if not cdc:
        print("CDC device not found - ABORTING")
        multitest.broadcast("HOST_ABORT")
        return

    print(f"Initial is_connected: {cdc.is_connected()}")

    # Signal device to send some data and then disconnect
    multitest.broadcast("SEND_AND_DISCONNECT")
    print("Signaled device to send and disconnect.")

    # Loop, trying to read and checking connection status
    print("Entering read/connection check loop...")
    data_received = b""
    disconnect_detected = False
    deadline = time.ticks_add(time.ticks_ms(), 8000)  # 8 second timeout

    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if not cdc.is_connected():
            print("SUCCESS: cdc.is_connected() returned False.")
            disconnect_detected = True
            break

        # Try reading any available data
        try:
            available = cdc.any()
            if available > 0:
                chunk = cdc.read(available)
                if chunk:
                    data_received += chunk
                    print(f"Received chunk before disconnect: {chunk}")
                else:
                    # This might happen if disconnected between any() and read()
                    print("any() > 0 but read() returned None, checking connection...")
                    if not cdc.is_connected():
                        print("SUCCESS: cdc.is_connected() returned False after read attempt.")
                        disconnect_detected = True
                        break
        except Exception as e:
            print(f"Exception during read/any (may indicate disconnect): {e}")
            # Check connection status again after exception
            if not cdc.is_connected():
                print("SUCCESS: cdc.is_connected() returned False after exception.")
                disconnect_detected = True
                break
            else:
                print("WARN: Exception occurred but still connected?")

        time.sleep_ms(100)

    if not disconnect_detected:
        print("TIMEOUT: Failed to detect device disconnect.")

    print(f"Final data received: {data_received}")
    # Check is_connected one last time
    print(f"Final is_connected check: {cdc.is_connected()}")

    print("Host finished")


# --- Instance 1: USB Device ---
def instance1():
    # Wait for host
    print("Device waiting for HOST_READY")
    multitest.wait("HOST_READY")
    print("HOST_READY received")

    # Configure as CDC device (assuming default)
    print("Device configured (assuming default CDC)")

    # Give host time for enumeration
    print("Waiting 3s for host enumeration...")
    time.sleep(3)
    print("Broadcasting DEVICE_READY")
    multitest.broadcast("DEVICE_READY")

    # Wait for host signal or abort
    print("Device waiting for SEND_AND_DISCONNECT or HOST_ABORT")
    received_signal = multitest.wait(("SEND_AND_DISCONNECT", "HOST_ABORT"), timeout_ms=7000)

    if received_signal == "HOST_ABORT":
        print("HOST_ABORT received, finishing.")
        return
    elif received_signal != "SEND_AND_DISCONNECT":
        print(f"Unexpected signal or timeout: {received_signal}, finishing.")
        return

    print("SEND_AND_DISCONNECT received.")

    # Send some data
    import sys

    try:
        msg = b"before_disconnect"
        print(f"Device sending: {msg}")
        sys.stdout.buffer.write(msg)
        time.sleep_ms(100)  # Allow data to transit
        print("Data sent.")
    except Exception as e:
        print(f"Device write error: {e}")

    # Simulate disconnect via soft reset
    print("Simulating disconnect via machine.reset() in 1 second...")
    time.sleep(1)
    print("Resetting now.")
    machine.reset()
    # Code execution stops here
