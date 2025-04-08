# tests/multi_usb/disconnect_msc.py
# Tests host reaction to MSC device disconnect.

import time
import machine
import vfs

try:
    import uos as os
except ImportError:
    import os

HOST_MOUNT_POINT = '/usb'


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

    print(f"Initial is_connected: {msc.is_connected()}")

    # Mount the MSC device to make it active
    print(f"Mounting MSC device at {HOST_MOUNT_POINT}...")
    mounted = False
    try:
        try:
            os.rmdir(HOST_MOUNT_POINT)
        except OSError:
            pass  # Ignore
        try:
            os.mkdir(HOST_MOUNT_POINT)
        except OSError:
            pass  # Ignore
        try:
            vfs.umount(HOST_MOUNT_POINT)
        except OSError:
            pass  # Ignore
        vfs.mount(msc, HOST_MOUNT_POINT)
        print("Mount successful")
        mounted = True
    except Exception as e:
        print(f"Mount failed: {e}")
        multitest.broadcast("HOST_ABORT")

    # Signal device to disconnect only if mount was successful
    if mounted:
        multitest.broadcast("DISCONNECT_NOW")
        print("Signaled device to disconnect.")
    else:
        print("Skipping disconnect signal due to mount failure.")

    # Loop, checking connection status
    print("Entering connection check loop...")
    disconnect_detected = False
    deadline = time.ticks_add(time.ticks_ms(), 8000)  # 8 second timeout

    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if not msc.is_connected():
            print("SUCCESS: msc.is_connected() returned False.")
            disconnect_detected = True
            break

        # Optional: Try filesystem operations (e.g., os.listdir) and catch exceptions?
        try:
            # Accessing the mounted filesystem might fail after disconnect
            # os.listdir(HOST_MOUNT_POINT)
            pass
        except Exception as e:
            print(f"Exception during FS interaction (may indicate disconnect): {e}")
            if not msc.is_connected():
                print("SUCCESS: msc.is_connected() returned False after exception.")
                disconnect_detected = True
                break

        time.sleep_ms(100)

    if not disconnect_detected:
        print("TIMEOUT: Failed to detect device disconnect.")

    # Check is_connected one last time
    print(f"Final is_connected check: {msc.is_connected()}")

    # Attempt to unmount (should fail if disconnected, might succeed if timeout)
    if mounted:  # Only try if we thought it was mounted initially
        print(f"Attempting final unmount of {HOST_MOUNT_POINT}...")
        try:
            vfs.umount(HOST_MOUNT_POINT)
            print("Final unmount successful (unexpected if disconnect occurred)")
        except Exception as e:
            print(f"Final unmount failed as expected: {e}")

    print("Host finished")


# --- Instance 1: USB Device ---
def instance1():
    # Wait for host
    print("Device waiting for HOST_READY")
    multitest.wait("HOST_READY")
    print("HOST_READY received")

    # Configure as MSC device (assuming default)
    print("Device configured (assuming default includes MSC)")

    # Give host time for enumeration
    print("Waiting 5s for host enumeration...")
    time.sleep(5)
    print("Broadcasting DEVICE_READY")
    multitest.broadcast("DEVICE_READY")

    # Wait for host signal or abort
    print("Device waiting for DISCONNECT_NOW or HOST_ABORT")
    received_signal = multitest.wait(
        ("DISCONNECT_NOW", "HOST_ABORT"), timeout_ms=15000
    )  # Wait longer for mount

    if received_signal == "HOST_ABORT":
        print("HOST_ABORT received, finishing.")
        return
    elif received_signal != "DISCONNECT_NOW":
        print(f"Unexpected signal or timeout: {received_signal}, finishing.")
        return

    print("DISCONNECT_NOW received.")

    # Simulate disconnect via soft reset
    print("Simulating disconnect via machine.reset() in 1 second...")
    time.sleep(1)
    print("Resetting now.")
    machine.reset()
    # Code execution stops here
