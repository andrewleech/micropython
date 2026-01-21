# tests/multi_usb/msc_block_rw.py
#
# Test raw MSC block read/write operations.
# Verifies consecutive block writes and read-back verification.
#
# Instance 0: USB Host
# Instance 1: USB Device with MSC support
#
# Dependencies:
#   Instance 1 requires firmware built with MICROPY_HW_ENABLE_USB_RUNTIME_DEVICE=1
#   and USBDevice.BUILTIN_CDC_MSC support (not installable via mip).

import time
from usbtest_util import wait_for_enum


# --- Instance 0: USB Host ---
def instance0():
    import machine

    host = machine.USBHost()
    host.active(True)
    print("Host active")

    multitest.next()
    multitest.broadcast("HOST_READY")
    multitest.wait("DEVICE_READY")

    # Wait for MSC device
    msc = wait_for_enum(host, "msc")
    if not msc:
        print("NO MSC DEVICE")
        multitest.broadcast("DONE")
        return
    print("Found MSC device")

    # Verify block device properties
    block_count = msc.ioctl(4, 0)  # IOCTL_BLOCK_COUNT
    block_size = msc.ioctl(5, 0)  # IOCTL_BLOCK_SIZE
    if block_count > 0 and block_size == 512:
        print("MSC ready")
    else:
        print(f"MSC error: blocks={block_count} size={block_size}")
        multitest.broadcast("DONE")
        return

    # Use blocks near the end of the device to avoid reserved areas
    test_block_0 = block_count - 4
    test_block_1 = block_count - 3

    # Pre-read to ensure device is working
    buf = bytearray(512)
    try:
        msc.readblocks(test_block_0, buf)
        msc.readblocks(test_block_1, buf)
        print("Pre-read OK")
    except Exception as e:
        print(f"Pre-read FAIL: {e}")
        multitest.broadcast("DONE")
        return

    # Prepare deterministic write patterns
    pattern0 = b"BLOCK0_TEST_DATA_PATTERN_12345678"
    pattern1 = b"BLOCK1_TEST_DATA_PATTERN_87654321"

    write_buf0 = bytearray(512)
    write_buf0[0 : len(pattern0)] = pattern0

    write_buf1 = bytearray(512)
    write_buf1[0 : len(pattern1)] = pattern1

    # Consecutive writes
    print("Write block 0...")
    try:
        msc.writeblocks(test_block_0, write_buf0)
        print("Write 0 OK")
    except Exception as e:
        print(f"Write 0 FAIL: {e}")
        multitest.broadcast("DONE")
        return

    print("Write block 1...")
    try:
        msc.writeblocks(test_block_1, write_buf1)
        print("Write 1 OK")
    except Exception as e:
        print(f"Write 1 FAIL: {e}")
        multitest.broadcast("DONE")
        return

    # Verify writes
    verify_buf = bytearray(512)

    msc.readblocks(test_block_0, verify_buf)
    if verify_buf[: len(pattern0)] == pattern0:
        print("Verify 0 OK")
    else:
        print("Verify 0 FAIL")

    msc.readblocks(test_block_1, verify_buf)
    if verify_buf[: len(pattern1)] == pattern1:
        print("Verify 1 OK")
    else:
        print("Verify 1 FAIL")

    multitest.broadcast("DONE")
    print("Host done")


# --- Instance 1: USB Device ---
def instance1():
    import machine

    multitest.next()
    multitest.wait("HOST_READY")

    # Configure TinyUSB with CDC+MSC
    usbd = machine.USBDevice()
    usbd.builtin_driver = machine.USBDevice.BUILTIN_CDC_MSC
    usbd.active(True)
    print("TinyUSB MSC active")

    multitest.broadcast("DEVICE_READY")
    multitest.wait("DONE")
    print("Device done")
