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
- `ports/rp2/main.c` - Moved pendsv_init() after scheduler starts (from previous session)
