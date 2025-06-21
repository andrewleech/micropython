# tests/multi_usb/msc_write_host_read_device.py
# Assumes:
# 1. Device instance exposes storage via MSC (e.g., internal flash partition or SD card).
# 2. The exposed filesystem is FAT and writable.
# 3. The device-side filesystem root is accessible (e.g., '/') after reset.

import time
import machine
import vfs

try:
    import uos as os
except ImportError:
    import os

HOST_MOUNT_POINT = '/usb'
DEVICE_FILE_PATH = '/msc_write_test.txt'  # Path relative to device's root fs
HOST_TEST_FILE = HOST_MOUNT_POINT + DEVICE_FILE_PATH

# Use a fixed string for simplicity in device-side verification via globals
TEST_DATA = "HostWriteDeviceRead"


# --- Instance 0: USB Host ---
def instance0():
    host = None
    msc = None
    mounted = False

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

        # Wait for MSC device with proper polling
        msc = None
        print("Searching for MSC device...")
        deadline = time.ticks_add(time.ticks_ms(), 7500)  # 7.5 second timeout
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            msc_devices = host.msc_devices()
            if msc_devices:
                msc = msc_devices[0]
                print(f"MSC device found: {msc}")
                break
            time.sleep_ms(250)

        if not msc:
            print("MSC device not found - ABORTING")
            multitest.broadcast("HOST_ABORT")
            return

        # Mount the MSC device
        print(f"Mounting MSC device at {HOST_MOUNT_POINT}...")
        mounted = False
        try:
            # Prep mount point
            try:
                os.rmdir(HOST_MOUNT_POINT)
            except OSError:
                try:
                    os.remove(HOST_MOUNT_POINT)
                except OSError:
                    pass
            try:
                os.mkdir(HOST_MOUNT_POINT)
            except OSError:
                pass
            try:
                vfs.umount(HOST_MOUNT_POINT)
            except OSError:
                pass
            # Mount
            vfs.mount(msc, HOST_MOUNT_POINT)
            print("Mount successful")
            mounted = True
        except Exception as e:
            print(f"Mount failed: {e}")
            multitest.broadcast("HOST_ABORT")
            return

        write_successful = False
        if mounted:
            # Delete previous test file if it exists
            try:
                os.remove(HOST_TEST_FILE)
                print(f"Removed existing file: {HOST_TEST_FILE}")
            except OSError:
                print(f"Previous file not found: {HOST_TEST_FILE}")

            # Write the test file
            print(f"Writing file: {HOST_TEST_FILE} with data: '{TEST_DATA}'")
            try:
                with open(HOST_TEST_FILE, 'w') as f:
                    written = f.write(TEST_DATA)
                    print(f"Wrote {written} bytes.")
                write_successful = True
                # Pass test data to device via globals for verification after reset
                multitest.globals(
                    EXPECTED_DATA_GLOBAL=TEST_DATA, TEST_FILE_GLOBAL=DEVICE_FILE_PATH
                )
                print("Test data sent to device via globals")

                # Synchronization point
                multitest.next()
            except Exception as e:
                print(f"Error writing file {HOST_TEST_FILE}: {e}")

            # Unmount the device
            print(f"Unmounting {HOST_MOUNT_POINT}...")
            try:
                vfs.umount(HOST_MOUNT_POINT)
                print("Unmount successful")
            except Exception as e:
                print(f"Unmount failed: {e}")

        # Signal device to reset and read, only if write was successful
        if write_successful:
            print("Signaling device to RESET_AND_READ")
            multitest.broadcast("RESET_AND_READ")
            # Wait for device to signal completion after reset
            print("Waiting for DEVICE_READ_COMPLETE")
            multitest.wait("DEVICE_READ_COMPLETE", timeout_ms=15000)  # Needs time for reset+read
            print("DEVICE_READ_COMPLETE received")
        else:
            print("Skipping device read due to host write failure")
            multitest.broadcast("HOST_ABORT")  # Tell device not to proceed

        print("Host finished")

    finally:
        # Cleanup resources
        if mounted:
            try:
                vfs.umount(HOST_MOUNT_POINT)
                print("Unmounted successfully")
            except Exception as e:
                print(f"Unmount failed: {e}")
        if host:
            host.active(False)
            print("Host deactivated")


# --- Instance 1: USB Device ---
def instance1():
    try:
        # Configure as MSC device
        print("Device configured (assuming default includes MSC)")

        # Synchronization point
        multitest.next()

        # Wait for host
        print("Device waiting for HOST_READY")
        multitest.wait("HOST_READY")
        print("HOST_READY received")

        # Give host time for enumeration with polling
        deadline = time.ticks_add(time.ticks_ms(), 5000)  # 5 second timeout
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            time.sleep_ms(200)

        print("Broadcasting DEVICE_READY")
        multitest.broadcast("DEVICE_READY")

        # Synchronization point
        multitest.next()

        # Wait for host signal to reset/read or abort
        print("Device waiting for RESET_AND_READ")
        try:
            multitest.wait("RESET_AND_READ")
            print("RESET_AND_READ received.")
        except:
            # Check if we got an abort signal
            try:
                multitest.wait("HOST_ABORT")
                print("HOST_ABORT received, finishing.")
                return
            except:
                print("Timeout waiting for signals")
                return

        # Use the special multitest function to request a reboot and specify resume function
        # This requires run-multitests.py to handle WAIT_FOR_REBOOT
        multitest.expect_reboot("instance1_resume", delay_ms=500)  # Add delay after print

    finally:
        # No explicit cleanup needed for MSC device
        pass


# --- Instance 1: Resume after reboot ---
def instance1_resume():
    # This code runs after the device soft-reboots
    print("Device resumed after reboot.")
    try:
        import uos as os
    except ImportError:
        import os

    # Globals EXPECTED_DATA_GLOBAL and TEST_FILE_GLOBAL should have been set by host
    # Access them directly (they are injected into the global scope by the runner)
    try:
        expected_data = EXPECTED_DATA_GLOBAL
        test_file = TEST_FILE_GLOBAL
        print(f"Resumed: Expecting '{expected_data}' in '{test_file}'")
    except NameError:
        print("ERROR: Multitest globals not found after reboot!")
        multitest.broadcast("DEVICE_READ_COMPLETE")  # Signal completion anyway
        return

    # Read the file from the device's own filesystem
    read_content = None
    read_error = False
    print(f"Reading file from device FS: {test_file}")
    try:
        # Ensure filesystem is mounted if necessary (might happen in boot.py)
        # time.sleep(1) # Optional delay for FS mount
        with open(test_file, 'r') as f:
            read_content = f.read()
            print(f"Device read content: '{read_content}'")
    except Exception as e:
        print(f"Device error reading file {test_file}: {e}")
        read_error = True

    # Verify content
    if not read_error:
        if read_content == expected_data:
            print("Device content verification PASSED.")
        else:
            print(f"ERROR: Device content verification FAILED! Read '{read_content}'")

    # Signal completion to host
    print("Broadcasting DEVICE_READ_COMPLETE")
    multitest.broadcast("DEVICE_READ_COMPLETE")
    print("Device finished post-reboot sequence.")
