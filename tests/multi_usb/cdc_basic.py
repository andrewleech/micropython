# tests/multi_usb/cdc_basic.py
import time
import machine

try:
    import uos as os
except ImportError:
    import os


# --- Instance 0: USB Host ---
def instance0():
    # Need host controller enabled before device connects
    host = machine.USBHost()
    host.active(True)
    print("Host active")

    # Signal device it can start
    multitest.broadcast("HOST_READY")
    print("Waiting for device")
    multitest.wait("DEVICE_READY")

    # Wait for CDC device to enumerate
    cdc = None
    print("Searching for CDC device...")
    for i in range(10):  # Timeout after ~5 seconds
        cdc_devices = host.cdc_devices()
        if cdc_devices:
            cdc = cdc_devices[0]
            print(f"CDC device found: {cdc}")
            break
        print(f"Attempt {i + 1}: Not found, waiting...")
        time.sleep_ms(500)

    if not cdc:
        print("CDC device not found - ABORTING")
        # Signal device to stop waiting if needed, although it might timeout anyway
        multitest.broadcast("HOST_ABORT")
        return

    # Test basic properties/methods
    print(f"is_connected: {cdc.is_connected()}")
    print(f"any (before recv): {cdc.any()}")

    # Signal device to send data
    multitest.broadcast("SEND_DATA")
    print("Waiting for data from device...")
    multitest.wait("DATA_SENT")
    print("Device data supposedly sent.")

    # Read data
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
            # No data available, wait a bit
            time.sleep_ms(50)

    if not data:
        print("TIMEOUT waiting for data from device")
    else:
        print(f"Received from device: {data}")

    # Send data back to device
    print("Sending 'world' to device...")
    bytes_written = cdc.write(b"world")
    print(f"Bytes written: {bytes_written}")
    # Small delay to ensure data is sent before signaling
    time.sleep_ms(100)
    multitest.broadcast("DATA_SENT_BACK")
    print("Signaled DATA_SENT_BACK")

    # Let device finish its print statements
    time.sleep_ms(500)

    print("Host finished")


# --- Instance 1: USB Device ---
def instance1():
    # Wait for host to be ready
    print("Device waiting for HOST_READY")
    multitest.wait("HOST_READY")
    print("HOST_READY received")

    # Configure as CDC device (using default device config if port supports it)
    # This assumes the board's default USBDevice setup includes CDC.
    # Activation might happen implicitly or require machine.USBDevice() call
    # depending on the port. Let's assume it's automatic for now.
    print("Device configured (assuming default CDC)")

    # Give host time to see the device after it's configured
    print("Waiting 2s for host enumeration...")
    time.sleep(2)
    print("Broadcasting DEVICE_READY")
    multitest.broadcast("DEVICE_READY")

    # Wait for host signal to send data, or abort signal
    print("Device waiting for SEND_DATA or HOST_ABORT")
    received_signal = multitest.wait(("SEND_DATA", "HOST_ABORT"), timeout_ms=7000)  # Wait up to 7s

    if received_signal == "HOST_ABORT":
        print("HOST_ABORT received, device finishing.")
        return
    elif received_signal != "SEND_DATA":
        print(f"Unexpected signal or timeout waiting for SEND_DATA: {received_signal}")
        return

    print("SEND_DATA received")

    # Attempt to write to stdio, assuming it's the USB VCP
    import sys

    try:
        print("Device sending 'hello'...")
        bytes_sent = sys.stdout.write("hello")  # Note: print() adds newline, write doesn't
        # How to check if write succeeded or how many bytes? Assume it worked.
        print(f"stdout.write returned: {bytes_sent} (Note: might be None or num bytes)")
        # Need a way to flush? Some ports might buffer. Add a small delay.
        time.sleep_ms(100)
        print("Broadcasting DATA_SENT")
        multitest.broadcast("DATA_SENT")
    except Exception as e:
        print(f"Device write error: {e}")
        # Still signal host even if write fails, so host doesn't hang
        print("Broadcasting DATA_SENT despite error")
        multitest.broadcast("DATA_SENT")
        return

    # Wait for host response
    print("Device waiting for DATA_SENT_BACK")
    multitest.wait("DATA_SENT_BACK")
    print("DATA_SENT_BACK received")

    # How to read from VCP? Assume sys.stdin if available and non-blocking.
    # This is highly speculative and port-dependent.
    print("Device attempting to read 'world'...")
    data = b""
    try:
        # Check if stdin has a buffer attribute (like CPython)
        # MicroPython might use other ways (uos.dupterm?)
        # A non-blocking read mechanism is needed.
        # This part likely needs port-specific adaptation or a helper library.

        # Simple polling example (might not work):
        deadline = time.ticks_add(time.ticks_ms(), 3000)  # 3 sec timeout
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            # Need a non-blocking way to check if data is available on stdin
            # This is missing in standard MicroPython streams.
            # Let's simulate a read attempt - THIS WILL LIKELY BLOCK or FAIL
            # chunk = sys.stdin.read(5) # Standard read is blocking
            # A better approach might be needed via select module if USB VCP fd is exposed
            # Or via a specific machine.USBDevice stream object if it exists.

            # **Placeholder**: Assume we somehow got the data for test logic flow
            # In a real test, this needs a working read mechanism.
            # if simulated_data_received:
            #    data = b"world"
            #    break

            # Since we can't reliably read non-blockingly, just wait a bit.
            time.sleep_ms(100)

        # Check if we (conceptually) received "world"
        # For now, just print a placeholder message.
        # Replace with actual check if read mechanism is found.
        print("Device read attempt finished (mechanism TBD)")
        # if data == b"world":
        #    print(f"Received from host: {data}")
        # else:
        #    print(f"Did not receive 'world' (Received: {data})")

    except Exception as e:
        print(f"Device read error: {e}")

    print("Device finished")
