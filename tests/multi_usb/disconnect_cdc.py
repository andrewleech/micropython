# tests/multi_usb/disconnect_cdc.py
# Tests host reaction to device disconnect during operation.
# Uses proper device deactivation instead of machine.reset().

import time
import machine

try:
    import uos as os
except ImportError:
    import os


# --- Instance 0: USB Host ---
def instance0():
    host = None
    cdc = None

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

        # Wait for CDC device with proper polling
        cdc = None
        print("Searching for CDC device...")
        deadline = time.ticks_add(time.ticks_ms(), 5000)  # 5 second timeout
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            cdc_devices = host.cdc_devices()
            if cdc_devices:
                cdc = cdc_devices[0]
                print(f"CDC device found: {cdc}")
                break
            time.sleep_ms(200)

        if not cdc:
            print("CDC device not found - ABORTING")
            multitest.broadcast("HOST_ABORT")
            return

        print(f"Initial is_connected: {cdc.is_connected()}")

        # Synchronization point
        multitest.next()

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

    finally:
        # Cleanup resources
        if host:
            host.active(False)
            print("Host deactivated")


# --- Instance 1: USB Device ---
def instance1():
    cdc_dev = None

    try:
        # Try to import the proper CDC device module
        try:
            import usb.device.cdc

            cdc_dev = usb.device.cdc.Device()
            print("CDC device configured")
        except ImportError:
            print("usb.device.cdc not available, skipping test")
            multitest.skip()

        # Synchronization point
        multitest.next()

        # Wait for host
        print("Device waiting for HOST_READY")
        multitest.wait("HOST_READY")
        print("HOST_READY received")

        # Give host time for enumeration with polling
        deadline = time.ticks_add(time.ticks_ms(), 3000)  # 3 second timeout
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            time.sleep_ms(100)

        print("Broadcasting DEVICE_READY")
        multitest.broadcast("DEVICE_READY")

        # Synchronization point
        multitest.next()

        # Wait for host signal or abort
        print("Device waiting for SEND_AND_DISCONNECT")
        try:
            multitest.wait("SEND_AND_DISCONNECT")
            print("SEND_AND_DISCONNECT received.")
        except:
            # Check if we got an abort signal
            try:
                multitest.wait("HOST_ABORT")
                print("HOST_ABORT received, finishing.")
                return
            except:
                print("Timeout waiting for signals")
                return

        # Synchronization point
        multitest.next()

        # Send some data using proper CDC device API
        try:
            msg = b"before_disconnect"
            print(f"Device sending: {msg}")
            cdc_dev.write(msg)
            time.sleep_ms(100)  # Allow data to transit
            print("Data sent.")
        except Exception as e:
            print(f"Device write error: {e}")

        # Simulate disconnect by deactivating device properly
        print("Simulating disconnect via device deactivation in 1 second...")
        time.sleep(1)
        print("Deactivating device now.")

        # Proper way to simulate disconnect - deactivate the device
        # This should trigger the host to detect the disconnect
        if hasattr(cdc_dev, 'deinit'):
            cdc_dev.deinit()
        elif hasattr(cdc_dev, 'close'):
            cdc_dev.close()

        print("Device deactivated.")

    finally:
        # Additional cleanup if needed
        pass
