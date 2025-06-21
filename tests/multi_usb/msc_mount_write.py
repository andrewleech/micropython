# tests/multi_usb/msc_mount_write.py
# Assumes:
# 1. Device instance exposes storage via MSC (e.g., internal flash partition or SD card).
# 2. The exposed filesystem is FAT and writable.

import time
import machine
import vfs

try:
    import uos as os
except ImportError:
    import os

MOUNT_POINT = '/usb'
TEST_FILE = MOUNT_POINT + '/msc_write_test.txt'
TEST_DATA = "MicroPython USB Host MSC Write Test: " + str(time.time())


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

    # Wait for MSC device
    msc = None
    print("Searching for MSC device...")
    for i in range(15):  # Timeout ~7.5s
        msc_devices = host.msc_devices()
        if msc_devices:
            msc = msc_devices[0]
            print(f"MSC device found: {msc}")
            break
        print(f"Attempt {i + 1}: MSC not found, waiting...")
        time.sleep_ms(500)

    if not msc:
        print("MSC device not found - ABORTING")
        multitest.broadcast("HOST_ABORT")
        return

    # Mount the MSC device
    print(f"Mounting MSC device at {MOUNT_POINT}...")
    try:
        # Ensure mount point doesn't exist (or handle error)
        try:
            os.rmdir(MOUNT_POINT)  # Fails if it's a file or non-empty dir
        except OSError:
            try:
                os.remove(MOUNT_POINT)  # Fails if it's a dir
            except OSError:
                pass  # Doesn't exist or non-empty dir ok
        try:
            os.mkdir(MOUNT_POINT)
        except OSError:
            pass  # Already exists

        # Attempt to unmount first in case of previous failure
        try:
            vfs.umount(MOUNT_POINT)
        except OSError:
            pass

        # Mount
        vfs.mount(msc, MOUNT_POINT)
        print("Mount successful")
        mounted = True
    except Exception as e:
        print(f"Mount failed: {e}")
        mounted = False
        multitest.broadcast("HOST_ABORT")

    if mounted:
        # Delete previous test file if it exists
        try:
            os.remove(TEST_FILE)
            print(f"Removed existing file: {TEST_FILE}")
        except OSError:
            print(f"Previous file not found: {TEST_FILE}")

        # Write the test file
        print(f"Writing file: {TEST_FILE}")
        write_error = False
        try:
            with open(TEST_FILE, 'w') as f:
                written = f.write(TEST_DATA)
                print(f"Wrote {written} bytes.")
            # Add a small delay/sync if needed, though VfsFat should handle it
            # time.sleep_ms(100)
        except Exception as e:
            print(f"Error writing file {TEST_FILE}: {e}")
            write_error = True

        # Read back the test file to verify
        read_content = None
        read_error = False
        if not write_error:
            print(f"Reading back file: {TEST_FILE}")
            try:
                with open(TEST_FILE, 'r') as f:
                    read_content = f.read()
                    print(f"Read back content: '{read_content}'")
            except Exception as e:
                print(f"Error reading back file {TEST_FILE}: {e}")
                read_error = True

            # Verify content
            if not read_error:
                if read_content == TEST_DATA:
                    print("Content verification PASSED.")
                else:
                    print(
                        f"ERROR: Content verification FAILED! Read '{read_content}', expected '{TEST_DATA}'"
                    )

        # Unmount the device
        print(f"Unmounting {MOUNT_POINT}...")
        try:
            vfs.umount(MOUNT_POINT)
            print("Unmount successful")
        except Exception as e:
            print(f"Unmount failed: {e}")

    # Signal host finished its MSC operations
    multitest.broadcast("HOST_FINISHED_MSC")

    print("Host finished")


# --- Instance 1: USB Device ---
def instance1():
    try:
        # Configure as MSC device (assuming default includes MSC and suitable block device)
        print("Device configured (assuming default includes MSC and suitable block device)")

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

        # Wait for host to finish or abort
        print("Device waiting for HOST_FINISHED_MSC")
        try:
            multitest.wait("HOST_FINISHED_MSC")
            print("HOST_FINISHED_MSC received.")
        except:
            # Check if we got an abort signal
            try:
                multitest.wait("HOST_ABORT")
                print("HOST_ABORT received.")
            except:
                print("Timeout waiting for signals")

        print("Device finished")

    finally:
        # No explicit cleanup needed for MSC device
        pass
