# Windows Cross-Build with dockcross

Cross-compiling the MicroPython Windows port from Linux using dockcross/mingw-w64.

## Prerequisites

```bash
# Pull the dockcross image
docker pull dockcross/windows-static-x64-posix

# Initialize required submodules
make -C ports/windows submodules
```

## Basic Build (without FFI)

```bash
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)/ports/windows" \
  --user $(id -u):$(id -g) dockcross/windows-static-x64-posix \
  make CROSS_COMPILE=x86_64-w64-mingw32.static.posix-
```

Output: `ports/windows/build-standard/micropython.exe`

## Build with FFI Support

FFI support requires libffi, which is built from source.

```bash
# Initialize libffi submodule
git submodule update --init lib/libffi

# Build libffi dependency first
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)/ports/windows" \
  --user $(id -u):$(id -g) dockcross/windows-static-x64-posix \
  make deplibs CROSS_COMPILE=x86_64-w64-mingw32.static.posix- MICROPY_PY_FFI=1

# Build MicroPython with FFI
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)/ports/windows" \
  --user $(id -u):$(id -g) dockcross/windows-static-x64-posix \
  make CROSS_COMPILE=x86_64-w64-mingw32.static.posix- MICROPY_PY_FFI=1
```

## Clean Build

```bash
docker run --rm -v "$(pwd):$(pwd)" -w "$(pwd)/ports/windows" \
  --user $(id -u):$(id -g) dockcross/windows-static-x64-posix \
  make clean CROSS_COMPILE=x86_64-w64-mingw32.static.posix-
```

## Toolchain Details

- Image: `dockcross/windows-static-x64-posix`
- Compiler: GCC 11.4.0 (MXE)
- Target: `x86_64-w64-mingw32.static.posix`
- Threading: POSIX threads
- Linking: Static

## FFI Implementation Notes

The Windows FFI module (`ports/windows/modffi.c`) uses native Windows APIs:
- `LoadLibraryA()` for loading DLLs
- `GetProcAddress()` for symbol lookup
- `FreeLibrary()` for cleanup
- `GetModuleHandle(NULL)` for main executable symbols

libffi is cross-compiled from the `lib/libffi` submodule using autotools.

## pyusb Package

The `lib/micropython-lib/unix-ffi/pyusb/` package provides PyUSB-compatible USB access via libusb-1.0.

### Supported APIs

**usb.core:**
- `find(idVendor=, idProduct=, find_all=, custom_match=)` - Find USB devices
- `Device.set_configuration()`, `Device.ctrl_transfer()`, `Device.configurations()`
- `Device.bus`, `Device.address`, `Device.idVendor`, `Device.idProduct`
- `Configuration.interfaces()`, `Configuration[(intf_num, alt_setting)]`
- `Interface.bInterfaceNumber`, `.bAlternateSetting`, `.bInterfaceClass`, `.bInterfaceSubClass`, `.bInterfaceProtocol`

**usb.util:**
- `claim_interface()`, `get_string()`, `dispose_resources()`

**usb.control:**
- `get_descriptor()`

### Windows DLL

The package bundles `libusb-1.0.dll` (from PyPI `libusb-package` 1.0.26.3). The DLL is loaded from the module directory automatically.

### Usage with pydfu.py

```bash
# Copy pyusb package to a location MicroPython can find
cp -r lib/micropython-lib/unix-ffi/pyusb/usb /path/to/libs/

# Run pydfu
micropython.exe -m pydfu -l
```

## RomFS Support

The Windows and Unix ports support embedding a read-only filesystem that auto-mounts at `/rom` on startup. Files in `/rom/lib` are automatically added to the module search path.

### Creating a RomFS Image

```bash
cd ports/windows  # or ports/unix
make romfs ROMFS_DIR=path/to/directory
```

This creates `romfs.img` in the port directory. Place `romfs.img` in the same directory as the micropython executable to enable auto-mount.

### Example

```bash
# Create a directory with Python modules
mkdir -p mylibs/lib
echo 'def hello(): print("Hello from RomFS!")' > mylibs/lib/greeting.py

# Build the RomFS image
cd ports/windows
make romfs ROMFS_DIR=../mylibs

# Run MicroPython (romfs.img must be in the same directory as the executable)
./build-standard/micropython.exe -c "from greeting import hello; hello()"
```

### Features

- Files are accessible at `/rom/`
- `/rom` and `/rom/lib` are automatically added to `sys.path`
- Read-only access
- No runtime filesystem required
- Ideal for bundling library dependencies

### Standalone Applications

If `/rom/main.py` exists, it is automatically executed as the main script. Command-line arguments are passed to it via `sys.argv`. This enables building standalone executable applications:

```bash
# Create application structure
mkdir -p myapp/lib
echo 'def run(): print("App running!")' > myapp/lib/app.py
cat > myapp/main.py << 'EOF'
import sys
from app import run
print("Args:", sys.argv[1:])
run()
EOF

# Build the RomFS image
cd ports/unix  # or ports/windows
python3 -c "import sys; sys.path.insert(0, '../../tools/mpremote'); \
  from mpremote.romfs import make_romfs; \
  open('romfs.img', 'wb').write(make_romfs('../../myapp/', mpy_cross=False))"

# Run as standalone app
./build-standard/micropython
# Output: Args: []
#         App running!

# Pass arguments to the app
./build-standard/micropython --config myfile.txt -v
# Output: Args: ['--config', 'myfile.txt', '-v']
#         App running!
```

The executable + romfs.img together form a complete standalone Python application. The `-h`, `-i`, `-v`, `-O`, and `-X` flags are still processed by MicroPython; all other arguments are passed to `main.py`.
