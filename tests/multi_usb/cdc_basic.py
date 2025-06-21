# tests/multi_usb/cdc_basic.py
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
        # Need host controller enabled before device connects
        host = machine.USBHost()
        host.active(True)
        print("Host active")

        # Synchronization point
        multitest.next()

        # Signal device it can start
        multitest.broadcast("HOST_READY")
        print("Waiting for device")
        multitest.wait("DEVICE_READY")

        # Synchronization point
        multitest.next()

        # Wait for CDC device to enumerate with proper polling
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

        # Test basic properties/methods
        print(f"is_connected: {cdc.is_connected()}")
        print(f"any (before recv): {cdc.any()}")

        # Synchronization point
        multitest.next()

        # Signal device to send data
        multitest.broadcast("SEND_DATA")
        print("Waiting for data from device...")
        multitest.wait("DATA_SENT")
        print("Device data supposedly sent.")

        # Read data with timeout
        data = b""
        deadline = time.ticks_add(time.ticks_ms(), 3000)  # 3 sec timeout
        print("Reading data...")
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            available = cdc.any()
            if available > 0:
                print(f"Reading {available} bytes")
                chunk = cdc.read(available)
                if chunk:
                    data += chunk
                    print(f"Received chunk: {chunk}")
                    if b"hello" in data:
                        print("Found 'hello'")
                        break
                else:
                    print("Read returned no data despite any() > 0")
            else:
                time.sleep_ms(50)

        if not data:
            print("TIMEOUT waiting for data from device")
        else:
            print(f"Received from device: {data}")

        # Synchronization point
        multitest.next()

        # Send data back to device
        print("Sending 'world' to device...")
        bytes_written = cdc.write(b"world")
        print(f"Bytes written: {bytes_written}")
        time.sleep_ms(100)
        multitest.broadcast("DATA_SENT_BACK")
        print("Signaled DATA_SENT_BACK")

        # Let device finish
        time.sleep_ms(500)
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

        # Wait for host to be ready
        print("Device waiting for HOST_READY")
        multitest.wait("HOST_READY")
        print("HOST_READY received")

        # Give host time to see the device after enumeration
        deadline = time.ticks_add(time.ticks_ms(), 3000)  # 3 second timeout
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            time.sleep_ms(100)
            # Could check device state here if API supports it

        print("Broadcasting DEVICE_READY")
        multitest.broadcast("DEVICE_READY")

        # Synchronization point
        multitest.next()

        # Wait for host signal to send data or abort
        print("Device waiting for SEND_DATA")
        try:
            multitest.wait("SEND_DATA")
            print("SEND_DATA received")
        except:
            # Check if we got an abort signal by trying to read
            try:
                multitest.wait("HOST_ABORT")
                print("HOST_ABORT received, device finishing.")
                return
            except:
                print("Timeout waiting for signals")
                return

        # Synchronization point
        multitest.next()

        # Send data to host using proper CDC device API
        try:
            print("Device sending 'hello'...")
            bytes_sent = cdc_dev.write(b"hello")
            print(f"CDC write returned: {bytes_sent} bytes")
            time.sleep_ms(100)  # Allow data to be transmitted
            print("Broadcasting DATA_SENT")
            multitest.broadcast("DATA_SENT")
        except Exception as e:
            print(f"Device write error: {e}")
            multitest.broadcast("DATA_SENT")
            return

        # Synchronization point
        multitest.next()

        # Wait for host response
        print("Device waiting for DATA_SENT_BACK")
        multitest.wait("DATA_SENT_BACK")
        print("DATA_SENT_BACK received")

        # Read data from host using proper CDC device API
        print("Device attempting to read 'world'...")
        data = b""
        try:
            deadline = time.ticks_add(time.ticks_ms(), 3000)  # 3 sec timeout
            while time.ticks_diff(deadline, time.ticks_ms()) > 0:
                available = cdc_dev.any()
                if available > 0:
                    chunk = cdc_dev.read(available)
                    if chunk:
                        data += chunk
                        print(f"Received chunk: {chunk}")
                        if b"world" in data:
                            print(f"Received from host: {data}")
                            break
                else:
                    time.sleep_ms(50)

            if not data:
                print("TIMEOUT waiting for data from host")
            elif b"world" not in data:
                print(f"Did not receive 'world' (Received: {data})")

        except Exception as e:
            print(f"Device read error: {e}")

        print("Device finished")

    finally:
        # No explicit cleanup needed for CDC device
        pass
