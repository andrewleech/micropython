# RTT Native Module for MicroPython

This native module provides a stream interface to SEGGER's RTT (Real-Time Transfer) library, enabling fast, non-blocking debug communication through J-Link and other compatible debug probes.

## What is RTT?

RTT (Real-Time Transfer) is a technology developed by SEGGER that enables real-time communication between a debugger and target hardware without affecting the target's runtime behavior. Unlike traditional UART or SWO, RTT:

- **Non-blocking**: Output doesn't stall the target
- **Bidirectional**: Supports both input and output
- **Fast**: Minimal overhead on target CPU
- **Multi-channel**: Supports multiple data streams
- **Works everywhere**: No special hardware pins required

## Features

- **Stream Protocol**: Full MicroPython stream interface with read/write/ioctl support
- **Multi-channel**: Support for multiple RTT channels
- **Context Manager**: Use with `with` statement for clean resource handling
- **Non-blocking I/O**: Reads and writes don't block target execution
- **Static Module**: Uses the new MicroPython static module definition system

## RTT Library Modifications

This module uses modified versions of the SEGGER RTT library files to ensure compatibility with MicroPython's native module system. The original RTT library uses global static variables that are placed in the BSS section, which is not supported in MicroPython native modules.

### Files and Modifications

- **SEGGER_RTT_patched.h**: Modified header with dynamic allocation interface
- **SEGGER_RTT_patched.c**: Implementation using heap-based control block allocation
- **SEGGER_RTT_Conf.h**: Configuration file (minimal changes)

### Key Changes Made

1. **Global Control Block**: Replaced static `_SEGGER_RTT` with dynamically allocated pointer `_SEGGER_RTT_PTR`
2. **Initialization Functions**: Added `RTT_InitControlBlock()`, `RTT_GetControlBlock()`, and `RTT_FreeControlBlock()`
3. **Memory Management**: All control block access now goes through the pointer with proper initialization checks
4. **Comment Headers**: Added detailed documentation of modifications for future reference

**Original Source**: https://github.com/SEGGERMicro/RTT/tree/main/RTT
**License**: BSD 3-Clause (preserved from original)

## Building

```bash
cd examples/natmod/rtt
make ARCH=x64  # or your target architecture
```

Supported architectures: `x86`, `x64`, `armv7m`, `xtensa`, `xtensawin`, `rv32imc`

The build now uses the locally patched RTT files instead of downloading from the SEGGER repository.

## Usage

### Basic Usage

```python
import rtt

# Initialize RTT system
rtt.init()

# Create RTT stream (channel 0 is default terminal)
stream = rtt.RTTStream(0)

# Write data
stream.write(b"Hello from MicroPython!\\n")
stream.write("Debug message\\n")

# Read data (non-blocking)
data = stream.read(100)
print(f"Received: {data}")
```

### Context Manager

```python
import rtt

# Use with context manager for automatic cleanup
with rtt.RTTStream() as stream:
    stream.write(b"Debug output\\n")

    # Check for available data
    if rtt.has_data(0):
        data = stream.read(100)
        print(f"Received: {data}")
```

### Multi-channel Communication

```python
import rtt

# Channel 0: Terminal (default)
terminal = rtt.RTTStream(0)
terminal.write("Terminal message\\n")

# Channel 1: Custom data stream
data_stream = rtt.RTTStream(1)
data_stream.write(b"Binary data...")

# Channel 2: Another custom stream
log_stream = rtt.RTTStream(2)
log_stream.write("Log message\\n")
```

### RTT as REPL Terminal

Enable interactive Python debugging through your debug probe:

```python
import rtt
import os

# Setup RTT REPL on channel 1
rtt.init()
rtt_repl = rtt.RTTStream(1)
os.dupterm(rtt_repl, 1)  # Add as additional REPL endpoint

print("RTT REPL active on channel 1 - connect with debugger!")
```

Now you can connect with J-Link RTT Viewer or your debugger and get full Python REPL access without using any UART pins!

### Stream Protocol Methods

```python
import rtt

stream = rtt.RTTStream(0)

# Standard stream methods
data = stream.read(size)           # Read up to size bytes
count = stream.readinto(buffer)    # Read into existing buffer
count = stream.write(data)         # Write bytes/string
line = stream.readline()           # Read line (blocking)

# Context management
stream.__enter__()                 # Enter context
stream.__exit__(exc_type, exc_val, exc_tb)  # Exit context
```

### Utility Functions

```python
import rtt

# Check for available data
bytes_available = rtt.has_data(0)      # Channel 0
bytes_available = rtt.has_data(1)      # Channel 1

# Check available write space
space = rtt.write_space(0)             # Channel 0
space = rtt.write_space(1)             # Channel 1

# Initialize RTT system explicitly
rtt.init()
```

## API Reference

### Classes

#### `RTTStream(channel=0)`

Create an RTT stream object for the specified channel.

**Parameters:**
- `channel` (int, optional): RTT channel number (default: 0)

**Methods:**
- `read(size)`: Read up to `size` bytes (non-blocking)
- `readinto(buffer)`: Read into existing buffer
- `write(data)`: Write bytes or string
- `readline()`: Read a line (may block)
- `close()`: Close stream (no-op for RTT)

### Functions

#### `init()`

Initialize the RTT system. Called automatically when needed, but can be called explicitly.

#### `has_data(channel=0)`

Check how many bytes are available for reading on the specified channel.

**Parameters:**
- `channel` (int, optional): RTT channel number (default: 0)

**Returns:**
- `int`: Number of bytes available

#### `write_space(channel=0)`

Get available write buffer space for the specified channel.

**Parameters:**
- `channel` (int, optional): RTT channel number (default: 0)

**Returns:**
- `int`: Available write space in bytes

## RTT Setup

### Hardware Requirements

- Debug probe with RTT support (J-Link, ST-Link v3, etc.)
- Target microcontroller with ARM Cortex-M or similar core
- Debug connection (SWD/JTAG)

### Debugger Setup

#### J-Link

Use J-Link RTT Viewer or integrate with your debugger:

```bash
# J-Link RTT Viewer
JLinkRTTViewer

# Or connect via telnet
JLinkRTTClient
```

#### GDB + OpenOCD

Configure OpenOCD with RTT support and connect via GDB.

#### IDE Integration

Many IDEs support RTT:
- SEGGER Embedded Studio
- IAR Embedded Workbench
- Keil µVision
- Visual Studio Code (with extensions)

## Performance

RTT provides excellent performance characteristics:

- **Minimal overhead**: ~1-2 CPU cycles per byte
- **Non-blocking**: Target never waits for debugger
- **High throughput**: Limited mainly by debug probe speed
- **Real-time**: Suitable for time-critical applications

## Example Applications

### Debug Logging

```python
import rtt

debug = rtt.RTTStream(0)

def log(level, message):
    debug.write(f"[{level}] {message}\\n")

log("INFO", "System initialized")
log("DEBUG", f"Variable value: {some_var}")
log("ERROR", "Something went wrong!")
```

### Data Streaming

```python
import rtt
import time

data_stream = rtt.RTTStream(1)

# Stream sensor data
while True:
    sensor_value = read_sensor()
    data_stream.write(f"{time.ticks_ms()},{sensor_value}\\n")
    time.sleep(0.1)
```

### Interactive Debug Console

```python
import rtt

console = rtt.RTTStream(0)

while True:
    if rtt.has_data(0):
        command = console.readline().strip()
        if command:
            result = process_command(command)
            console.write(f"Result: {result}\\n")
```

### RTT as REPL Endpoint

Use `os.dupterm()` to add RTT as an additional REPL endpoint, enabling interactive Python debugging through your debug probe:

```python
import rtt
import os

# Initialize RTT
rtt.init()

# Create RTT stream for REPL on channel 1 (leave channel 0 for logging)
rtt_repl = rtt.RTTStream(1)

# Add RTT as a REPL endpoint
os.dupterm(rtt_repl, 1)  # Slot 1 (slot 0 is usually UART)

print("RTT REPL enabled on channel 1!")
print("Connect with your debugger to interact with Python")

# Your main application continues here...
# The REPL will be available through RTT channel 1
while True:
    # Your application logic
    import time
    time.sleep(1)
```

**Using RTT REPL:**

1. **Connect debugger** (J-Link RTT Viewer, GDB, etc.)
2. **Open RTT channel 1** in your debugger
3. **Interactive Python** - Full REPL access through debug probe!

```
>>> import machine
>>> machine.freq()
240000000
>>> x = [1, 2, 3]
>>> len(x)
3
>>> help(rtt)
```

**Advanced REPL Setup:**

```python
import rtt
import os
import sys

def setup_rtt_repl():
    """Setup RTT REPL with error handling and status output."""
    try:
        rtt.init()

        # Use channel 2 for REPL, reserve 0 for logs, 1 for data
        rtt_repl = rtt.RTTStream(2)

        # Add as secondary REPL (slot 1)
        old_term = os.dupterm(rtt_repl, 1)

        # Send welcome message to RTT REPL channel
        rtt_repl.write("\\n=== MicroPython RTT REPL Active ===\\n")
        rtt_repl.write(f"Python {sys.version}\\n")
        rtt_repl.write("Type help() for more information.\\n>>> ")

        print(f"RTT REPL enabled on channel 2 (previous: {old_term})")
        return rtt_repl

    except Exception as e:
        print(f"Failed to setup RTT REPL: {e}")
        return None

# Setup RTT REPL at startup
rtt_repl = setup_rtt_repl()

# Optional: Remove RTT REPL later
def disable_rtt_repl():
    if rtt_repl:
        os.dupterm(None, 1)  # Remove from slot 1
        rtt_repl.close()
        print("RTT REPL disabled")
```

**Benefits of RTT REPL:**
- **No pins required** - Uses existing debug connection
- **Non-blocking** - Doesn't interfere with real-time operation
- **Fast** - High-speed communication through debug probe
- **Simultaneous** - Multiple RTT channels for logs + REPL + data
- **Remote debugging** - Debug embedded devices through probe

**Debugger Support:**
- **J-Link RTT Viewer** - Connect to specific RTT channels
- **OpenOCD + GDB** - RTT support with `monitor rtt` commands
- **IDE Integration** - Many IDEs support RTT channels directly

## Troubleshooting

### Common Issues

**Module import fails:**
- Ensure the module was built for the correct architecture
- Check that the .mpy file is in the Python path

**No RTT output visible:**
- Verify debug probe supports RTT
- Check that RTT is enabled in debugger
- Ensure target is running (not halted)

**Data corruption:**
- Check RTT buffer sizes
- Verify single-threaded access to RTT streams
- Consider using different channels for different data types

### Debug Tips

1. Start with simple write operations
2. Use J-Link RTT Viewer for initial testing
3. Check `has_data()` before reading
4. Monitor buffer usage with `write_space()`
5. Use different channels for different purposes

## License

This module uses SEGGER's RTT library, which is provided under their license terms. The MicroPython integration code follows the MicroPython license.

## Contributing

Report issues and contribute improvements via the MicroPython repository. When reporting issues, include:

- Target architecture
- Debug probe type
- MicroPython version
- Complete error messages