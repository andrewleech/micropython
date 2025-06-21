# tests/multi_usb/msc_mount_read.py
# Assumes:
# 1. Device instance exposes storage via MSC (e.g., internal flash partition or SD card).
# 2. The exposed filesystem is FAT.
# 3. A file named 'test_read.txt' containing "MSC Read Test" exists at the root
#    of the device's MSC filesystem BEFORE the test runs.

import time
import machine
import vfs

try:
    import uos as os
except ImportError:
    import os

MOUNT_POINT = '/usb'
TEST_FILE = MOUNT_POINT + '/test_read.txt'
EXPECTED_CONTENT = "MSC Read Test"


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
                # Check if it seems ready (block count/size are non-zero)
                # ioctl calls happen during mount, let's rely on that primarily
                break
            time.sleep_ms(250)

        if not msc:
            print("MSC device not found - ABORTING")
            multitest.broadcast("HOST_ABORT")
            return

        # Mount the MSC device
        print(f"Mounting MSC device at {MOUNT_POINT}...")
        try:
            vfs.mount(msc, MOUNT_POINT)
            print("Mount successful")
            mounted = True
        except Exception as e:
            print(f"Mount failed: {e}")
            mounted = False
            multitest.broadcast("HOST_ABORT")  # Signal failure
            return

        if mounted:
            # Try to read the test file
            print(f"Reading file: {TEST_FILE}")
            try:
                with open(TEST_FILE, 'r') as f:
                    content = f.read()
                    print(f"Read content: '{content}'")
                    if content == EXPECTED_CONTENT:
                        print("Content matches expected.")
                    else:
                        print(f"ERROR: Content mismatch! Expected '{EXPECTED_CONTENT}'")
            except Exception as e:
                print(f"Error reading file {TEST_FILE}: {e}")

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

    finally:
        # Cleanup resources
        if mounted:
            try:
                vfs.umount(MOUNT_POINT)
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
        # This is highly port-specific. Assume the default USBDevice enables MSC
        # and exposes an appropriate block device (e.g., internal flash VFS or SD card).
        # Some ports might require explicit configuration here.
        print("Device configured (assuming default includes MSC and suitable block device)")
        # E.g., on some boards: machine.USBDevice(usb.MSC_DEVICE) or similar

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

        # Device doesn't actively do much here, just presents the storage.
        # Cleanup (disable MSC?) might be needed depending on port.

        print("Device finished")

    finally:
        # No explicit cleanup needed for MSC device
        pass
