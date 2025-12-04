#!/usr/bin/env micropython
# Standalone pydfu application entry point for MicroPython.
# Automatically expands heap as needed for loading DFU files.

import gc
import os
import sys


def ensure_heap(min_free_bytes):
    """Ensure at least min_free_bytes are available, expanding heap if needed."""
    current_free = gc.mem_free()
    if current_free >= min_free_bytes:
        return

    # Try to add heap in 1MB chunks until we have enough
    chunk_size = 1024 * 1024  # 1MB chunks

    while gc.mem_free() < min_free_bytes:
        try:
            gc.add_heap(chunk_size)
        except (MemoryError, AttributeError):
            # MemoryError: system can't allocate more
            # AttributeError: gc.add_heap not available on this port
            break


def extract_native_libs():
    """On Windows, extract native DLLs from romfs to a temp directory.

    Windows cannot load DLLs directly from the romfs VFS, so we need to
    extract them to a real filesystem location. Returns the path to the
    extracted DLL directory, or None if not needed.
    """
    if sys.platform != "win32":
        return None

    # Check if libusb DLL exists in romfs
    dll_name = "libusb-1.0.dll"
    romfs_dll = "/rom/lib/usb/" + dll_name

    try:
        os.stat(romfs_dll)
    except OSError:
        # DLL not in romfs, assume it's available on system PATH
        return None

    # Create temp directory for extracted DLLs
    # Use a consistent name so we don't litter temp with copies
    temp_base = os.getenv("TEMP") or os.getenv("TMP") or "."
    extract_dir = temp_base + "/micropython_native_libs"

    try:
        os.mkdir(extract_dir)
    except OSError:
        pass  # Directory may already exist

    # Extract the DLL
    dest_dll = extract_dir + "/" + dll_name
    try:
        # Check if already extracted (avoid re-copying on every run)
        os.stat(dest_dll)
    except OSError:
        # Copy from romfs to temp
        with open(romfs_dll, "rb") as src:
            with open(dest_dll, "wb") as dst:
                while True:
                    chunk = src.read(4096)
                    if not chunk:
                        break
                    dst.write(chunk)

    return extract_dir


def main():
    # Extract native libraries before importing modules that need them
    # Store the path globally so usb.core can find the extracted DLL
    native_lib_dir = extract_native_libs()
    if native_lib_dir:
        # Store in a well-known location for usb.core to find
        import usb
        usb._native_lib_dir = native_lib_dir

    # Estimate memory needed: base overhead + file size headroom
    # DFU files can be several MB, allocate extra heap upfront
    # Start with 4MB minimum free to handle typical firmware images
    ensure_heap(4 * 1024 * 1024)

    # Import pydfu after heap expansion to have room for parsing
    import pydfu

    # Run the pydfu main function
    pydfu.main()


if __name__ == "__main__":
    main()
