# Zephyr Kernel ARM Cortex-M Architecture Layer

This directory contains the ARM Cortex-M architecture bridge for Zephyr RTOS integration with MicroPython.

## Overview

The Cortex-M architecture layer provides the low-level platform support needed to run the Zephyr kernel on bare-metal ARM Cortex-M systems. This code is shared across all Cortex-M based MicroPython ports (QEMU, STM32, nRF52, RP2, etc.).

## Files

- **cortex_m_arch.c** - Architecture-specific Zephyr kernel support
  - FPU initialization (CPACR, FPCCR configuration)
  - SysTick timer configuration and interrupt handler
  - PendSV interrupt handler for context switching
  - Zephyr kernel stubs (clock, console, fatal error)
  - Minimal stdio stubs for bare-metal builds

- **cortex_m_arch.h** - Public interface header
  - Functions for initializing arch layer
  - Functions for enabling interrupts
  - Functions for yielding to scheduler

- **README.md** - This file

## Port Requirements

### Configuration Defines

Ports must define the following in `mpconfigboard.h`:

```c
// CPU frequency in Hz - required for SysTick calculation
#define MICROPY_HW_CPU_FREQ_HZ      (64000000)  // Example: 64MHz

// Optional: System tick frequency (defaults to 1000Hz = 1ms ticks)
// #define CONFIG_SYS_CLOCK_TICKS_PER_SEC  1000
```

### Initialization Sequence

The port must follow this initialization sequence:

1. **Early init** (in port's startup or main):
   ```c
   mp_zephyr_arch_init();  // Configure FPU, SysTick, PendSV (interrupt disabled)
   ```

2. **Kernel init** (port calls z_cstart):
   ```c
   extern void z_cstart(void);
   z_cstart();  // Initialize Zephyr kernel, never returns
   ```

3. **Main thread entry** (in micropython_main_thread_entry):
   ```c
   void micropython_main_thread_entry(void *p1, void *p2, void *p3) {
       // Enable SysTick interrupt now that kernel is ready
       mp_zephyr_arch_enable_systick_interrupt();

       // Initialize MicroPython threading
       char stack_dummy;
       mp_thread_init(&stack_dummy);

       // Continue with main loop...
   }
   ```

### Interrupt Handlers

The architecture layer provides these interrupt handlers:

- **SysTick_Handler**: Increments tick counter, calls `sys_clock_announce()`, triggers PendSV when rescheduling needed
- **PendSV_Handler**: Naked function that jumps to Zephyr's `z_arm_pendsv` for context switching

If the port has existing SysTick or PendSV handlers, they must be conditionally excluded when `MICROPY_ZEPHYR_THREADING=1`.

## FPU Support

The architecture layer automatically configures the FPU when available:

- Enables CP10/CP11 access (full access for privileged and unprivileged modes)
- Configures FPCCR for lazy FPU context save (ASPEN + LSPEN bits)
- Initializes FPSCR to zero

This requires `CONFIG_FPU=1` and `CONFIG_FPU_SHARING=1` in Zephyr configuration.

## SysTick Configuration

SysTick is configured for `CONFIG_SYS_CLOCK_TICKS_PER_SEC` (default 1000Hz = 1ms ticks):

- Reload value calculated from `MICROPY_HW_CPU_FREQ_HZ`
- Uses processor clock (CLKSOURCE = 1)
- PendSV priority set to lowest (0xFF)

Formula: `RELOAD = (CPU_FREQ / TICK_FREQ) - 1`

## Zephyr Configuration

The architecture layer requires these Zephyr kernel options (provided by `zephyr_config_cortex_m.h`):

```c
#define CONFIG_MULTITHREADING 1
#define CONFIG_FPU 1
#define CONFIG_FPU_SHARING 1
#define CONFIG_SYS_CLOCK_EXISTS 1
#define CONFIG_SYS_CLOCK_TICKS_PER_SEC 1000
#define CONFIG_TIMESLICING 1
#define CONFIG_TIMESLICE_SIZE 10  // 10ms timeslice
```

### Preemptive Multitasking Requirement

**CRITICAL**: `CONFIG_TIMESLICING` and `CONFIG_TIMESLICE_SIZE` MUST remain enabled. These provide preemptive multitasking, which is a fundamental requirement for Zephyr threading.

Without time slicing, threads only switch at explicit yield points (like `time.sleep_ms()` or lock acquisition). This defeats the primary benefit of threading - threads cannot be preempted mid-execution based on priority and time slices. This makes threading effectively useless for real applications.

The Zephyr scheduler infrastructure provides proper spinlock-based synchronization to protect scheduler data structures (rb-trees) from interrupt reentrancy. All scheduler files (sched.c, timeslicing.c, timeout.c, rb.c) are integrated and properly locked.

See `extmod/zephyr_kernel/SCHEDULER_ANALYSIS.md` for detailed analysis of preemptive multitasking requirements.

## Compatibility

This architecture layer is compatible with:

- **Cortex-M3**: ARMv7-M without FPU
- **Cortex-M4F**: ARMv7E-M with FPU
- **Cortex-M7F**: ARMv7E-M with FPU and cache
- **Cortex-M33**: ARMv8-M with optional FPU and TrustZone

Tested on:
- QEMU mps2-an385 (Cortex-M3), mps2-an386 (Cortex-M4F)
- STM32WB55 (Cortex-M4F @ 64MHz)
- STM32F767 (Cortex-M7F @ 216MHz)

## Integration Example

See `ports/qemu/` and `ports/stm32/` for integration examples.

### QEMU Port Integration

```makefile
# In ports/qemu/Makefile
ifeq ($(MICROPY_ZEPHYR_THREADING),1)
ZEPHYR_ARCH := arm
include $(TOP)/extmod/zephyr_kernel/zephyr_kernel.mk
CFLAGS += $(ZEPHYR_INC) $(ZEPHYR_CFLAGS)
endif
```

### STM32 Port Integration

```makefile
# In ports/stm32/Makefile
ifeq ($(MICROPY_ZEPHYR_THREADING),1)
ZEPHYR_ARCH := arm
include $(TOP)/extmod/zephyr_kernel/zephyr_kernel.mk
CFLAGS += $(ZEPHYR_INC) $(ZEPHYR_CFLAGS)
SRC_C += zephyr_stm32.c
endif
```

## Debugging

Enable debug output by uncommenting `DEBUG_printf` calls in `cortex_m_arch.c`.

Check initialization:
```c
mp_printf(&mp_plat_print, "Zephyr arch (Cortex-M): Initialized\n");
```

Use GDB to inspect:
```gdb
(gdb) break SysTick_Handler
(gdb) break PendSV_Handler
(gdb) print _kernel.cpus[0].current
(gdb) print _kernel.ready_q.cache
```

## Known Issues

1. **QEMU PTY Latency**: Avoid using `k_sleep()` in stdin polling loops - use `k_yield()` in `MICROPY_EVENT_POLL_HOOK` instead
2. **HAL Tick Compatibility**: If port uses STM32 HAL, ensure `uwTick` is still incremented for HAL functions
3. **BLE Coprocessor**: On multi-core MCUs (e.g., STM32WB), verify BLE coprocessor timing is not affected by thread switching

## License

MIT License - See file header for full license text.
