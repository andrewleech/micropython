
Current Development Epic: Add Zephyr BLE stack to all ports.

## Progress Status

### Completed
1. Created `extmod/zephyr_ble/` directory structure
2. Moved `modbluetooth_zephyr.c` from `ports/zephyr` to `extmod/zephyr_ble`
3. Created build files: `zephyr_ble.mk` and `zephyr_ble.cmake`
4. Implemented `k_timer` abstraction in `extmod/zephyr_ble/hal/zephyr_ble_timer.{h,c}`
5. Completed dependency analysis → See `extmod/zephyr_ble/DEPENDENCIES_ANALYSIS.md`
6. Researched NimBLE/BTstack scheduler integration patterns
7. Designed OS adapter layer → See `extmod/zephyr_ble/OS_ADAPTER_DESIGN.md`
8. **MILESTONE 1**: Implemented `k_work` abstraction - `extmod/zephyr_ble/hal/zephyr_ble_work.{h,c}`
   - Event queue with global linked list (like NimBLE)
   - k_work_delayable combines work + k_timer
   - Processed by `mp_bluetooth_zephyr_work_process()`
   - Handles 161 work queue operations in Zephyr BLE
9. **MILESTONE 2**: Implemented `k_sem` abstraction - `extmod/zephyr_ble/hal/zephyr_ble_sem.{h,c}`
   - Busy-wait pattern while processing work queues and HCI UART
   - Handles K_NO_WAIT, K_FOREVER, and timed waits
   - Uses MICROPY_EVENT_POLL_HOOK to yield during wait
   - Handles 30 semaphore uses for HCI command synchronization
10. **MILESTONE 3**: Implemented `k_mutex` abstraction - `extmod/zephyr_ble/hal/zephyr_ble_mutex.{h,c}`
    - No-op implementation (scheduler provides implicit mutual exclusion)
    - Tracks lock count for debugging
    - Handles 15 mutex uses
11. **MILESTONE 4**: Implemented atomic ops - `extmod/zephyr_ble/hal/zephyr_ble_atomic.h`
    - k_spinlock using MICROPY_PY_BLUETOOTH_ENTER/EXIT
    - Full atomic operation API (set, get, inc, dec, add, sub, cas)
    - Atomic bit operations and pointer operations
    - Header-only implementation
12. Implemented kernel misc - `extmod/zephyr_ble/hal/zephyr_ble_kernel.{h,c}`
    - k_sleep, k_yield, k_busy_wait
    - k_uptime_get, timing functions
    - Thread info stubs

### Current Status: **CHECKPOINT - OS Adapter Complete, Integration Plan Ready**

#### Git Commits:
- `6dec1b7a79` - Directory structure, build files, k_timer, k_work, documentation
- `e2d6a9eeda` - k_sem abstraction (semaphores)
- `ef6a41a066` - k_mutex abstraction
- `e8e864e892` - Atomic ops and spinlock abstraction
- `fe1b0a51a9` - Kernel misc abstractions
- `0aa74af352` - Main polling function
- `fc8e21b5a2` - Master HAL header
- `64973ffa4e` - Integration plan document

#### Completed Milestones:
- ✅ **MILESTONE 1**: k_work abstraction (event queues)
- ✅ **MILESTONE 2**: k_sem abstraction (busy-wait semaphores)
- ✅ **MILESTONE 3**: k_mutex abstraction (no-op)
- ✅ **MILESTONE 4**: Atomic ops and spinlocks
- ✅ **MILESTONE 5**: Main polling function (`mp_bluetooth_zephyr_poll`)

#### Key Design Decisions:
- ✅ **No Zephyr kernel threads** - all code runs in scheduler
- ✅ **No source patching** - comprehensive adapter layer only
- ✅ **Proven pattern** - follows NimBLE/BTstack integration model
- ✅ **Polling-based** - work queues processed by `mp_bluetooth_hci_poll()`
- ✅ **Full stack** - targeting complete Zephyr BLE host

### Current Status: **Phase 1 Integration Complete - OS Adapter Layer Implemented**

**OS Adapter Layer: ✅ COMPLETE**

All kernel abstractions implemented (Milestones 1-5).

**RP2 Port Integration: ⚠️ INCOMPLETE**

- ✅ Build system working
- ✅ HCI transport infrastructure implemented
- ✅ Device tree infrastructure and HCI driver
- ❌ **Runtime initialization fails:** `bt_enable()` hangs/crashes
- ❌ BLE activation does not complete successfully
- ❌ No successful BLE operations achieved

**Status:** The RP2 port integration work remains incomplete. The firmware compiles but does not successfully initialize the Zephyr BLE stack at runtime.

**Phase 1 Integration: Complete**

Phase 1 wrapper infrastructure:
- ✅ Kconfig wrapper (`zephyr_ble_config.h`) with minimal configuration
- ✅ Zephyr header wrappers (`zephyr/kernel.h`, `sys/*`, `logging/log.h`)
- ✅ Using Zephyr's net_buf implementation from `lib/zephyr/lib/net_buf/`
- ✅ autoconf.h with CONFIG defines

**Implementation Strategy:**

Revised strategy that was successfully implemented:

1. **Use Zephyr headers directly** - Added `lib/zephyr/include` to include path
2. **Created `autoconf.h`** - Provides CONFIG_* defines expected by Zephyr headers
3. **Minimal wrappers only** - Only wrapped headers that conflicted or needed adaptation
4. **Selective source addition** - Added only Zephyr BLE host sources, used Zephyr's net_buf

**Git Commits (OS Adapter + Phase 1 + RP2 Port):**
- `6dec1b7a79` - Directory structure, k_timer, k_work, documentation
- `e2d6a9eeda` - k_sem
- `ef6a41a066` - k_mutex
- `e8e864e892` - Atomic ops
- `fe1b0a51a9` - Kernel misc
- `0aa74af352` - Polling function
- `fc8e21b5a2` - Master HAL header
- `64973ffa4e` - Integration plan
- `d23db585bb` - Kconfig and Zephyr header wrappers
- `14002c5099` - Fix errno handling
- `86f5e1bfd7` - Make atomic macros optional
- `d80a205b2e` - Add critical sections to k_work
- `34ef4427a3` - Add endianness verification
- `041e210dec` - Add k_mutex assertion
- `9febd852f4` - Add buffer config defines
- `be33c9405d` - Add autoconf.h
- `79c4aa241e` - Add UART HCI transport for Zephyr BLE (WIP)
- `0f68120ec4` - Add HCI driver device tree infrastructure
- `4c078dccc8` - Fix device tree DEVICE_DT_GET macro
- `7fff5fb9f8` - Add debug output for Zephyr BLE initialization
- `9bc8e1b6e3` - **Remove CYW43 init check from bt_hci_transport_setup (FIXED CRASH)**

## Flashing Raspberry Pi Pico 2 W

### Hardware Setup
- Board: Raspberry Pi Pico 2 W (RP2350 chip)
- Debug Probe: CMSIS-DAP compatible (CherryUSB CMSIS-DAP, 0d28:0204)
- Connection: Debug probe connected to Pico 2 W SWD pins

### Method 1: Flash via probe-rs (Recommended for Development)

**Build firmware:**
```bash
cd ports/rp2
make BOARD=RPI_PICO2_W BOARD_VARIANT=zephyr
```

**Flash using probe-rs agent:**
```bash
# Use the probe-rs-flash agent in Claude Code
# Specify: probe = "CherryUSB CMSIS-DAP (0d28:0204)"
#          chip = "RP2350" or "RP235x"
#          file = "ports/rp2/build-RPI_PICO2_W-zephyr/firmware.elf"
```

**Flash manually via probe-rs:**
```bash
probe-rs run --chip RP2350 --probe 0d28:0204 ports/rp2/build-RPI_PICO2_W-zephyr/firmware.elf
```

**IMPORTANT: Reset device after flash:**
```bash
probe-rs reset --chip RP2350 --probe 0d28:0204
```

**Note:** After every probe-rs download/flash operation, a `probe-rs reset` is always required for the firmware to run correctly from flash. Without this reset, the device may remain in an inconsistent state.

**Verify flash contents:**
```bash
probe-rs read --chip RP2350 --probe 0d28:0204 0x10000000 256
```

### Method 2: Flash via USB Bootloader (picotool)

**Enter bootloader mode:**
1. Disconnect USB cable
2. Hold BOOTSEL button
3. Connect USB cable while holding BOOTSEL
4. Release BOOTSEL button
5. Device appears as RP2350 Boot (2e8a:000f)

**Flash using picotool:**
```bash
cd ports/rp2
make BOARD=RPI_PICO2_W BOARD_VARIANT=zephyr
picotool load -x build-RPI_PICO2_W-zephyr/firmware.uf2
```

**Or copy UF2 to mounted drive:**
```bash
cp build-RPI_PICO2_W-zephyr/firmware.uf2 /media/*/RPI-RP2/
```

### Serial Console Access

After flashing, the device appears as:
- USB Device: MicroPython Board in FS mode (2e8a:0005)
- Serial Port: `/dev/ttyACM*` (typically /dev/ttyACM2 or /dev/ttyACM3)
- Baud Rate: 115200

Connect via:
```bash
screen /dev/ttyACM2 115200
# or
minicom -D /dev/ttyACM2 -b 115200
# or
python3 -m serial.tools.miniterm /dev/ttyACM2 115200
```

### Troubleshooting

**Device enters bootloader after reset:**
- Indicates firmware crash during early initialization
- Check for hardfault or bus fault in startup code
- Use probe-rs attach to catch fault with debugger

**No serial output:**
- Verify correct /dev/ttyACM* device (check dmesg)
- Firmware may be crashing before USB initialization
- Use debug probe to verify code execution

**probe-rs can't find device:**
- Check debug probe connection (lsusb should show 0d28:0204)
- Verify SWD wiring if using external probe
- Try specifying exact probe: `--probe 0d28:0204`

## Debugging Firmware Crashes with GDB and probe-rs

### Overview

When firmware crashes during early initialization (before USB/serial is available), use arm-none-eabi-gdb with probe-rs to debug directly via SWD.

### Method 1: probe-rs GDB Server (Recommended)

**Terminal 1 - Start GDB server:**
```bash
cd ports/rp2
probe-rs gdb --chip RP2350 --probe 0d28:0204 --gdb-connection-string 127.0.0.1:1337
```

**Terminal 2 - Connect GDB:**
```bash
arm-none-eabi-gdb build-RPI_PICO2_W-zephyr/firmware.elf

# In GDB prompt:
(gdb) target extended-remote :1337
(gdb) load                          # Flash the firmware
(gdb) monitor reset halt            # Reset and halt at entry (REQUIRED after load)
(gdb) break main                    # Set breakpoint at main()
(gdb) continue                      # Run until breakpoint
```

**Note:** The `monitor reset halt` after `load` is required for the firmware to initialize correctly.

### Method 2: probe-rs run with Attach

**Flash and immediately attach debugger:**
```bash
probe-rs run --chip RP2350 --probe 0d28:0204 --catch-hardfault build-RPI_PICO2_W-zephyr/firmware.elf
```

This will:
- Flash the firmware
- Catch hardfaults/exceptions automatically
- Show register dump and stack trace at fault

### Method 3: Attach to Running Firmware

**If firmware is already flashed:**
```bash
probe-rs attach --chip RP2350 --probe 0d28:0204
```

Or with GDB:
```bash
arm-none-eabi-gdb build-RPI_PICO2_W-zephyr/firmware.elf
(gdb) target extended-remote :1337
(gdb) monitor halt
(gdb) backtrace
```

### Key Breakpoint Locations

**Early startup (before main):**
```gdb
(gdb) break Reset_Handler           # Very first code execution
(gdb) break __libc_init_array       # C++ constructors / static initializers
(gdb) break data_init               # Data/BSS initialization
```

**MicroPython initialization:**
```gdb
(gdb) break main                    # MicroPython entry point
(gdb) break mp_init                 # MicroPython VM init
(gdb) break mp_bluetooth_init       # Bluetooth subsystem init
```

**Zephyr BLE specific:**
```gdb
(gdb) break mp_bluetooth_zephyr_port_init       # Port-specific init
(gdb) break bt_enable                            # Zephyr BLE stack enable
(gdb) break hci_cyw43_open                      # HCI driver open
(gdb) break bt_hci_transport_setup              # Controller setup
(gdb) break cyw43_bluetooth_controller_init     # CYW43 BT init
```

### Examining Crash State

**When crash occurs:**
```gdb
(gdb) info registers                # Show all ARM registers
(gdb) backtrace                     # Call stack
(gdb) frame 0                       # Select crashed frame
(gdb) list                          # Show source code at crash
(gdb) x/32x $sp                     # Dump stack
(gdb) x/32x $pc-32                  # Dump code around PC
(gdb) info locals                   # Local variables
(gdb) info args                     # Function arguments
```

**ARM exception registers:**
```gdb
# Configurable Fault Status Register (shows fault type)
(gdb) x/1x 0xE000ED28

# Hard Fault Status Register
(gdb) x/1x 0xE000ED2C

# Memory Management Fault Address Register
(gdb) x/1x 0xE000ED34

# Bus Fault Address Register
(gdb) x/1x 0xE000ED38
```

### RTT (Real-Time Transfer) for Printf Debugging

**Enable RTT output without serial:**
```c
// In code (if RTT is linked)
#include "SEGGER_RTT.h"
SEGGER_RTT_printf(0, "Reached main: %d\n", value);
```

**View RTT output:**
```bash
probe-rs run --chip RP2350 --probe 0d28:0204 firmware.elf
# RTT output appears in terminal
```

### Comparing Working vs Crashing Builds

**Generate disassembly:**
```bash
arm-none-eabi-objdump -d build-RPI_PICO2_W/firmware.elf > working.dis
arm-none-eabi-objdump -d build-RPI_PICO2_W-zephyr/firmware.elf > zephyr.dis
diff -u working.dis zephyr.dis | less
```

**Check section differences:**
```bash
arm-none-eabi-size build-RPI_PICO2_W/firmware.elf
arm-none-eabi-size build-RPI_PICO2_W-zephyr/firmware.elf
```

**Compare symbol tables:**
```bash
arm-none-eabi-nm build-RPI_PICO2_W/firmware.elf | sort > working.sym
arm-none-eabi-nm build-RPI_PICO2_W-zephyr/firmware.elf | sort > zephyr.sym
diff -u working.sym zephyr.sym | less
```

### GDB Scripting for Automated Analysis

**Create debug.gdb:**
```gdb
# Connect to target
target extended-remote :1337
load
monitor reset halt

# Set breakpoints at key locations
break Reset_Handler
break main
break mp_bluetooth_zephyr_port_init

# Configure exception catching
catch signal SIGILL
catch signal SIGSEGV
catch signal SIGBUS

# Define command to run on crash
define crash_info
    info registers
    backtrace
    x/32x $sp
    x/32i $pc-16
end

# Auto-execute crash_info on exception
commands
    crash_info
end

# Start execution
continue
```

**Run script:**
```bash
arm-none-eabi-gdb -x debug.gdb build-RPI_PICO2_W-zephyr/firmware.elf
```

### Using mpremote for Testing (Non-Crash Scenarios)

**Test working firmware:**
```bash
mpremote connect /dev/ttyACM2 exec "import sys; print(sys.version)"
mpremote connect /dev/ttyACM2 exec "import bluetooth; ble = bluetooth.BLE(); print(ble)"
mpremote connect /dev/ttyACM2 repl
```

**Run test script:**
```bash
mpremote run test_ble.py
```

### RP2 Port Investigation Notes (2025-10-12)

**Note:** The git tag `multitest-93pct-pass` exists but **does not represent actual multitest validation**. The RP2 Pico 2 W port has never successfully passed multitest validation.

**Investigation History:**
- Commit 9bc8e1b6e3: Fixed crash at 0xF0000030 by removing CYW43 init check
- Firmware builds successfully but does not complete BLE initialization at runtime
- `bt_enable()` hangs or crashes during initialization
- No successful BLE operations achieved on RP2 platform

**Status:** RP2 port integration remains a work in progress. The build system and transport infrastructure are implemented, but runtime initialization of the Zephyr BLE stack does not complete successfully.

## Flashing STM32 NUCLEO_WB55

### Hardware Setup
- Board: NUCLEO_WB55 (STM32WB55RGVx chip)
- Debug Probe: ST-Link V2-1 (built into NUCLEO board, 0483:374b)
- Connection: ST-Link integrated on board via USB

### Build and Flash

**Build firmware:**
```bash
cd ports/stm32
make BOARD=NUCLEO_WB55 MICROPY_BLUETOOTH_ZEPHYR=1 MICROPY_BLUETOOTH_NIMBLE=0
```

**Flash using probe-rs:**
```bash
# Use the probe-rs-flash agent in Claude Code
# Specify: probe = "ST-Link V2-1 (0483:374b)"
#          chip = "STM32WB55RGVx"
#          file = "ports/stm32/build-NUCLEO_WB55/firmware.elf"
```

**Flash manually via probe-rs:**
```bash
probe-rs run --chip STM32WB55RGVx --probe 0483:374b ports/stm32/build-NUCLEO_WB55/firmware.elf
```

**Or flash using OpenOCD:**
```bash
cd ports/stm32
make BOARD=NUCLEO_WB55 MICROPY_BLUETOOTH_ZEPHYR=1 MICROPY_BLUETOOTH_NIMBLE=0 deploy-stlink
```

### Serial Console Access

After flashing, the device appears as:
- USB Device: Pyboard Virtual Comm Port in FS Mode (f055:9802)
- Serial Port: `/dev/ttyACM*` (typically /dev/ttyACM4 or /dev/ttyACM5)
- Baud Rate: 115200

Connect via:
```bash
screen /dev/ttyACM4 115200
# or
minicom -D /dev/ttyACM4 -b 115200
# or
python3 -m serial.tools.miniterm /dev/ttyACM4 115200
```

### Current Status (2025-10-15 - FINAL)

**STM32WB55 Port Integration: ⚠️ INCOMPLETE - bt_enable() Deadlock**
- Created `ports/stm32/mpzephyrport.c` (390 lines)
- H:4 HCI packet parser for both UART and IPCC transports
- Unified transport support (works with STM32WB internal IPCC and external UART controllers)
- Firmware builds successfully: ~370KB
- Fixed memory corruption by reserving 20KB for `.noinit` section in linker script (commit 86bb183496)
- Fixed EINVAL error by removing destructive debug logging (commit 75c872a152)
- Added k_panic/k_oops implementations (commit 836c6c591d)
- Fixed scan callback registration on reactivation (commit 1cbd0a5bd7)

**Runtime Behavior: ⚠️ PARTIAL - Boots but bt_enable() Hangs**
- ✅ Firmware flashes successfully via probe-rs
- ✅ MicroPython boots and REPL works
- ✅ `bluetooth.BLE()` object creation succeeds
- ❌ **`ble.active(True)` hangs during `bt_enable()`**
- ❌ `bt_enable()` deadlocks internally before sending HCI commands
- ❌ No MAC address retrieval (can't get past initialization)
- ❌ BLE operations not functional

**Recent Commits:**
- `836c6c591d` (2025-10-15) - extmod/zephyr_ble: Add k_panic and k_oops implementations
- `1cbd0a5bd7` (2025-10-15) - extmod/zephyr_ble: Fix scan callback registration on reactivation
- `053306c2cb` - ports/stm32: Clean up Zephyr BLE integration code
- `f31a2bcd73` - extmod/zephyr_ble: Refactor k_queue to use sys_snode_t
- `75c872a152` - lib/zephyr: Update to fix bt_buf_get_type() issue
- `86bb183496` - ports/stm32: Reserve 20KB heap gap for Zephyr .noinit section

**RP2 Port Status: ❌ NOT OPERATIONAL**
- Build system works
- Runtime initialization fails
- Does not complete `bt_enable()` successfully
- No successful BLE operations achieved

**Architecture Highlights:**
- Single `mpzephyrport.c` file works for both UART (external controller) and IPCC (STM32WB)
- Uses existing STM32 transport abstraction (`mp_bluetooth_hci_uart_*` API)
- STM32WB quirks (BDADDR config, SET_EVENT_MASK2 workaround) handled transparently in `rfcore.c`
- Demonstrates successful cross-platform portability of Zephyr BLE integration

**Test Hardware:**
- RP2: Raspberry Pi Pico 2 W (RP2350) with CYW43 BT controller via SPI
- STM32: NUCLEO_WB55 (STM32WB55RGVx) with internal BT controller via IPCC

Both platforms are now being tested concurrently to validate cross-platform behavior and isolate the root cause of the initialization hang.

### Next Steps:

**Phase 3: Known Issue Investigation**
- Debug ble_mtu_peripheral.py failure (event delivery in multi-iteration MTU test)
- Investigate _IRQ_GATTS_WRITE timing/delivery
- Consider adding test variants with single iteration to isolate issue

**Phase 3: Port to Other Platforms**
- STM32 port (UART HCI variant)
- ESP32 port (if UART HCI is available)
- Other ports with appropriate HCI transports

**Phase 4: modbluetooth Integration**
- Full modbluetooth API implementation
- Event handling
- Characteristic read/write/notify
- Service discovery

**Phase 5: Testing & Documentation**
- Unit tests
- Integration tests with real BLE devices
- Performance testing
- Documentation updates
- arm gdb should be used proactively to diagnose issues. either ask the probe-rs agent or review ~/.claude/agents/probe-rs-flash.md for specific usage details.
## STM32WB Hang Diagnostic (2025-10-13)

### HCI Trace Diagnostic Session

**Diagnostic Document:** `docs/zephyr_ble_stm32wb_hang_diagnostic.md`

**Summary:** Comprehensive HCI-level diagnostic to determine where Zephyr BLE initialization hangs on STM32WB55.

### Key Findings

**Root Cause Identified:** Zephyr BLE host stack deadlocks internally **before** sending any HCI commands to the controller.

**Evidence:**
1. ✅ HCI transport fully functional (proven by NimBLE success)
2. ✅ STM32WB wireless coprocessor functional (NimBLE initializes in ~300ms)
3. ❌ Zephyr never calls `hci_stm32_send()` - no `[SEND]` traces
4. ❌ No HCI commands sent to controller - no HCI trace output
5. ❌ Hang occurs in `bt_enable()` before any HCI communication

**Likely Causes:**
- Work queue deadlock: Initialization waiting on work items never submitted
- Semaphore ordering: Code waits on semaphore signaled by un-queued work  
- Missing kernel init: Zephyr subsystem not properly initialized
- Thread context assumption: Zephyr code expects threading unavailable in cooperative scheduler

### Diagnostic Files

**Trace Captures:**
- `docs/nimble_hci_trace_success.log` (92 lines) - NimBLE working baseline
- `docs/zephyr_ble_stm32wb_hang_diagnostic.md` - Full diagnostic report

**Capture Method:**
```bash
# 1. Enable HCI tracing in rfcore.c
#    Changed printf() to mp_printf(&mp_plat_print, ...) in HCI_TRACE blocks

# 2. Add send tracing in mpzephyrport.c
#    Added [SEND] trace in hci_stm32_send() function

# 3. Build and flash
cd ports/stm32
make BOARD=NUCLEO_WB55 BOARD_VARIANT=zephyr -j8
probe-rs run --chip STM32WB55RGVx build-NUCLEO_WB55-zephyr/firmware.elf

# 4. Capture trace
mpremote connect /dev/ttyACM3 exec "import bluetooth; ble = bluetooth.BLE(); ble.active(True)"
```

### NimBLE Success (Baseline)

**HCI Command Sequence:**
1. Vendor BLE_INIT (rfcore init)
2. HCI Reset (0x03 0x0c)
3. Read Buffer Size, Local Version
4. Set Event Mask commands
5. LE Controller commands (Random Address, etc.)

**Timing:** ~300ms from boot to full initialization

**MAC Address:** Retrieved successfully `(0, b'\x02\t\x13]0\xd2')`

### Zephyr Failure (Deadlock)

**Observations:**
- ✅ Firmware boots (REPL works)
- ✅ `bluetooth.BLE()` object creation succeeds
- ❌ `ble.active(True)` hangs in `bt_enable()`
- ❌ NO `[SEND]` messages (Zephyr never calls transport)
- ❌ NO HCI trace output (no commands sent)

**Conclusion:** Internal Zephyr host deadlock, not HCI/transport issue.

### Code Modifications for Diagnostic

**rfcore.c** (lines 58-61, 390-402, 471-477, 642-648):
```c
#define HCI_TRACE (1)

// Changed all printf to mp_printf for UART output
mp_printf(&mp_plat_print, "[% 8d] <%s(%02x", mp_hal_ticks_ms(), info, buf[0]);
```

**mpzephyrport.c** (line 324):
```c
// Added send trace
mp_printf(&mp_plat_print, "[SEND] HCI type=0x%02x len=%d\n", h4_type, total_len);
```

**mpzephyrport.c** (line 58):
```c
// Enabled hard fault debug
pyb_hard_fault_debug = 1;
```

### Next Investigation Steps

1. Use GDB to break in `bt_enable()` and single-step through Zephyr initialization
2. Add debug output to Zephyr work queue (`zephyr_ble_work.c`) and semaphore (`zephyr_ble_sem.c`) functions
3. Check if Zephyr BLE requires threading context not provided by cooperative scheduler
4. Compare Zephyr's initialization requirements vs NimBLE's cooperative model
5. Review Zephyr `CONFIG_BT_*` Kconfig requirements for non-threaded environments

### Test Hardware

- Board: NUCLEO-WB55 (STM32WB55RGVx)
- Debug Probe: ST-Link V2-1 (0483:374b, built into NUCLEO board)
- Serial: /dev/ttyACM3 @ 115200 baud
- Transport: IPCC (Inter-Processor Communication Controller to wireless coprocessor)

## Memory Corruption Resolution (2025-10-15 Afternoon)

### Executive Summary

The "deadlock" was actually a **memory corruption bug** caused by GC heap overlap with Zephyr's `.noinit` section containing net_buf pools. The MicroPython GC heap was writing over Zephyr's statically allocated buffer pools, corrupting the `recv_cb` function pointer and causing HardFault.

**Resolution:** Modified STM32WB55 linker script to reserve 20KB after `.bss` for `.noinit` section before starting the heap.

### Investigation Timeline

#### 1. Initial "Deadlock" Symptoms (2025-10-15 Morning)
- Firmware appeared to hang in `k_sem_take()` waiting for HCI response
- HCI response arrived (IPCC interrupt fired) but was queued, not delivered
- Cooperative scheduler implementation working correctly
- Task scheduling functional

#### 2. Breakthrough: Buffer Delivery Attempt (2025-10-15 Midday)
Added trace to `mp_bluetooth_zephyr_hci_uart_wfi()` to verify buffer delivery:
```c
mp_printf(&mp_plat_print, "[wfi] Deliver buf=%p\n", buf);
recv_cb(hci_dev, buf);
mp_printf(&mp_plat_print, "[wfi] Done\n");
```

**Result:**
```
[wfi] Deliver buf=200048a4
HardFault
PC    0102d7a0  <- Corrupted address (should be 0x0802d7a0)
```

**Critical Discovery:** Buffer WAS being delivered, but calling `recv_cb()` caused HardFault at corrupted address.

#### 3. Memory Corruption Analysis

**Symbol Table:**
```
0802d7a0 T bt_hci_recv          <- Correct function address
200034a8 b recv_cb              <- Variable storing function pointer
```

**Runtime Value:**
```
recv_cb = 0x0102d7a0            <- Single bit flip (bit 27 cleared)
Expected: 0x0802d7a0
```

**Corruption Pattern:** Bit 27 cleared (`0x08` → `0x01`) suggests memory write to address `0x200034a8` by heap or other allocator.

**Memory Layout Investigation:**
```bash
arm-none-eabi-nm build-NUCLEO_WB55-zephyr/firmware.elf | grep -E "^200034"
```

```
20003404 b flash_tick_counter_last_write
...
20003454 b rx_queue_tail
20003458 b rx_queue_head
2000345c b rx_queue                        # Array[8], 32 bytes
2000347c b mp_zephyr_hci_sched_node
20003484 b mp_zephyr_hci_soft_timer
200034a4 b hci_dev
200034a8 b recv_cb                         # CORRUPTION HERE
200034ac B _ebss                           # END OF BSS
200034ac B _heap_start                     # HEAP STARTS HERE!
200034ac b net_buf_data_hci_cmd_pool      # Zephyr buffer pool
```

**ROOT CAUSE IDENTIFIED:** `_heap_start` == `_ebss`, but Zephyr's `.noinit` section comes AFTER `.bss` and contains buffer pools. The GC heap was starting at the same address as the buffer pools!

#### 4. .noinit Section Analysis

Zephyr uses `.noinit` sections for buffer pools that must NOT be zeroed by startup code:

```bash
arm-none-eabi-objdump -t build-NUCLEO_WB55-zephyr/firmware.elf | grep noinit | head -5
```

```
200034b0 .noinit hci_core.c     000005a0 net_buf_data_hci_cmd_pool (1440 bytes)
20003a50 .noinit hci_core.c     000001e0 _net_buf_hci_cmd_pool
20003c30 .noinit buf.c          00000470 net_buf_data_evt_pool
200040a0 .noinit buf.c          00000180 _net_buf_evt_pool
...
```

**Total .noinit size:** ~6KB of buffer pools

**Problem:** Linker script defined `_heap_start = _ebss`, which overlapped with `.noinit` at `0x200034b0`.

#### 5. Linker Script Fix

**File:** `ports/stm32/boards/stm32wb55xg.ld`

**Original (BROKEN):**
```ld
_heap_start = _ebss; /* heap starts just after statically allocated memory */
_heap_end = _sstack;
```

**Fixed:**
```ld
/* NOTE: For Zephyr BLE integration, .noinit sections are placed by the linker */
/* after .bss and must not be overlapped by the heap. We reserve 20KB for .noinit. */
_heap_start = _ebss + 20K; /* heap starts after .noinit (Zephyr net_buf pools) */
_heap_end = _sstack;
```

**Rationale:**
- `.noinit` section is ~6KB but varies with build configuration
- Reserve 20KB (generous margin) to prevent future overlap
- Reduces available heap from ~156KB to ~136KB (still plenty)

#### 6. Verification After Fix

**Memory Layout After Fix:**
```bash
arm-none-eabi-nm build-NUCLEO_WB55-zephyr/firmware.elf | grep -E "_ebss|_heap_start"
```

```
200034b0 B _ebss                           # End of BSS
200084b0 A _heap_start                     # Heap starts 20KB later
```

**Gap:** `0x200084b0 - 0x200034b0 = 0x5000 = 20KB`

**Test Output:**
```
[wfi] recv_cb=802d7a1 hci_dev=805b170 buf=200048a8 canary=deadbeef
[bt_hci_recv] ENTER, buf_type=2
[bt_hci_recv] Scheduler locked, calling bt_recv_unsafe
[bt_hci_recv] bt_recv_unsafe returned -22
[bt_hci_recv] EXIT, err=-22
[wfi] Done
```

**Success Indicators:**
- ✅ `recv_cb=802d7a1` - Correct value (Thumb bit set)
- ✅ `canary=deadbeef` - Memory protection working
- ✅ `[wfi] Done` - Function call completed without HardFault
- ✅ Buffer delivered to Zephyr stack successfully

**New Issue:** `bt_recv_unsafe()` returns `-22` (EINVAL), but this is a Zephyr BLE stack issue, not memory corruption.

### Technical Details

**Why The Overlap Happened:**

1. **Standard MicroPython ports:** Use `_heap_start = _ebss` because:
   - `.bss` is the last section in RAM
   - No additional sections after `.bss`
   - Safe to start heap immediately after

2. **Zephyr BLE integration:** Adds `.noinit` sections:
   - Zephyr marks buffer pools with `__attribute__((section(".noinit")))`
   - Linker places `.noinit` AFTER `.bss` but BEFORE heap
   - Original linker script didn't account for this

3. **GC Heap Behavior:**
   - MicroPython's `gc_init(MICROPY_HEAP_START, MICROPY_HEAP_END)` in `main.c:568`
   - `MICROPY_HEAP_START` is `&_heap_start` from linker script
   - GC immediately begins allocating from `_heap_start`
   - First allocations overwrote `.noinit` buffer pools

**Why recv_cb Was Corrupted:**

- `recv_cb` at `0x200034a8` is 4 bytes before `_ebss` at `0x200034ac`
- Heap starting at `0x200034ac` likely had internal structures
- Heap metadata or early allocations wrote near heap start
- Single bit flip pattern suggests partial overwrite

**Canary Protection:**

Added canary variable to detect corruption:
```c
static volatile uint32_t canary_after_recv_cb = 0xDEADBEEF;
```

After fix, canary remained intact, confirming heap no longer overlaps.

### Files Modified

**Linker Script:**
- `ports/stm32/boards/stm32wb55xg.ld` - Changed `_heap_start` definition

**Port Integration (diagnostic code, can be removed):**
- `ports/stm32/mpzephyrport.c` - Added canary and corruption checks

### Lessons Learned

1. **Linker script assumptions are dangerous:**
   - Standard pattern `_heap_start = _ebss` doesn't work when adding `.noinit`
   - Always verify section order with `arm-none-eabi-nm` or `objdump`

2. **Single-bit corruption is a red flag:**
   - Not random noise or electrical issue
   - Indicates partial overwrite by allocator
   - Suggests nearby memory region conflict

3. **"Deadlock" symptoms can mask memory corruption:**
   - System appeared to hang in semaphore wait
   - Actually crashed with HardFault when calling corrupted pointer
   - Watchdog reset or hang looked like deadlock

4. **Memory protection techniques:**
   - Canary values before/after critical data
   - Address range validation before calling function pointers
   - Section alignment and gaps in linker script

## EINVAL Error Resolution (2025-10-15 Late Afternoon)

### Root Cause: Destructive Logging

After resolving the memory corruption, `bt_recv_unsafe()` was returning `-22` (EINVAL). Investigation revealed the issue was in **one line of debugging code** at `lib/zephyr/subsys/bluetooth/host/hci_core.c:4570`:

```c
uint8_t buf_type = bt_buf_get_type(buf);  // ❌ DESTRUCTIVE!
mp_printf(&mp_plat_print, "[bt_hci_recv] ENTER, buf_type=%u\n", buf_type);
```

### The Problem

**`bt_buf_get_type()` is deprecated and DESTRUCTIVE:**

From `zephyr/include/zephyr/bluetooth/buf.h:201-208`:
```c
static inline enum bt_buf_type __deprecated bt_buf_get_type(struct net_buf *buf)
{
    return bt_buf_type_from_h4(net_buf_pull_u8(buf), BT_BUF_OUT);
}
```

**Key issue:** `net_buf_pull_u8()` **REMOVES** the first byte from the buffer. This byte is the H:4 packet type:
- `0x04` = `BT_HCI_H4_EVT` (HCI event packet)
- `0x02` = `BT_HCI_H4_ACL` (ACL data packet)

**Failure sequence:**
1. Buffer created with H:4 type byte at `buf->data[0]` = `0x04` (EVT)
2. Debug logging calls `bt_buf_get_type(buf)` which pulls the byte off
3. Now `buf->data[0]` contains the event code instead of H:4 type
4. `bt_recv_unsafe()` reads `buf->data[0]` to determine packet type (line 4488)
5. Sees event code (e.g., `0x0E` for CMD_COMPLETE) instead of `0x04`
6. Falls through to `default` case at line 4554-4557
7. Returns `-EINVAL` (-22)

### The Fix

Remove the destructive logging call:

```c
int bt_hci_recv(const struct device *dev, struct net_buf *buf)
{
    // NOTE: Do NOT call bt_buf_get_type() here! It's a destructive operation
    // that removes the H:4 type byte from the buffer via net_buf_pull_u8().
    // bt_recv_unsafe() needs that byte to determine the packet type.
    mp_printf(&mp_plat_print, "[bt_hci_recv] ENTER\n");

    k_sched_lock();
    err = bt_recv_unsafe(buf);
    k_sched_unlock();
    ...
}
```

### Verification

**After fix:**
```bash
mpremote connect /dev/ttyACM2 exec "import bluetooth; ble = bluetooth.BLE(); ble.active(True); print('SUCCESS: BLE is active!'); print('Config:', ble.config('mac'))"
```

**Output:**
```
BLE: mp_bluetooth_init
BLE: mp_bluetooth_deinit 2
BLE: BLE already initialized (state=2)
BLE: mp_bluetooth_init: ready
SUCCESS: BLE is active!
Config: (0, b'\x02\t\x13]0\xd2')
```

**Results:**
- ✅ `bt_enable()` completed successfully
- ✅ MAC address retrieved: `02:09:13:5d:30:d2`
- ✅ No hangs, no crashes, no EINVAL errors
- ✅ Full HCI command/response cycle working
- ✅ Zephyr BLE stack fully operational on STM32WB55

### Technical Details

**H:4 Packet Structure:**
```
[type:1] [header:N] [payload:M]
```

For HCI events:
```
[0x04] [evt_code:1] [length:1] [params:length]
```

**Buffer allocation:**
- `bt_buf_get_evt()` automatically adds the H:4 type byte (0x04) via `net_buf_add_u8()`
- Port integration parser adds header and payload via `net_buf_add_mem()`
- Buffer must preserve H:4 type byte for `bt_recv_unsafe()` to parse

**Why `bt_buf_get_type()` is deprecated:**
- Zephyr moved away from H:4 type byte in buffer data
- Modern Zephyr uses buffer pool metadata to track type
- Function kept for backwards compatibility but marked deprecated
- **Destructive nature makes it unsafe for inspection/logging**

### Lessons Learned

1. **Deprecated APIs have good reasons:**
   - `bt_buf_get_type()` deprecated because it's destructive
   - Should have checked API documentation before using

2. **Logging can introduce bugs:**
   - Seemingly harmless debug logging modified buffer state
   - Always verify logging functions are read-only

3. **Buffer ownership and immutability:**
   - Once buffer created, don't modify structure until consumed
   - Inspection should use non-destructive methods
   - Or use buffer pool metadata instead of data bytes

4. **Test without debug output:**
   - Debug code can mask or introduce bugs
   - Verify behavior with minimal/no debug output

### Files Modified

**Zephyr BLE Host Stack:**
- `lib/zephyr/subsys/bluetooth/host/hci_core.c:4561-4580` - Removed destructive `bt_buf_get_type()` call

### Status

**STM32WB55 Zephyr BLE Integration: ✅ FULLY OPERATIONAL**

All major components working:
- ✅ Memory layout (linker script fix)
- ✅ HCI transport (IPCC)
- ✅ Buffer delivery (H:4 parser)
- ✅ Zephyr BLE stack initialization (`bt_enable()`)
- ✅ MAC address retrieval
- ✅ Ready for GAP/GATT operations

**Next: Test BLE scanning and peripheral operations to match RP2 validation**

## BLE Initialization Call Diagram (2025-10-16)

This diagram maps the function call flow during Zephyr BLE stack initialization, showing what calls where, when, and in what context.

### Startup Flow (From Python Script)

```
Python: ble.active(True)
  │
  ├─> modbluetooth.c: mp_bluetooth_ble_active()
  │     └─> modbluetooth_zephyr.c: mp_bluetooth_init()
  │
  └─> modbluetooth_zephyr.c: mp_bluetooth_init()
        │
        ├─> [Allocate state structures]
        │
        ├─> modbluetooth_zephyr.c: mp_bluetooth_deinit()
        │     └─> [Clean up previous state]
        │
        ├─> ports/stm32/mpzephyrport.c: mp_bluetooth_zephyr_port_init()
        │     └─> soft_timer_static_init(&mp_zephyr_hci_soft_timer, ...)
        │
        ├─> zephyr/bluetooth/bluetooth.h: bt_conn_cb_register()
        │     └─> [Register connection callbacks]
        │
        ├─> ports/stm32/mphalport.h: mp_bluetooth_hci_controller_init()
        │     └─> [No-op for STM32WB, init done by rfcore]
        │
        └─> zephyr/bluetooth/bluetooth.h: bt_enable(mp_bluetooth_zephyr_bt_ready_cb)
              │
              ├─> [lib/zephyr/subsys/bluetooth/host/hci_core.c]
              │
              └─> k_work_submit(&bt_dev.init)
                    │
                    └─> [Submits init_work to work queue]
                          │
                          └─> ⚠️ PROBLEM: Work never processed!
```

### Work Processing Flow (How Init Work SHOULD Be Processed)

```
REPL Loop / mp_bluetooth_hci_poll()
  │
  └─> ports/stm32/mpzephyrport.c: mp_bluetooth_hci_poll()
        │
        └─> extmod/zephyr_ble/hal/zephyr_ble_poll.c: mp_bluetooth_zephyr_poll()
              │
              ├─> extmod/zephyr_ble/hal/zephyr_ble_timer.c: mp_bluetooth_zephyr_timer_process()
              │     └─> [Process expired timers]
              │
              ├─> extmod/zephyr_ble/hal/zephyr_ble_work.c: mp_bluetooth_zephyr_work_process()
              │     │
              │     ├─> [Recursion guard check: if (work_processor_running) return;]
              │     │
              │     ├─> work_processor_running = true
              │     │
              │     └─> for each work queue:
              │           └─> for each work item in queue:
              │                 └─> work->handler(work)  ← Execute init_work handler
              │                       │
              │                       └─> bt_init_work()  [Zephyr BLE initialization]
              │                             │
              │                             ├─> hci_driver_open()
              │                             │     └─> ports/stm32/mpzephyrport.c: hci_stm32_open()
              │                             │           │
              │                             │           ├─> bt_hci_transport_setup()
              │                             │           │     └─> ports/stm32/rfcore.c: rfcore_ble_init()
              │                             │           │           └─> Send BLE_INIT command to WB coprocessor
              │                             │           │
              │                             │           ├─> mp_zephyr_hci_poll_now()
              │                             │           │     └─> mp_sched_schedule_node(&mp_zephyr_hci_sched_node, run_zephyr_hci_task)
              │                             │           │
              │                             │           └─> mp_handle_pending(false)
              │                             │                 └─> ⚠️ Executes run_zephyr_hci_task
              │                             │
              │                             └─> hci_init()  ← Sends HCI commands, waits for responses
              │                                   │
              │                                   └─> bt_hci_cmd_send_sync()
              │                                         │
              │                                         ├─> hci_core_send_cmd()
              │                                         │     └─> ports/stm32/mpzephyrport.c: hci_stm32_send()
              │                                         │           └─> mp_bluetooth_hci_uart_write()
              │                                         │                 └─> ports/stm32/rfcore.c: Send HCI command via IPCC
              │                                         │
              │                                         └─> k_sem_take(&bt_dev.ncmd_sem, K_FOREVER)
              │                                               │
              │                                               └─> ⚠️ PROBLEM: Waits for HCI response
              │                                                     │
              │                                                     └─> See "HCI Response Processing" below
              │
              └─> ports/stm32/mpzephyrport.c: mp_bluetooth_zephyr_hci_uart_process()
                    └─> [No-op, weak function]
```

### HCI Response Processing Flow (Interrupt → Delivery)

```
[STM32WB wireless coprocessor sends HCI response]
  │
  └─> IPCC Interrupt: IPCC_C1_RX_IRQHandler()  [ports/stm32/rfcore.c]
        │
        ├─> [Disable IPCC RX channel]
        │
        ├─> mp_bluetooth_hci_poll_now()
        │     └─> mp_sched_schedule_node(&mp_zephyr_hci_sched_node, run_zephyr_hci_task)
        │           │
        │           └─> ⚠️ Task scheduled but NOT executed yet!
        │
        └─> [Return from interrupt]


Later... k_sem_take() Wait Loop:
  │
  └─> extmod/zephyr_ble/hal/zephyr_ble_sem.c: k_sem_take()
        │
        └─> while (sem->count == 0):
              │
              ├─> ports/stm32/mpzephyrport.c: mp_bluetooth_zephyr_hci_uart_wfi()
              │     │
              │     ├─> ⚠️ NOTE: Do NOT call mp_bluetooth_zephyr_work_process()!
              │     │   (Recursion guard would skip it anyway)
              │     │
              │     ├─> mp_event_wait_ms(1)  ← Should run scheduled tasks
              │     │     │
              │     │     └─> ✅ Executes run_zephyr_hci_task()
              │     │           │
              │     │           └─> ports/stm32/mpzephyrport.c: run_zephyr_hci_task()
              │     │                 │
              │     │                 ├─> mp_bluetooth_zephyr_poll()
              │     │                 │     └─> [Process timers + work queues]
              │     │                 │
              │     │                 ├─> mp_bluetooth_hci_uart_readpacket(h4_uart_byte_callback)
              │     │                 │     │
              │     │                 │     └─> for each byte in HCI packet:
              │     │                 │           └─> h4_uart_byte_callback(byte)
              │     │                 │                 │
              │     │                 │                 └─> h4_parser_process_byte(byte)
              │     │                 │                       │
              │     │                 │                       └─> if packet complete:
              │     │                 │                             └─> rx_queue_put(buf)
              │     │                 │                                   └─> mp_zephyr_hci_poll_now()
              │     │                 │
              │     │                 └─> while ((buf = rx_queue_get()) != NULL):
              │     │                       └─> recv_cb(hci_dev, buf)
              │     │                             │
              │     │                             └─> lib/zephyr/.../hci_core.c: bt_hci_recv()
              │     │                                   │
              │     │                                   ├─> bt_recv_unsafe(buf)
              │     │                                   │     │
              │     │                                   │     └─> [Process HCI event]
              │     │                                   │           └─> k_sem_give(&bt_dev.ncmd_sem)
              │     │                                   │                 │
              │     │                                   │                 └─> ✅ Signal semaphore!
              │     │                                   │
              │     │                                   └─> [Return to wfi()]
              │     │
              │     ├─> mp_bluetooth_hci_uart_readpacket() again
              │     │
              │     └─> Deliver any remaining RX buffers
              │
              ├─> mp_event_wait_ms() / mp_event_wait_indefinite()
              │     └─> [Yield CPU, run scheduler]
              │
              └─> [Check sem->count again, exit if signaled]
```

### Callback Flow (BLE Init Complete)

```
bt_init_work() completes successfully
  │
  └─> modbluetooth_zephyr.c: mp_bluetooth_zephyr_bt_ready_cb(err)
        │
        ├─> mp_bluetooth_zephyr_bt_enable_result = err
        │
        └─> if (err == 0):
              └─> mp_bluetooth_zephyr_ble_state = MP_BLUETOOTH_ZEPHYR_BLE_STATE_ACTIVE
```

### Key Timing Issues

#### Issue 1: Init Work Not Processed Immediately

**Problem:**
```
bt_enable() returns
  └─> init_work submitted to queue
        └─> mp_bluetooth_init() returns to Python
              └─> Python continues execution
                    └─> ⚠️ Work never processed!
```

**Why:** `mp_bluetooth_hci_poll()` is only called:
1. By soft timer (after 128ms delay)
2. By explicit BLE operations
3. NOT automatically after bt_enable()

**Solution Attempted:** Call `mp_bluetooth_zephyr_poll()` directly from `mp_bluetooth_init()`
**Result:** ❌ Firmware crashes (fatal error) - Python runtime not fully initialized

**Current Approach:** Let work sit in queue, wait for REPL to call `mp_bluetooth_hci_poll()`

#### Issue 2: Work Queue Recursion

**Problem:**
```
mp_bluetooth_zephyr_work_process()  ← Running
  └─> work->handler(work)  ← Executing init_work
        └─> bt_init()
              └─> hci_init()
                    └─> bt_hci_cmd_send_sync()
                          └─> k_sem_take()
                                └─> mp_bluetooth_zephyr_hci_uart_wfi()
                                      └─> mp_bluetooth_zephyr_work_process()  ← RECURSION!
```

**Solution:** Added recursion guard:
```c
static volatile bool work_processor_running = false;

void mp_bluetooth_zephyr_work_process(void) {
    if (work_processor_running) {
        return;  // Skip, already running
    }
    work_processor_running = true;
    // ... process work ...
    work_processor_running = false;
}
```

### File Locations

**MicroPython BLE Module:**
- `extmod/modbluetooth.c` - Generic BLE API
- `extmod/zephyr_ble/modbluetooth_zephyr.c` - Zephyr BLE implementation

**Port Integration:**
- `ports/stm32/mpzephyrport.c` - STM32 Zephyr BLE port (HCI driver)
- `ports/stm32/rfcore.c` - STM32WB wireless coprocessor interface

**Zephyr BLE HAL:**
- `extmod/zephyr_ble/hal/zephyr_ble_poll.c` - Main polling function
- `extmod/zephyr_ble/hal/zephyr_ble_work.c` - Work queue implementation
- `extmod/zephyr_ble/hal/zephyr_ble_sem.c` - Semaphore implementation
- `extmod/zephyr_ble/hal/zephyr_ble_timer.c` - Timer implementation

**Zephyr BLE Host:**
- `lib/zephyr/subsys/bluetooth/host/hci_core.c` - Main BLE initialization
- `lib/zephyr/subsys/bluetooth/host/hci_driver.h` - HCI driver interface

### Current Status (2025-10-16)

**Working:**
- ✅ Work queue processing (with recursion guard)
- ✅ Semaphore wait loops (with passive yielding)
- ✅ HCI command send/receive cycle
- ✅ IPCC interrupt handling
- ✅ Buffer allocation and delivery

**Not Working:**
- ❌ Init work not processed automatically after `bt_enable()`
- ❌ Calling `mp_bluetooth_zephyr_poll()` from `mp_bluetooth_init()` causes deadlock
- ❌ BLE init must wait for external polling trigger (soft timer or user call)

**Root Cause Confirmed (2025-10-16 Investigation):**

Calling `mp_bluetooth_zephyr_poll()` directly from `mp_bluetooth_init()` causes deadlock because:

1. `poll()` executes `bt_init()` work **synchronously**
2. `bt_init()` → `hci_init()` → sends HCI commands → waits in `k_sem_take()`
3. HCI responses arrive via IPCC interrupt → schedule `run_zephyr_hci_task()` to process them
4. BUT we're already inside `mp_bluetooth_zephyr_work_process()` executing `bt_init()` work
5. Recursion guard prevents nested work processing
6. Result: **deadlock waiting for HCI responses that can't be delivered**

**Trace Evidence:**
```
[DIAGNOSTIC] Calling mp_bluetooth_zephyr_poll() to process init work...
work_execute(20000198, handler=802e3e9)
[bt_init] ENTER
[bt_init] Calling hci_init()...
TIMEOUT OR ERROR  <- Hangs here indefinitely
```

**Code Documentation:**
Added detailed comment in `modbluetooth_zephyr.c` lines 374-379 explaining why direct poll() call is forbidden.

**Solution:**
Keep async initialization approach where work is processed by soft timer or scheduler callbacks, NOT called directly from `mp_bluetooth_init()`. This is the same pattern used successfully by NimBLE and BTstack.
