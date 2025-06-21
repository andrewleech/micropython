# Prerequisite: Requires the device instance (instance1) to have the
# usb-device-cdc library installed. Install using mpremote connected to the device:
# mpremote mip install usb-device-cdc
#
# tests/multi_usb/cdc_custom_lib.py
# Assumes device instance has 'usb.device.cdc' available (e.g., frozen).

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

        # Test connection
        print(f"is_connected: {cdc.is_connected()}")

        # Synchronization point
        multitest.next()

        # --- Host Write -> Device Read ---
        host_msg = b"HostToDeviceCustom"
        print(f"Host sending: {host_msg}")
        bytes_written = cdc.write(host_msg)
        print(f"Bytes written: {bytes_written}")
        time.sleep_ms(100)  # Give time for data transfer
        multitest.broadcast("HOST_DATA_SENT")
        print("Waiting for device to confirm read...")
        multitest.wait("DEVICE_READ_CONFIRMED")
        print("Device confirmation received.")

        # Synchronization point
        multitest.next()

        # --- Device Write -> Host Read ---
        print("Signaling device to send data...")
        multitest.broadcast("SEND_DEVICE_DATA")
        print("Waiting for device data...")
        multitest.wait("DEVICE_DATA_SENT")

        print("Reading data from device...")
        data_from_device = b""
        deadline = time.ticks_add(time.ticks_ms(), 3000)  # 3 sec timeout
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            available = cdc.any()
            if available > 0:
                chunk = cdc.read(available)
                if chunk:
                    data_from_device += chunk
                    print(f"Host received chunk: {chunk}")
                    if len(data_from_device) >= 18:  # Length of b'DeviceToHostCustom'
                        break
                else:
                    print("Host read returned no data despite any() > 0")
            time.sleep_ms(50)

        expected_device_msg = b'DeviceToHostCustom'
        if data_from_device == expected_device_msg:
            print(f"Host received expected data: {data_from_device}")
        else:
            print(
                f"ERROR: Host received unexpected data! Got: {data_from_device}, Expected: {expected_device_msg}"
            )

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
        # Try importing the necessary CDC helper
        try:
            import usb.device.cdc

            print("usb.device.cdc imported successfully")
        except ImportError:
            print("SKIP: usb.device.cdc module not found.")
            multitest.skip()
            return  # Skip the test if the module is missing

        # Synchronization point
        multitest.next()

        # Wait for host
        print("Device waiting for HOST_READY")
        multitest.wait("HOST_READY")
        print("HOST_READY received")

        # Configure as CDC device using the library
        print("Configuring as CDC device via usb.device.cdc...")
        try:
            # The Device class likely handles USBDevice activation
            cdc_dev = usb.device.cdc.Device()
            print("usb.device.cdc.Device configured")
        except Exception as e:
            print(f"Error configuring usb.device.cdc.Device: {e}")
            print("SKIP: Failed to initialize CDC device.")
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

        # Wait for host data or abort
        print("Device waiting for HOST_DATA_SENT")
        try:
            multitest.wait("HOST_DATA_SENT")
            received_signal = "HOST_DATA_SENT"
        except:
            # Check if we got an abort signal
            try:
                multitest.wait("HOST_ABORT")
                received_signal = "HOST_ABORT"
            except:
                print("Timeout waiting for signals")
                return

        # Synchronization point
        multitest.next()

        if received_signal == "HOST_ABORT":
            print("HOST_ABORT received, finishing.")
            return
        elif received_signal != "HOST_DATA_SENT":
            print(f"Unexpected signal or timeout waiting for HOST_DATA_SENT: {received_signal}")
            return

        # --- Device Read <- Host Write ---
        print("HOST_DATA_SENT received. Reading from CDC stream...")
        data_from_host = b""
        read_error = False
        try:
            deadline = time.ticks_add(time.ticks_ms(), 3000)  # 3 sec timeout
            while time.ticks_diff(deadline, time.ticks_ms()) > 0:
                # Use the stream interface provided by the cdc_dev object
                available = cdc_dev.any()
                if available > 0:
                    chunk = cdc_dev.read(available)
                    if chunk:
                        data_from_host += chunk
                        print(f"Device received chunk: {chunk}")
                        if len(data_from_host) >= 18:  # Length of b'HostToDeviceCustom'
                            break
                time.sleep_ms(50)

            expected_host_msg = b'HostToDeviceCustom'
            if data_from_host == expected_host_msg:
                print(f"Device received expected data: {data_from_host}")
            else:
                print(
                    f"ERROR: Device received unexpected data! Got: {data_from_host}, Expected: {expected_host_msg}"
                )

        except Exception as e:
            print(f"Device read error: {e}")
            read_error = True

        print("Broadcasting DEVICE_READ_CONFIRMED")
        multitest.broadcast("DEVICE_READ_CONFIRMED")

        # Synchronization point
        multitest.next()

        # --- Device Write -> Host Read ---
        # Wait for signal from host
        print("Device waiting for SEND_DEVICE_DATA")
        multitest.wait("SEND_DEVICE_DATA")
        print("SEND_DEVICE_DATA received.")

        device_msg = b"DeviceToHostCustom"
        print(f"Device sending: {device_msg}")
        write_error = False
        try:
            # Use the stream interface provided by the cdc_dev object
            bytes_written = cdc_dev.write(device_msg)
            print(f"Device write returned: {bytes_written}")
            # Add delay for data transmission
            time.sleep_ms(100)
        except Exception as e:
            print(f"Device write error: {e}")
            write_error = True

        print("Broadcasting DEVICE_DATA_SENT")
        multitest.broadcast("DEVICE_DATA_SENT")

        print("Device finished")

    finally:
        # Cleanup (optional)
        # if hasattr(cdc_dev, 'close'): cdc_dev.close()
        pass
