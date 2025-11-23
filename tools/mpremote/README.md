# mpremote -- MicroPython remote control

This CLI tool provides an integrated set of utilities to remotely interact with
and automate a MicroPython device over a serial connection.

The simplest way to use this tool is:

    mpremote

This will automatically connect to a USB serial port and provide an interactive REPL.

The full list of supported commands are:

    mpremote connect <device>         -- connect to given device
                                         device may be: list, auto, id:x, port:x
                                         or any valid device name/path
    mpremote disconnect               -- disconnect current device
    mpremote mount <local-dir>        -- mount local directory on device
                                         options:
                                             --unsafe-links / -l: follow symlinks outside mount
                                             --auto-mpy / -m: auto-compile .py to .mpy (default)
                                             --no-auto-mpy: disable auto-compilation
    mpremote eval <string>            -- evaluate and print the string
    mpremote exec <string>            -- execute the string
    mpremote run <file>               -- run the given local script
    mpremote fs <command> <args...>   -- execute filesystem commands on the device
                                         command may be: cat, ls, cp, rm, mkdir, rmdir, sha256sum
                                         use ":" as a prefix to specify a file on the device
    mpremote repl                     -- enter REPL
                                         options:
                                             --capture <file>
                                             --inject-code <string>
                                             --inject-file <file>
    mpremote mip install <package...> -- Install packages (from micropython-lib or third-party sources)
                                         options:
                                             --target <path>
                                             --index <url>
                                             --no-mpy
    mpremote help                     -- print list of commands and exit

Multiple commands can be specified and they will be run sequentially.  Connection
and disconnection will be done automatically at the start and end of the execution
of the tool, if such commands are not explicitly given.  Automatic connection will
search for the first available serial device.  If no action is specified then the
REPL will be entered.

Shortcuts can be defined using the macro system.  Built-in shortcuts are:

- a0, a1, a2, a3: connect to `/dev/ttyACM?`
- u0, u1, u2, u3: connect to `/dev/ttyUSB?`
- c0, c1, c2, c3: connect to `COM?`
- cat, ls, cp, rm, mkdir, rmdir, df: filesystem commands
- reset: reset the device
- bootloader: make the device enter its bootloader

Any user configuration, including user-defined shortcuts, can be placed in
.config/mpremote/config.py.  For example:

    # Custom macro commands
    commands = {
        "c33": "connect id:334D335C3138",
        "bl": "bootloader",
        "double x=4": {
            "command": "eval x*2",
            "help": "multiply by two"
        }
    }

Examples:

    mpremote
    mpremote a1
    mpremote connect /dev/ttyUSB0 repl
    mpremote ls
    mpremote a1 ls
    mpremote exec "import micropython; micropython.mem_info()"
    mpremote eval 1/2 eval 3/4
    mpremote mount .
    mpremote mount . exec "import local_script"
    mpremote ls
    mpremote cat boot.py
    mpremote cp :main.py .
    mpremote cp main.py :
    mpremote cp -r dir/ :
    mpremote sha256sum :main.py
    mpremote mip install aioble
    mpremote mip install github:org/repo@branch
    mpremote mip install gitlab:org/repo@branch

## Auto-MPY Compilation

When a local directory is mounted using `mpremote mount`, the tool can automatically
compile Python files to bytecode (.mpy) format on-demand. This feature:

- **Improves import performance**: .mpy files are pre-compiled and load faster
- **Transparent operation**: Automatically compiles when device requests .mpy
- **Caching**: Compiled files are cached to avoid recompilation
- **Graceful fallback**: Uses .py files if compilation fails

### Installation

Auto-compilation requires the `mpy-cross` Python package:

    pip install mpremote[mpy]

Or install `mpy-cross` separately:

    pip install mpy-cross

### Usage

    mpremote mount /path/to/code              # Auto-compile enabled (default)
    mpremote mount --no-auto-mpy /path/to/code  # Disable auto-compilation

### How it Works

1. When MicroPython tries to import a module, it checks for .py files first
2. mpremote intercepts the .py stat request and compiles to .mpy if needed
3. mpremote returns "file not found" for .py to force MicroPython to use .mpy
4. The compiled .mpy is saved both locally and in a cache directory
5. Subsequent imports use the cached .mpy file
6. Cache is automatically managed (100MB limit, LRU eviction)

### Important Limitations

When auto-mpy is enabled with `mount`, `.py` files on mounted filesystems become invisible to the device for ALL operations, not just imports.

This means:
- ✗ **`open("script.py")`** will fail (file appears not to exist)
- ✗ **`os.stat("script.py")`** will return ENOENT
- ✓ **`import script`** works (uses compiled .mpy)

**Workaround:** Disable auto-mpy if you need direct .py file access:

```bash
mpremote mount --no-auto-mpy /path/to/code
```

This limitation exists because mpremote cannot distinguish between import-related file access and regular file operations at the stat interception level.

### Requirements

- The `mpy-cross` Python package must be installed
- Device must support .mpy files (MicroPython v1.12+)
- Architecture is auto-detected from the connected device

### Cache Structure

Compiled `.mpy` files are stored in a cache directory:

**Location:** `/tmp/mpremote_mpy_cache/`

**Cache key format:** `_<path>_<mtime>_<arch>.mpy`
- Example: `_tmp_mpy_test_script_py_1732456789_0_xtensawin.mpy`
- `<path>`: Source .py file path (sanitized, slashes/dots → underscores)
- `<mtime>`: Modification timestamp of .py file
- `<arch>`: Target architecture (e.g., xtensawin, armv7m)

**Cache behavior:**
- Automatic LRU eviction when cache exceeds 100MB
- Cached .mpy is reused if source .py file hasn't changed (mtime check)
- Different architectures maintain separate cache entries
- Compiled .mpy files are ALSO copied to the mounted directory alongside .py files
- Example: `/path/to/code/module.py` → `/path/to/code/module.mpy` + cache entry

### Troubleshooting

If auto-compilation isn't working:
- Check if `mpy-cross` is installed: `python -c "import mpy_cross"`
- Use verbose mode to see compilation messages: `mpremote -v mount /path/to/code`
- Manually disable if needed: `mpremote mount --no-auto-mpy /path/to/code`
- Check cache directory: `ls -lh /tmp/mpremote_mpy_cache/`
- Clear cache if needed: `rm -rf /tmp/mpremote_mpy_cache/`
