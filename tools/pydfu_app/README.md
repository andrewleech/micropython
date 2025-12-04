# pydfu Standalone Executable

A self-contained MicroPython executable that runs `pydfu.py` for flashing STM32 devices via DFU, with all dependencies bundled into a single binary.

## Overview

This project combines several MicroPython features to create a standalone DFU flashing tool:

- **Frozen Modules**: Python code compiled directly into the binary
- **RomFS** (Windows only): Embedded filesystem for native DLL
- **pyusb**: Pure-Python USB library using FFI to access libusb
- **gc.add_heap()**: Runtime heap expansion for handling large DFU files

The result is a single executable (~850 KB) that can flash STM32 devices without requiring a Python installation.

## Components

### Application Code (`tools/pydfu_app/`)

```
tools/pydfu_app/
├── main.py              # Entry point: heap setup, Windows DLL extraction
├── manifest_frozen.py   # Frozen module manifest (Python code)
├── lib/
│   ├── pydfu.py         # DFU flashing logic
│   └── zlib.py          # Compatibility shim using binascii.crc32
└── romfs_windows/       # Directory for Windows romfs build
    └── lib/usb/
        └── libusb-1.0.dll  # Windows native library
```

### MicroPython Features Used

**Frozen Modules** (`manifest_frozen.py`):
- Python code (pydfu, pyusb, argparse) compiled into the binary
- Uses micropython-lib for standard modules
- main.py detected and auto-executed on startup

**RomFS** (Windows only):
- Embedded read-only filesystem for native DLL
- Required because Windows cannot load DLLs from frozen modules
- Mounted at `/rom` on startup

**gc.add_heap()** (`py/modgc.c`):
- Dynamically expands heap at runtime
- Required because DFU file parsing needs significant memory

**FFI** (`ports/unix/modffi.c`, `ports/windows/modffi.c`):
- Foreign Function Interface for calling native libraries
- Used by pyusb to access libusb-1.0

### Windows DLL Handling

Windows cannot load DLLs from a virtual filesystem. The `main.py` entry point:
1. Detects Windows platform
2. Extracts `libusb-1.0.dll` from `/rom/lib/usb/` to a temp directory
3. Stores the path in `usb._native_lib_dir` for `usb/core.py` to find

## Building

### Prerequisites

```bash
# Build mpy-cross (must match MicroPython version)
cd mpy-cross
make
```

### Unix Build

```bash
cd ports/unix

# Build pydfu with frozen modules
make FROZEN_MANIFEST=../../tools/pydfu_app/manifest_frozen.py \
     PROG=pydfu \
     CFLAGS_EXTRA="-DMICROPY_FROZEN_MAIN_MODULE=1"
```

### Windows Build (Cross-compile from Linux with dockcross)

```bash
cd ports/windows

# Build romfs image with DLL only (from romfs_windows directory)
python3 -m mpremote romfs build ../../tools/pydfu_app/romfs_windows
mv ../../tools/pydfu_app/romfs_windows.romfs romfs.img

# Using dockcross/windows-static-x64-posix container
CROSS=x86_64-w64-mingw32.static.posix-

# Build libffi dependency first
docker run --rm -v "$PWD/../..:$PWD/../.." -w "$PWD" --user "$(id -u):$(id -g)" \
    dockcross/windows-static-x64-posix \
    make deplibs CROSS_COMPILE=$CROSS MICROPY_PY_FFI=1

# Cross-compile with MinGW (FFI required for pyusb)
docker run --rm -v "$PWD/../..:$PWD/../.." -w "$PWD" --user "$(id -u):$(id -g)" \
    dockcross/windows-static-x64-posix \
    make CROSS_COMPILE=$CROSS \
         MICROPY_PY_FFI=1 \
         FROZEN_MANIFEST=../../tools/pydfu_app/manifest_frozen.py \
         PROG=pydfu \
         ROMFS_IMG=romfs.img \
         CFLAGS_EXTRA="-DMICROPY_FROZEN_MAIN_MODULE=1"
```

## Usage

The resulting executable works like the original `pydfu.py`:

```bash
# Show help
./pydfu -h

# List DFU devices
./pydfu -l

# Flash a DFU file
./pydfu -u firmware.dfu

# Mass erase and flash
./pydfu -m -u firmware.dfu
```

All arguments after the executable name are passed to the pydfu application.

## How It Works

1. **Startup**: MicroPython initializes
2. **Detection**: `main.c` checks for frozen `main.py` module
3. **Execution**: Imports and runs frozen main module
4. **Heap Expansion**: `main.py` calls `gc.add_heap()` to allocate working memory
5. **DLL Setup** (Windows only): Extracts libusb DLL from romfs to temp directory
6. **pydfu**: Runs the DFU flashing logic with `sys.argv`

## File Size Breakdown

| Platform | Binary Size |
|----------|-------------|
| Unix (pydfu) | ~870 KB |
| Windows (pydfu.exe) | ~750 KB |

The Windows binary is smaller because the romfs with libusb DLL (~170 KB) is embedded
as read-only data rather than extra code.
