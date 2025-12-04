# pydfu Standalone Executable

A self-contained MicroPython executable that runs `pydfu.py` for flashing STM32 devices via DFU, with all dependencies bundled into a single binary.

## Overview

This project combines several MicroPython features to create a standalone DFU flashing tool:

- **RomFS**: Read-only filesystem embedded directly in the executable binary
- **pyusb**: Pure-Python USB library using FFI to access libusb
- **gc.add_heap()**: Runtime heap expansion for handling large DFU files
- **mpy-cross**: Precompilation of Python source to bytecode for smaller size

The result is a single executable (~1MB) that can flash STM32 devices without requiring a Python installation.

## Components

### Application Code (`tools/pydfu_app/`)

```
tools/pydfu_app/
├── main.py          # Entry point: heap setup, Windows DLL extraction
├── manifest.py      # RomFS manifest
└── lib/
    ├── argparse.py  # Command-line argument parsing
    ├── pydfu.py     # DFU flashing logic (modified for MicroPython)
    ├── zlib.py      # Compatibility shim using binascii.crc32
    └── usb/         # pyusb library
        ├── __init__.py
        ├── core.py
        ├── control.py
        ├── util.py
        └── libusb-1.0.dll  # Windows native library
```

### MicroPython Features Used

**RomFS** (`ports/unix/main.c`, `ports/windows/vfs_rom_ioctl.c`):
- Mounts an embedded read-only filesystem at `/rom`
- Auto-executes `/rom/main.py` or `/rom/main.mpy` on startup
- Embedded via objcopy at link time using `ROMFS_IMG=` make variable

**gc.add_heap()** (`py/modgc.c`):
- Dynamically expands heap at runtime
- Required because DFU file parsing needs significant memory
- Called from `main.py` to allocate 4MB heap

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

# Install mpy_cross Python module for .py to .mpy compilation
pip install mpy-cross
```

### Unix Build

```bash
cd ports/unix

# Build the romfs image (compiles .py to .mpy automatically)
python3 -c "
import mpy_cross
mpy_cross.mpy_cross = '$(pwd)/../../mpy-cross/build/mpy-cross'
import sys
sys.argv = ['mpremote', 'romfs', 'build', '../../tools/pydfu_app']
from mpremote.main import main
main()
"
mv ../../tools/pydfu_app.romfs romfs.img

# Build pydfu with embedded romfs
make PROG=pydfu ROMFS_IMG=romfs.img
```

### Windows Build (Cross-compile from Linux)

```bash
cd ports/windows

# Build romfs.img same as Unix (see above)

# Build libffi dependency first
make deplibs CROSS_COMPILE=x86_64-w64-mingw32- MICROPY_PY_FFI=1

# Cross-compile with MinGW (FFI required for pyusb)
make CROSS_COMPILE=x86_64-w64-mingw32- MICROPY_PY_FFI=1 PROG=pydfu ROMFS_IMG=romfs.img
```

## Usage

The resulting executable works like the original `pydfu.py`:

```bash
# List DFU devices
./pydfu -l

# Flash a DFU file
./pydfu -u firmware.dfu

# Mass erase and flash
./pydfu -m -u firmware.dfu
```

All arguments after the executable name are passed to the pydfu application.

## How It Works

1. **Startup**: MicroPython initializes and mounts the embedded RomFS at `/rom`
2. **Detection**: `main.c` checks for `/rom/main.py` or `/rom/main.mpy`
3. **Execution**: For `.mpy`, uses import mechanism; for `.py`, uses lexer
4. **Heap Expansion**: `main.py` calls `gc.add_heap()` to allocate working memory
5. **DLL Setup** (Windows only): Extracts libusb DLL to temp directory
6. **pydfu**: Imports and runs the DFU flashing logic with `sys.argv`

## File Size Breakdown

| Component | Approximate Size |
|-----------|------------------|
| MicroPython core | ~780 KB |
| RomFS (with .mpy) | ~190 KB |
| **Total** | **~970 KB** |

Using `.mpy` precompilation reduces the romfs from ~216 KB to ~188 KB.
