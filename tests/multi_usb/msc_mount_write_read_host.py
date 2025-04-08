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
    for i in range(15):  # Timeout ~7.5s (MSC can take longer)
        msc_devices = host.msc_devices()
        if msc_devices:
            msc = msc_devices[0]
            print(f"MSC device found: {msc}")
            # Check if it seems ready (block count/size are non-zero)
            # ioctl calls happen during mount, let's rely on that primarily
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
        vfs.mount(msc, MOUNT_POINT)
        print("Mount successful")
        mounted = True
    except Exception as e:
        print(f"Mount failed: {e}")
        mounted = False
        multitest.broadcast("HOST_ABORT")  # Signal failure

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


# --- Instance 1: USB Device ---
def instance1():
    # Wait for host
    print("Device waiting for HOST_READY")
    multitest.wait("HOST_READY")
    print("HOST_READY received")

    # Configure as MSC device
    # This is highly port-specific. Assume the default USBDevice enables MSC
    # and exposes an appropriate block device (e.g., internal flash VFS or SD card).
    # Some ports might require explicit configuration here.
    print("Device configured (assuming default includes MSC and suitable block device)")
    # E.g., on some boards: machine.USBDevice(usb.MSC_DEVICE) or similar

    # Give host time for enumeration
    print("Waiting 5s for host enumeration (MSC can be slow)...")
    time.sleep(5)
    print("Broadcasting DEVICE_READY")
    multitest.broadcast("DEVICE_READY")

    # Wait for host to finish or abort
    print("Device waiting for HOST_FINISHED_MSC or HOST_ABORT")
    received_signal = multitest.wait(
        ("HOST_FINISHED_MSC", "HOST_ABORT"), timeout_ms=20000
    )  # Generous timeout

    if received_signal == "HOST_ABORT":
        print("HOST_ABORT received.")
    elif received_signal == "HOST_FINISHED_MSC":
        print("HOST_FINISHED_MSC received.")
    else:
        print(f"Unexpected signal or timeout: {received_signal}")

    # Device doesn't actively do much here, just presents the storage.
    # Cleanup (disable MSC?) might be needed depending on port.

    print("Device finished")
