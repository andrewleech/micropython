# FreeRTOS Boot Investigation - RP2 Port

## Investigation Goal

Enable FreeRTOS-based threading on the RP2 (Raspberry Pi Pico W) port, matching the architecture used successfully on STM32.

## Current Status

- **Non-threaded builds**: Working correctly
- **FreeRTOS threaded builds**: Boot failure with memory corruption

## Hardware Setup

- Target: Raspberry Pi Pico W (RP2040 dual-core Cortex-M0+)
- Debug probe: Picoprobe (CMSIS-DAP) serial 0501083219160908
- Debugging: pyocd + arm-none-eabi-gdb

## Issues Found and Fixed

### 1. PICO_CORE1_STACK_SIZE=0 (Fixed)

**Symptom**: `panic("multicore_launch_core1() can't be used when PICO_CORE1_STACK_SIZE == 0")`

**Root cause**: CMakeLists.txt unconditionally set `PICO_CORE1_STACK_SIZE=0` to save RAM, but FreeRTOS SMP requires a non-zero stack for core 1 startup.

**Fix**: Made conditional in CMakeLists.txt:
- Threaded: `PICO_CORE1_STACK_SIZE=0x800`
- Non-threaded: `PICO_CORE1_STACK_SIZE=0`

### 2. Missing Pico SDK Interop Settings (Fixed)

**Symptom**: Potential conflicts between Pico SDK primitives and FreeRTOS.

**Fix**: Added to FreeRTOSConfig.h:
```c
#define configSUPPORT_PICO_SYNC_INTEROP (1)
#define configSUPPORT_PICO_TIME_INTEROP (1)
```

### 3. MICROPY_PY_THREAD Not Propagated (Fixed)

**Symptom**: Non-threaded builds still included FreeRTOS code.

**Root cause**: CMakeLists.txt only defined `MICROPY_PY_THREAD=1` when enabled, but didn't define `MICROPY_PY_THREAD=0` when disabled. The mpconfigport.h default of 1 took precedence.

**Fix**: Added explicit `MICROPY_PY_THREAD=0` in CMakeLists.txt else block.

## Outstanding Issue: Memory Corruption

### Symptom

After scheduler starts and `rp2_main_loop()` begins execution, a HardFault occurs during `machine_pin_init()` with corrupted function pointer:

```
gpio_add_raw_irq_handler_with_order_priority_masked(
    gpio_mask=0,
    handler=0x454c4449,  // ASCII "IDLE" - FreeRTOS task name
    order_priority=0
)
```

### Observations

1. `rp2_main_loop()` is reached (breakpoint hits)
2. `pendsv_init()` completes
3. `gc_init()` completes
4. `mp_thread_init()` completes
5. `mp_init()` completes
6. Crash occurs in `machine_pin_init()` → `irq_add_shared_handler()`

The `gpio_irq` function pointer (should be ~0x10xxxxxx) is replaced with "IDLE" (0x454c4449), suggesting FreeRTOS task name data is being read from where a function pointer should be.

### Tested Configurations

| Configuration | Result |
|--------------|--------|
| Non-threaded (no FreeRTOS) | ✅ Works |
| Single-core FreeRTOS (configNUMBER_OF_CORES=1) | ❌ Same crash |
| Dual-core FreeRTOS SMP (configNUMBER_OF_CORES=2) | ❌ Same crash |

## References Used

### Official FreeRTOS RP2040 Resources

- FreeRTOS-SMP-Demos: https://github.com/FreeRTOS/FreeRTOS-SMP-Demos/tree/main/FreeRTOS/Demo/CORTEX_M0+_RP2040
- RP2040 port README: `lib/FreeRTOS-Kernel/portable/ThirdParty/GCC/RP2040/README.md`
- RP2040 config: `lib/FreeRTOS-Kernel/portable/ThirdParty/GCC/RP2040/include/rp2040_config.h`

### Key Configuration Files

- `ports/rp2/FreeRTOSConfig.h` - FreeRTOS configuration
- `ports/rp2/CMakeLists.txt` - Build configuration
- `ports/rp2/main.c` - Entry point and scheduler startup
- `ports/rp2/pendsv.c` - Service task implementation
- `extmod/freertos/mpthreadport.c` - Threading backend

### Handler Naming Chain

The RP2040 FreeRTOS port uses preprocessor aliasing for exception handlers:
```
xPortPendSVHandler → isr_pendsv → PendSV_Handler
```
This is handled by:
- `portmacro.h`: `#define xPortPendSVHandler isr_pendsv`
- `rename_exceptions.h`: `#define isr_pendsv PendSV_Handler`

## Learnings

1. **configUSE_DYNAMIC_EXCEPTION_HANDLERS**: Defaults to 1 in the RP2040 port. The official demos don't override this. Our attempt to comment it out had no effect (still uses default).

2. **RP2040 has only 4 hardware breakpoints**: Cortex-M0+ limitation affects GDB debugging.

3. **SMP config options**: `configUSE_CORE_AFFINITY` and `configRUN_MULTIPLE_PRIORITIES` must only be defined when `configNUMBER_OF_CORES > 1`.

4. **Memory layout matters**: The "IDLE" string corruption suggests FreeRTOS TCB/stack memory is overlapping with or being read instead of MicroPython function pointers.

## Next Steps

1. **Investigate memory layout**: Check if static allocations (main_task_stack, service_task_stack, idle_task_stack) overlap with MicroPython structures.

2. **Compare with working STM32 port**: The STM32 FreeRTOS integration works. Compare initialization order and memory layout.

3. **Check MP_STATE_PORT access**: Verify `mp_state_ctx.vm` is correctly initialized before `machine_pin_init()` accesses it.

4. **Try minimal FreeRTOS test**: Create a stripped-down test that just starts the scheduler and runs a simple task without full MicroPython initialization.

5. **Examine TCB placement**: Use GDB to examine where FreeRTOS places the IDLE task TCB and compare to addresses being corrupted.

## Files Modified

- `ports/rp2/FreeRTOSConfig.h` - Added Pico interop settings, conditional SMP config
- `ports/rp2/CMakeLists.txt` - Conditional PICO_CORE1_STACK_SIZE, explicit MICROPY_PY_THREAD=0
- `ports/rp2/main.c` - Moved pendsv_init() after scheduler starts, fixed TLS init order, RTC init sequence

## Hardware Issue - Pico W Board (Dec 2025)

### Issue Summary

After fixing the software initialization issues, USB enumeration still failed. Investigation revealed a **hardware fault** on the specific Pico W being used.

### Fixed Software Issues

1. **TLS Init Order**: `mp_thread_init()` must be called before `mp_cstack_init_with_top()`
2. **lwIP Init**: Must wait for WiFi chip before initializing lwIP with `cyw43_arch_init()`
3. **RTC Init**: `aon_timer_start()` was hanging because clk_rtc wasn't configured

### Hardware Fault Evidence

GDB debugging revealed:

| Register | Value | Expected | Issue |
|----------|-------|----------|-------|
| XOSC_CTRL | 0x255 | 0xfabaa0 | Writes ignored/corrupted |
| XOSC_STATUS | 0x55 | 0x8xxx1xxx | STABLE bit never set |
| XOSC_STARTUP | 0x00 | 0xc4 | Write completely ignored |
| CLK_REF_SEL | 0x00 | 0x01 | Mux never switches |
| CLK_SYS_SEL | 0x00 | 0x01 | Mux never switches |
| Flash ID | 0xffffff | valid ID | Flash not responding |

### Test Results

1. **SRAM R/W**: ✅ Works (wrote 0xdeadbeef, read back correctly)
2. **RAM Execution**: ✅ Works (loaded and executed code from RAM)
3. **ROSC**: ✅ Running (STATUS shows STABLE=1)
4. **XOSC**: ❌ Not starting, register writes ignored
5. **Clock Muxes**: ❌ Not switching (SEL registers stay at 0)
6. **Flash Access**: ❌ Returns 0xffffff
7. **OpenOCD Flash**: ❌ "Unknown flash device (ID 0x00ffffff)"

### Conclusion

The Pico W board appeared to have a transient hardware fault affecting:
- XOSC crystal oscillator circuit
- Clock distribution network
- Flash QSPI interface

### Resolution

**Power cycling the board resolved the hardware issue.** After power cycle:

1. Flash programming succeeded: `Found flash device 'win w25q16jv' (ID 0x001540ef)`
2. USB enumeration works: `MicroPython Board in FS mode`
3. FreeRTOS threading works: Multiple threads executed successfully
4. WiFi interface initializes: CYW43 driver functional

The transient hardware fault (stuck XOSC, non-responsive clock muxes) was cleared by a full power cycle. The FreeRTOS-based MicroPython build is now fully functional on the Pico W.

## Thread Test Results

### Standard Thread Tests

All standard thread unit tests pass on the RP2 FreeRTOS build:

```
29 tests performed (124 individual testcases)
29 tests passed
4 tests skipped
```

### Previously Skipped Tests Now Pass

The `run-tests.py` skips 4 thread tests for `rp2` platform with the comment "require more than 2 threads". This skip was for the **old multicore-based threading** which was limited to 2 threads (one per CPU core).

With FreeRTOS, all 4 tests now pass:

| Test | Threads | Status | Output |
|------|---------|--------|--------|
| thread_shared2.py | 2 | ✅ Pass | `[10, 20]` |
| thread_lock2.py | 4 | ✅ Pass | 4x "have it" + "done" |
| thread_lock3.py | 10 | ✅ Pass | "my turn: 0" through 9 |
| stress_heap.py | 10 | ✅ Pass | 10x `49995000 10000` |

### Implication

The skip list in `tests/run-tests.py` could be updated to differentiate between:
- Old multicore backend (limited to 2 threads)
- FreeRTOS backend (supports many threads)
