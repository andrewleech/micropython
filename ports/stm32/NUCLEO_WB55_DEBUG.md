# NUCLEO_WB55 Flash and Debug Guide

## Hardware Configuration

- **Board**: NUCLEO_WB55 (STM32WB55RG microcontroller)
- **Chip**: STM32WB55RG (512 KB Flash, 256 KB RAM)
- **Programmer**: ST-Link V2-1 (VID:PID = 0483:374b)
- **Debug Protocol**: SWD (Serial Wire Debug)
- **Core**: ARM Cortex-M4F

## Quick Commands

### Flash Firmware
```bash
# Using the flash script (recommended)
cd /home/corona/mpy/thread/ports/stm32
./nucleo_wb55_flash.sh

# Or manually
probe-rs download --chip STM32WB55RG --probe 0483:374b --verify build-NUCLEO_WB55/firmware.elf
probe-rs reset --chip STM32WB55RG --probe 0483:374b
```

### Start GDB Debugging Session
```bash
# Two-terminal method (recommended for interactive debugging)
# Terminal 1: Start GDB server
cd /home/corona/mpy/thread/ports/stm32
probe-rs gdb --chip STM32WB55RG --probe 0483:374b --gdb-connection-string 127.0.0.1:1337

# Terminal 2: Connect GDB client
cd /home/corona/mpy/thread/ports/stm32
arm-none-eabi-gdb -x build-NUCLEO_WB55/debug.gdb build-NUCLEO_WB55/firmware.elf

# Or use the wrapper script
./nucleo_wb55_gdb.sh both        # Start server + attach client
./nucleo_wb55_gdb.sh start-server # Server only
./nucleo_wb55_gdb.sh attach      # Client only (server must be running)
```

### Verify Probe Connection
```bash
# List all connected probes
probe-rs list

# Get target chip info
probe-rs info --chip STM32WB55RG --probe 0483:374b
```

## GDB Debugging Workflow

### Basic Debugging Commands

Once connected to GDB:
```gdb
# Start execution
(gdb) continue

# Set additional breakpoints
(gdb) break mp_init
(gdb) break mp_thread_init

# Step through code
(gdb) step      # Step into functions
(gdb) next      # Step over functions
(gdb) finish    # Step out of function

# Examine state
(gdb) info registers
(gdb) backtrace
(gdb) print variable_name
(gdb) x/32x $sp    # Examine stack

# Reset device
(gdb) monitor reset halt    # Reset and halt at entry
(gdb) monitor reset         # Reset and run
```

### Crash Analysis

When a crash occurs:
```gdb
(gdb) crash_info    # Custom command defined in debug.gdb
```

This displays:
- All register values
- Stack backtrace
- Stack memory dump
- Disassembly around crash point
- Local variables and arguments

### Exception Handlers

The debug.gdb script sets breakpoints on:
- `main` - Entry point
- `HardFault_Handler` - General faults
- `MemManage_Handler` - Memory protection faults
- `BusFault_Handler` - Bus access faults
- `UsageFault_Handler` - Instruction/state faults

### Manual Flash from GDB

If you need to reflash from within GDB:
```gdb
(gdb) load              # Flash the firmware
(gdb) monitor reset halt # CRITICAL: Reset after load
(gdb) continue          # Start execution
```

## Debugging MicroPython with Zephyr Threading

### Thread-Specific Debugging

```gdb
# Break at thread initialization
(gdb) break mp_thread_init
(gdb) break zephyr_thread_entry

# Examine thread state
(gdb) info threads      # If RTOS awareness is available
(gdb) print k_current_get()  # Current Zephyr thread

# Watch for context switches
(gdb) break z_swap
```

### Common Issues and Solutions

#### Issue: Firmware crashes immediately after boot

**Investigation:**
```gdb
(gdb) break Reset_Handler    # First instruction
(gdb) break SystemInit        # Clock initialization
(gdb) break __libc_init_array # Static initializers
(gdb) break main
(gdb) continue
```

Step through each stage to find where crash occurs.

#### Issue: Timing-sensitive bug (works with debugger, fails without)

**Workaround:**
```bash
# Use probe-rs run for less invasive debugging
probe-rs run --chip STM32WB55RG --probe 0483:374b --catch-hardfault build-NUCLEO_WB55/firmware.elf
```

This catches hardfaults without GDB overhead.

#### Issue: Stack overflow

**Check stack usage:**
```gdb
(gdb) break deep_function
(gdb) info registers sp
(gdb) print/x &__stack_start__
(gdb) print/x &__stack_end__
(gdb) backtrace 100
```

#### Issue: Uninitialized variables

**Check BSS section:**
```gdb
(gdb) break main
(gdb) x/256x &__bss_start__  # Should be all zeros
```

## Advanced Debugging Techniques

### RTT (Real-Time Transfer) Logging

If firmware has RTT support:
```bash
# Flash and monitor RTT output
probe-rs run --chip STM32WB55RG --probe 0483:374b build-NUCLEO_WB55/firmware.elf
```

RTT output appears in terminal without affecting timing.

### Hardware Watchpoints

```gdb
# Watch for writes to variable
(gdb) watch global_variable

# Watch for reads
(gdb) rwatch global_variable

# Watch for any access
(gdb) awatch global_variable
```

STM32WB55 typically has 4 hardware watchpoints available.

### Conditional Breakpoints

```gdb
# Break only when condition is true
(gdb) break function_name if (error_count > 5)

# Break on Nth iteration
(gdb) break loop_body
(gdb) commands
>silent
>set $counter = $counter + 1
>if $counter >= 100
>  printf "Hit on iteration %d\n", $counter
>else
>  continue
>end
>end
```

### Memory Dump

```bash
# Read flash contents
probe-rs read --chip STM32WB55RG --probe 0483:374b 0x08000000 1024 > flash_dump.bin

# Compare with ELF
arm-none-eabi-objcopy -O binary build-NUCLEO_WB55/firmware.elf firmware.bin
diff flash_dump.bin firmware.bin
```

## Troubleshooting probe-rs

### Connection Issues

**Problem:** "No probe found"
```bash
# Check USB connection
lsusb | grep ST

# Check udev permissions (Linux)
groups  # Should include plugdev or dialout
```

**Problem:** "Target not responding"
```bash
# Try connect under reset
probe-rs download --chip STM32WB55RG --probe 0483:374b --connect-under-reset --verify build-NUCLEO_WB55/firmware.elf
```

**Problem:** "Timeout during programming"
```bash
# Lower SWD speed
probe-rs download --chip STM32WB55RG --probe 0483:374b --speed 1000 --verify build-NUCLEO_WB55/firmware.elf
```

### Flash Issues

**Problem:** "Verification failed"
```bash
# Erase entire chip first
probe-rs download --chip STM32WB55RG --probe 0483:374b --chip-erase --verify build-NUCLEO_WB55/firmware.elf
```

**Problem:** "Protected flash"
```bash
# WARNING: This may erase security keys
probe-rs download --chip STM32WB55RG --probe 0483:374b --allow-erase-all --verify build-NUCLEO_WB55/firmware.elf
```

## Chip-Specific Information

### Memory Map (STM32WB55RG)
```
0x08000000 - 0x0807FFFF : Flash (512 KB)
0x20000000 - 0x2003FFFF : SRAM1 (192 KB)
0x20030000 - 0x20040000 : SRAM2a (32 KB)
0x20040000 - 0x20050000 : SRAM2b (32 KB)
```

### Firmware Size Limits
```
Current firmware: 395,400 bytes (.text) + 368 bytes (.data) = ~386 KB
Flash available:  512 KB
Remaining:        ~126 KB
```

## File Locations

- **Firmware ELF**: `/home/corona/mpy/thread/ports/stm32/build-NUCLEO_WB55/firmware.elf`
- **GDB Script**: `/home/corona/mpy/thread/ports/stm32/build-NUCLEO_WB55/debug.gdb`
- **Flash Script**: `/home/corona/mpy/thread/ports/stm32/nucleo_wb55_flash.sh`
- **Debug Script**: `/home/corona/mpy/thread/ports/stm32/nucleo_wb55_gdb.sh`
- **This Guide**: `/home/corona/mpy/thread/ports/stm32/NUCLEO_WB55_DEBUG.md`

## Quick Reference: probe-rs Commands

```bash
# List probes
probe-rs list

# Get chip info
probe-rs info --chip STM32WB55RG --probe 0483:374b

# Flash firmware
probe-rs download --chip STM32WB55RG --probe 0483:374b --verify firmware.elf

# Verify existing flash
probe-rs verify --chip STM32WB55RG --probe 0483:374b firmware.elf

# Reset target
probe-rs reset --chip STM32WB55RG --probe 0483:374b

# Read memory
probe-rs read --chip STM32WB55RG --probe 0483:374b <ADDRESS> <LENGTH>

# Start GDB server
probe-rs gdb --chip STM32WB55RG --probe 0483:374b --gdb-connection-string 127.0.0.1:1337

# Flash and run with RTT
probe-rs run --chip STM32WB55RG --probe 0483:374b firmware.elf
```

## Resources

- [probe-rs Documentation](https://probe.rs/)
- [STM32WB55 Reference Manual](https://www.st.com/resource/en/reference_manual/rm0434-multiprotocol-wireless-32bit-mcu-armbased-cortexm4-with-fpu-bluetooth-lowenergy-and-802154-radio-solution-stmicroelectronics.pdf)
- [ARM Cortex-M4 Debug Guide](https://developer.arm.com/documentation/ddi0439/b/Debug)
