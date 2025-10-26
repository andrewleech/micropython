# STM32 Zephyr Threading Migration Plan

## Executive Summary

This document outlines the migration strategy for replacing STM32 port's legacy threading implementation with Zephyr RTOS kernel-based threading. The migration leverages work already completed for the QEMU port and establishes a unified threading architecture that can be shared across all Cortex-M based MicroPython ports.

### Key Objectives
- Replace custom `pybthread` implementation with Zephyr kernel threading
- Maintain compatibility with existing STM32 boards through board variant approach
- Share common Cortex-M architecture code across ports
- Preserve REPL responsiveness and threading performance

### Architectural Decisions (Approved)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Code Organization | Move `zephyr_arch_cortex_m.c` to `extmod/zephyr_kernel/arch/` | Enable reuse across all Cortex-M ports (STM32, nRF52, RP2, etc.) |
| REPL/stdin Handling | MICROPY_EVENT_POLL_HOOK with `k_yield()` | Preserves STM32's working pattern, avoids QEMU's k_sleep timeout issues |
| Coexistence Strategy | Board variant (`BOARD_VARIANT=ZEPHYR`) | Non-breaking change, allows testing/migration per board |
| FPU Context | Zephyr `CONFIG_FPU_SHARING` | Automatic lazy FPU state preservation, tested in QEMU |

### Timeline Overview
1. **Phase 1**: Code refactoring (move Cortex-M code to extmod) - ~1-2 days
2. **Phase 2**: STM32 build system integration - ~1 day
3. **Phase 3**: Port-specific adaptations - ~2-3 days
4. **Phase 4**: Legacy code conditional exclusion - ~1 day
5. **Phase 5**: REPL/Event poll integration - ~1-2 days
6. **Phase 6**: Testing and validation on NUCLEO_WB55 - ~2-3 days

---

## 1. Current State Analysis

### 1.1 Extmod Changes (from `git diff micropython5 -- ./extmod`)

#### New Module: `extmod/zephyr_kernel/`
Complete Zephyr kernel integration module providing:

**Core Infrastructure**:
- `zephyr_kernel.mk` - Makefile for building Zephyr kernel with MicroPython
- `zephyr_config.h` - Zephyr kernel configuration (POSIX)
- `zephyr_config_cortex_m.h` - Zephyr kernel configuration (ARM Cortex-M)
- `zephyr_kernel.h` - Header exposing Zephyr integration API
- `zephyr_cstart.c` - Zephyr kernel initialization (replaces traditional main())

**Generated Headers** (`generated/zephyr/`):
- `version.h` - Generated from Zephyr VERSION file
- `syscalls/*.h` - Syscall wrappers (userspace disabled, inline wrappers only)
- `devicetree*.h` - Empty stubs (no device tree in bare-metal mode)
- `offsets.h` - Architecture-specific struct offsets for assembly
- `cmsis_core.h` - CMSIS wrapper routing to correct Cortex-M variant

**MicroPython Integration** (`kernel/`):
- `mpthread_zephyr.c` - MicroPython threading layer using Zephyr APIs

**Architecture Support** (`arch/`):
- Currently empty - QEMU port has `zephyr_arch_cortex_m.c` that belongs here

**POSIX Architecture Bridge**:
- `posix_minimal_board.c` - Minimal board support for POSIX architecture
- `generated/zephyr/arch/posix/*.h` - POSIX-specific headers

#### Modified Files

**`extmod/modtime.c`**:
Added debug fprintf() statements (lines 119-123, 126, 130):
```c
fprintf(stderr, "[time_sleep] START\n");
fprintf(stderr, "[time_sleep] calling mp_hal_delay_ms\n");
fprintf(stderr, "[time_sleep] mp_hal_delay_ms done\n");
fprintf(stderr, "[time_sleep] DONE\n");
```
**Action Required**: Remove debug statements before STM32 integration.

### 1.2 QEMU Port Changes (from `git diff micropython5 -- ./ports/qemu/`)

#### Build System

**`ports/qemu/Makefile`**:
- Added `MICROPY_ZEPHYR_THREADING` option (default 0)
- Conditional Zephyr integration (lines 162-194):
  - Includes `extmod/zephyr_kernel/zephyr_kernel.mk`
  - Adds `zephyr_arch_cortex_m.c` to build
  - Configures CFLAGS, warning suppressions
  - Builds Zephyr assembly files (swap_helper.S)
  - Generates offsets.h from offsets.c
- Pattern-specific CFLAGS for Zephyr objects (suppress warnings)

#### Board Configuration

**`ports/qemu/boards/MPS2_AN386/`** (new board):
- `mpconfigboard.mk`: Sets `MICROPY_ZEPHYR_THREADING=1`, Cortex-M4 flags
- `mpconfigboard.h`: Board name/MCU identification

#### Main Application

**`ports/qemu/main.c`**:
- Split initialization path:
  - **Without Zephyr**: Traditional `main()` entry point
  - **With Zephyr**: `micropython_main_thread_entry()` called from `z_cstart()`
- Thread initialization: `mp_thread_init(&stack_dummy)` with stack pointer
- Thread cleanup: `mp_thread_deinit()` before soft reboot
- Startup changes in `_start()`: Call `z_cstart()` instead of `main()`

**`ports/qemu/mphalport.c`**:
- Added `k_sleep(K_MSEC(1))` in `mp_hal_stdin_rx_chr()` polling loop
- **CRITICAL ISSUE**: This causes all tests to timeout (1ms sleep too long for PTY)
- Requires `#include "zephyr/kernel.h"` when threading enabled

**`ports/qemu/mphalport.h`**:
- Defined atomic sections using Zephyr primitives:
```c
#define MICROPY_BEGIN_ATOMIC_SECTION()     arch_irq_lock()
#define MICROPY_END_ATOMIC_SECTION(state)  arch_irq_unlock(state)
```

#### Configuration

**`ports/qemu/mpconfigport.h`**:
- Added Zephyr threading configuration block:
```c
#if MICROPY_ZEPHYR_THREADING
#define MICROPY_PY_THREAD 1
#define MICROPY_PY_THREAD_GIL 1
#define MICROPY_PY_THREAD_GIL_VM_DIVISOR 32
#define MICROPY_ENABLE_FINALISER 1
#endif
```

**`ports/qemu/mpthreadport.h`** (new file):
- Defines `mp_thread_mutex_t` using `struct k_mutex`
- Defines `mp_thread_recursive_mutex_t` (same as k_mutex - already recursive)
- Forward declarations for threading functions

#### Architecture Bridge

**`ports/qemu/zephyr_arch_cortex_m.c`** (new file, 382 lines):
**THIS IS THE KEY FILE TO MOVE TO EXTMOD**

Provides complete Cortex-M architecture layer for Zephyr:

- **Hardware Initialization**:
  - FPU configuration (CP10/CP11 access, FPCCR for lazy stacking)
  - SysTick timer setup (25MHz @ 1000Hz = 1ms ticks)
  - PendSV priority configuration (lowest priority for context switching)

- **Interrupt Handlers**:
  - `SysTick_Handler()`: Increments tick counter, calls `sys_clock_announce()`, triggers PendSV if higher-priority thread ready
  - `PendSV_Handler()`: Naked function jumping to `z_arm_pendsv` (Zephyr's context switcher)

- **Kernel Interfaces**:
  - `mp_zephyr_arch_init()`: Initialize arch-specific components
  - `mp_zephyr_arch_enable_systick_interrupt()`: Enable SysTick interrupt after kernel init
  - `mp_zephyr_arch_get_ticks()`: Return tick counter
  - `mp_zephyr_arch_yield()`: Trigger PendSV for context switch

- **Zephyr Stubs**:
  - System clock functions (`sys_clock_elapsed`, `sys_clock_set_timeout`)
  - SMP stubs (single-core architecture)
  - Object core stubs (statistics/tracing disabled)
  - Console output (`k_str_out`)
  - Fatal error handler

- **Minimal stdio stubs**: For bare-metal builds without full C library

**Architecture-Specific Configuration**:
- CPU frequency: `MICROPY_HW_CPU_FREQ_HZ` (default 25MHz)
- SysTick reload calculation for 1ms ticks
- Global `_kernel` structure and `z_idle_threads[]` array

#### Exception Handling

**`ports/qemu/mcu/arm/errorhandler.c`**:
- Conditionally exclude `PendSV_Handler` and `SysTick_Handler` when Zephyr enabled (Zephyr provides these)

**`ports/qemu/mcu/arm/startup.c`**:
- Call `z_cstart()` instead of `main()` when Zephyr enabled

### 1.3 STM32 Legacy Threading Implementation

#### Core Threading Files

**`ports/stm32/pybthread.c`** (234 lines):
Custom threading implementation with manual context switching:

- **Thread Structure** (`pyb_thread_t`):
  - Stack pointer (`sp`), stack buffer, stack length
  - Thread-local state pointer
  - Linked list pointers (all threads, runnable threads, queue)
  - Timeslice counter (4ms default)

- **Scheduler**:
  - Round-robin scheduling via circular linked list
  - Manual stack frame setup for new threads (r0-r12, lr, pc, xPSR, s16-s31)
  - FPU register space reserved (16 float registers)
  - Thread termination via `pyb_thread_terminate()`

- **Context Switching**:
  - Triggered by PendSV interrupt
  - `pyb_thread_next()`: Saves current SP, switches to next thread, returns new SP
  - Returns to thread mode with `lr = 0xfffffff9` (use MSP, non-FP)

- **Mutex Implementation**:
  - Simple lock with thread queue for blocked threads
  - `pyb_mutex_lock()`: Blocks by removing thread from runnable list
  - `pyb_mutex_unlock()`: Moves waiting thread back to runnable list

**`ports/stm32/pybthread.h`**:
- Defines `pyb_thread_t` structure
- Declares threading functions
- Defines `pyb_mutex_t` as `void*` (either NULL, LOCKED, or thread pointer)

**`ports/stm32/mpthreadport.c`** (100 lines):
MicroPython threading port layer:

- `mp_thread_init()`: Initialize mutex, set thread-local state
- `mp_thread_gc_others()`: Collect GC roots from all other threads' stacks
- `mp_thread_create()`: Allocate stack, create thread via `pyb_thread_new()`
- Thread ID is `pyb_thread_cur` pointer

**`ports/stm32/mpthreadport.h`**:
- Wraps `pyb_mutex_t` as `mp_thread_mutex_t`
- Inline functions forwarding to `pyb_mutex_*()` functions
- Thread-local storage via `pyb_thread_set_local()` / `pyb_thread_get_local()`

#### Context Switching

**`ports/stm32/pendsv.c`** (134 lines):
PendSV interrupt handler for context switching:

- **Dual Purpose**:
  1. Dispatch pending functions (soft timer, etc.) via `pendsv_dispatch_table`
  2. Thread context switching when `MICROPY_PY_THREAD` enabled

- **Assembly Implementation** (lines 94-132):
  - Check for pending dispatches first
  - If threading enabled:
    - Push r4-r11, lr, s16-s31 (FPU registers)
    - Save PRIMASK, disable interrupts
    - Call `pyb_thread_next()` to get next thread SP
    - Switch stack pointer
    - Restore PRIMASK, pop registers, return

- **FPU Handling**: Manually saves/restores s16-s31 (callee-saved FP registers)

**`ports/stm32/systick.c`** (lines 39-72):
SysTick interrupt handler (1ms tick):

- Increments `uwTick` (HAL tick counter)
- Dispatches registered handlers (round-robin)
- Triggers soft timer via PendSV if needed
- **Threading Support** (lines 61-71):
  - Decrements `pyb_thread_cur->timeslice`
  - When timeslice expires and other threads exist, trigger PendSV
  - Default timeslice: 4ms (set in `pyb_thread_next()`)

#### Configuration

**`ports/stm32/mpconfigport.h`** (lines 256-280):
Threading configuration and event poll hook:

```c
#if MICROPY_PY_THREAD
#define MICROPY_EVENT_POLL_HOOK \
    do { \
        extern void mp_handle_pending(bool); \
        mp_handle_pending(true); \
        if (pyb_thread_enabled) { \
            MP_THREAD_GIL_EXIT(); \
            pyb_thread_yield(); \
            MP_THREAD_GIL_ENTER(); \
        } else { \
            __WFI(); \
        } \
    } while (0);

#define MICROPY_THREAD_YIELD() pyb_thread_yield()
#else
#define MICROPY_EVENT_POLL_HOOK \
    do { \
        extern void mp_handle_pending(bool); \
        mp_handle_pending(true); \
        __WFI(); \
    } while (0);
#endif
```

**Key Pattern**: When threads enabled, yield to other threads; otherwise use WFI (wait for interrupt).

#### Build System

**Board Variants with Threading**:
Several boards have `mpconfigvariant_THREAD.mk` and `mpconfigvariant_DP_THREAD.mk` files:
- PYBV10, PYBV11, PYBLITEV10

These variants set `MICROPY_PY_THREAD=1` in CFLAGS.

### 1.4 Comparison: Legacy vs Zephyr Threading

| Feature | STM32 Legacy | Zephyr Kernel |
|---------|-------------|---------------|
| **Scheduler** | Custom round-robin, circular linked list | Priority-based, scalable queues |
| **Context Switch** | Manual assembly in PendSV | Zephyr `z_arm_pendsv` |
| **Timeslicing** | 4ms via SysTick decrement | Configurable via `CONFIG_TIMESLICE_SIZE` |
| **Mutex** | Simple lock with thread queue | Full k_mutex with priority inheritance |
| **FPU** | Manual s16-s31 save/restore | Automatic lazy stacking via FPCCR |
| **Thread Local Storage** | Single pointer per thread | Full TLS support |
| **Stack Allocation** | Manual m_new() | Zephyr k_thread_stack_alloc() or static |
| **Initialization** | `pyb_thread_init()` | `z_cstart()` with kernel init |
| **Code Size** | ~700 lines (pybthread + port) | ~15KB Zephyr kernel |
| **Portability** | STM32-specific | Any Zephyr-supported platform |

---

## 2. Code Organization Strategy

### 2.1 Directory Structure (Target State)

```
extmod/zephyr_kernel/
├── arch/
│   └── cortex_m/
│       ├── cortex_m_arch.c        # Moved from ports/qemu/zephyr_arch_cortex_m.c
│       ├── cortex_m_arch.h        # New header for arch interface
│       └── README.md              # Arch layer documentation
├── generated/
│   └── zephyr/
│       ├── arch/
│       │   ├── arm/               # ARM-specific generated headers (future)
│       │   └── posix/             # POSIX-specific generated headers
│       ├── syscalls/              # Syscall wrappers
│       ├── version.h              # Generated from Zephyr VERSION
│       ├── offsets.h              # Generated per-architecture
│       ├── devicetree*.h          # Empty stubs
│       └── cmsis_core.h           # CMSIS wrapper
├── kernel/
│   └── mpthread_zephyr.c         # MicroPython threading layer
├── posix_minimal_board.c         # POSIX architecture board support
├── zephyr_config.h               # Zephyr config for POSIX
├── zephyr_config_cortex_m.h      # Zephyr config for Cortex-M
├── zephyr_cstart.c               # Kernel initialization
├── zephyr_kernel.h               # Public API header
├── zephyr_kernel.mk              # Build system integration
└── gen_zephyr_version.py         # Version header generator

ports/stm32/
├── boards/
│   └── NUCLEO_WB55/
│       ├── mpconfigvariant_ZEPHYR.mk    # New: Zephyr threading variant
│       └── ... (existing files)
├── mpthreadport_zephyr.h         # New: Zephyr-based thread port header
├── zephyr_stm32.c                # New: STM32-specific Zephyr integration
├── main.c                        # Modified: Conditional Zephyr init
├── mphalport.h                   # Modified: Atomic sections for Zephyr
├── mpconfigport.h                # Modified: Zephyr config block
├── Makefile                      # Modified: Conditional Zephyr build
├── pybthread.c                   # Excluded when MICROPY_ZEPHYR_THREADING=1
├── pendsv.c                      # Modified: Exclude thread switch when Zephyr
└── systick.c                     # Modified: Conditional Zephyr clock announce
```

### 2.2 Layer Responsibilities

#### Extmod Layer (Portable)
**`extmod/zephyr_kernel/arch/cortex_m/cortex_m_arch.c`**:
- Generic Cortex-M initialization (FPU, SysTick, PendSV priority)
- Generic interrupt handlers (SysTick, PendSV wrapper)
- Generic Zephyr stubs (clock, fatal, console)
- Parameterized by CPU frequency, SysTick frequency from port config

#### Port Layer (STM32-Specific)
**`ports/stm32/zephyr_stm32.c`**:
- STM32 HAL integration (if needed)
- Board-specific initialization (clock setup, peripheral init)
- STM32-specific UART/console routing
- Hardware-specific interrupt handlers (if any)

#### Configuration Layer
**Port provides via mpconfigboard.h**:
- `MICROPY_HW_CPU_FREQ_HZ`: CPU frequency for SysTick calculation
- `CONFIG_SYS_CLOCK_TICKS_PER_SEC`: Tick frequency (default 1000Hz)
- `CONFIG_FPU_SHARING`: FPU context switching mode

### 2.3 Interface Contracts

#### Extmod → Port
Functions that extmod expects port to provide:
- `mp_hal_stdout_tx_strn()`: Console output (for k_str_out)
- Port-specific startup calls `mp_zephyr_arch_init()` during boot

#### Port → Extmod
Functions that extmod provides to port:
- `mp_zephyr_arch_init()`: Initialize architecture layer
- `mp_zephyr_arch_enable_systick_interrupt()`: Enable SysTick after kernel init
- `mp_zephyr_arch_get_ticks()`: Get current tick count
- `mp_zephyr_arch_yield()`: Trigger context switch
- `mp_zephyr_kernel_deinit()`: Cleanup on shutdown

---

## 3. Implementation Phases

### Phase 1: Code Refactoring (Move Cortex-M to Extmod)

**Objective**: Generalize `zephyr_arch_cortex_m.c` and move to `extmod/zephyr_kernel/arch/cortex_m/`.

#### Files to Create

**`extmod/zephyr_kernel/arch/cortex_m/cortex_m_arch.c`**:
- Copy from `ports/qemu/zephyr_arch_cortex_m.c`
- Remove QEMU-specific assumptions (CPU freq must come from config)
- Add configuration validation checks
- Keep all Zephyr stubs and interrupt handlers

**`extmod/zephyr_kernel/arch/cortex_m/cortex_m_arch.h`**:
```c
#ifndef EXTMOD_ZEPHYR_KERNEL_CORTEX_M_ARCH_H
#define EXTMOD_ZEPHYR_KERNEL_CORTEX_M_ARCH_H

void mp_zephyr_arch_init(void);
void mp_zephyr_arch_enable_systick_interrupt(void);
uint64_t mp_zephyr_arch_get_ticks(void);
void mp_zephyr_arch_yield(void);
void mp_zephyr_kernel_deinit(void);

#endif
```

**`extmod/zephyr_kernel/arch/cortex_m/README.md`**:
Documentation explaining:
- Required port configuration defines
- Initialization sequence
- Interrupt handler behavior
- Configuration options

#### Files to Modify

**`extmod/zephyr_kernel/zephyr_kernel.mk`**:
```makefile
# ARM Cortex-M architecture (for embedded targets)
ifeq ($(ZEPHYR_ARCH),arm)
# Add arch-specific source
ZEPHYR_ARCH_SRC_C += \
    $(ZEPHYR_KERNEL)/arch/cortex_m/cortex_m_arch.c

# Rest of ARM config...
endif
```

**`ports/qemu/Makefile`**:
- Remove `zephyr_arch_cortex_m.c` from SRC_C (now in ZEPHYR_ARCH_SRC_C)

#### Configuration Requirements

Port must define in `mpconfigboard.h`:
```c
#define MICROPY_HW_CPU_FREQ_HZ      (64000000)  // STM32WB55: 64MHz
#define CONFIG_SYS_CLOCK_TICKS_PER_SEC  1000     // 1ms ticks (default)
```

#### Testing
- Build QEMU port with refactored code
- Verify threading tests still pass
- Confirm no functional changes

---

### Phase 2: STM32 Build System Integration

**Objective**: Add Zephyr threading support to STM32 build system via board variant.

#### Files to Create

**`ports/stm32/boards/NUCLEO_WB55/mpconfigvariant_ZEPHYR.mk`**:
```makefile
# NUCLEO_WB55 board with Zephyr threading

# Enable Zephyr threading
MICROPY_ZEPHYR_THREADING = 1

# Pass flag to C compiler
CFLAGS += -DMICROPY_ZEPHYR_THREADING=1
```

#### Files to Modify

**`ports/stm32/Makefile`**:
Add after existing includes (around line 80):

```makefile
# Zephyr threading integration (optional)
MICROPY_ZEPHYR_THREADING ?= 0

ifeq ($(MICROPY_ZEPHYR_THREADING),1)
# Remove -Werror for Zephyr headers
CFLAGS := $(filter-out -Werror,$(CFLAGS))

# Set architecture for Zephyr
ZEPHYR_ARCH := arm

# Include Zephyr kernel build
include $(TOP)/extmod/zephyr_kernel/zephyr_kernel.mk

# Add Zephyr includes and flags
CFLAGS += $(ZEPHYR_INC) $(ZEPHYR_CFLAGS)

# Add STM32-specific Zephyr integration
SRC_C += zephyr_stm32.c

# Ensure Zephyr objects depend on generated headers
$(addprefix $(BUILD)/, $(SRC_THIRDPARTY_C:.c=.o)): $(ZEPHYR_GEN_HEADERS)

# Pattern-specific CFLAGS for Zephyr objects (suppress warnings)
$(BUILD)/$(TOP)/lib/zephyr/%.o: CFLAGS += -Wno-unused-parameter -Wno-sign-compare
$(BUILD)/$(TOP)/extmod/zephyr_kernel/%.o: CFLAGS += -Wno-unused-parameter

# Build Zephyr swap_helper.S (context switching)
ZEPHYR_SWAP_HELPER_O = $(BUILD)/swap_helper.o
OBJ += $(ZEPHYR_SWAP_HELPER_O)
OBJ += $(BUILD)/zephyr_offsets.o

$(ZEPHYR_SWAP_HELPER_O): $(ZEPHYR_ARCH_SRC_S) $(ZEPHYR_GEN_HEADERS)
	$(ECHO) "CC $<"
	$(Q)$(CC) -x assembler-with-cpp -D_ASMLANGUAGE \
		$(filter-out -std=% -Wdouble-promotion -Wfloat-conversion,$(CFLAGS)) \
		-Wa,-mimplicit-it=thumb -c -o $@ $<

# Conditionally exclude legacy threading files
SRC_C := $(filter-out pybthread.c mpthreadport.c,$(SRC_C))
endif
```

#### Build Command
```bash
cd ports/stm32
make BOARD=NUCLEO_WB55 BOARD_VARIANT=ZEPHYR
```

#### Testing
- Verify build completes without errors
- Check that Zephyr objects are compiled
- Confirm legacy threading files excluded
- Verify binary size increase (~15KB for Zephyr kernel)

---

### Phase 3: Port-Specific Adaptations

**Objective**: Create STM32-specific Zephyr integration and thread port layer.

#### Files to Create

**`ports/stm32/mpthreadport_zephyr.h`**:
```c
#ifndef MICROPY_INCLUDED_STM32_MPTHREADPORT_ZEPHYR_H
#define MICROPY_INCLUDED_STM32_MPTHREADPORT_ZEPHYR_H

#if MICROPY_ZEPHYR_THREADING

#include <zephyr/kernel.h>

// Mutex types using Zephyr k_mutex
typedef struct _mp_thread_mutex_t {
    struct k_mutex handle;
} mp_thread_mutex_t;

typedef struct _mp_thread_recursive_mutex_t {
    struct k_mutex handle;
} mp_thread_recursive_mutex_t;

// Threading functions
bool mp_thread_init(void *stack);
void mp_thread_deinit(void);
void mp_thread_gc_others(void);

#endif // MICROPY_ZEPHYR_THREADING

#endif // MICROPY_INCLUDED_STM32_MPTHREADPORT_ZEPHYR_H
```

**`ports/stm32/zephyr_stm32.c`**:
```c
// STM32-specific Zephyr integration
#include "py/runtime.h"
#include "py/mphal.h"

#if MICROPY_ZEPHYR_THREADING

#include <zephyr/kernel.h>
#include "extmod/zephyr_kernel/zephyr_kernel.h"

// STM32-specific initialization (if needed)
// Called from main() before z_cstart()
void stm32_zephyr_preinit(void) {
    // Any STM32-specific setup before kernel init
    // (Most should happen in standard STM32 init path)
}

// STM32-specific post-initialization (if needed)
// Called from micropython_main_thread_entry()
void stm32_zephyr_postinit(void) {
    // Any STM32-specific setup after kernel init
}

#endif // MICROPY_ZEPHYR_THREADING
```

#### Files to Modify

**`ports/stm32/mpconfigport.h`**:
Add after existing MICROPY_PY_THREAD block (around line 280):

```c
// Zephyr threading configuration
#if MICROPY_ZEPHYR_THREADING
#define MICROPY_PY_THREAD 1
#define MICROPY_PY_THREAD_GIL 1
#define MICROPY_PY_THREAD_GIL_VM_DIVISOR 32
#define MICROPY_ENABLE_FINALISER 1

// MICROPY_EVENT_POLL_HOOK: Use k_yield() instead of pyb_thread_yield()
#define MICROPY_EVENT_POLL_HOOK \
    do { \
        extern void mp_handle_pending(bool); \
        mp_handle_pending(true); \
        extern void k_yield(void); \
        MP_THREAD_GIL_EXIT(); \
        k_yield(); \
        MP_THREAD_GIL_ENTER(); \
    } while (0);

#define MICROPY_THREAD_YIELD() k_yield()

#elif MICROPY_PY_THREAD
// Legacy threading (existing code unchanged)
#define MICROPY_EVENT_POLL_HOOK /* ... existing ... */
#else
// No threading
#define MICROPY_EVENT_POLL_HOOK /* ... existing ... */
#endif
```

**`ports/stm32/mphalport.h`**:
Add after existing includes:

```c
// Atomic sections for Zephyr
#if MICROPY_ZEPHYR_THREADING
#include <zephyr/arch/cpu.h>
#define MICROPY_BEGIN_ATOMIC_SECTION()     arch_irq_lock()
#define MICROPY_END_ATOMIC_SECTION(state)  arch_irq_unlock(state)
#endif
```

**`ports/stm32/mpthreadport.h`**:
```c
// ... existing includes ...

#if MICROPY_ZEPHYR_THREADING
#include "mpthreadport_zephyr.h"
#else
#include "pybthread.h"
// ... existing legacy threading code ...
#endif
```

#### Testing
- Verify headers compile correctly
- Check #ifdef logic doesn't break legacy builds
- Test build with and without MICROPY_ZEPHYR_THREADING

---

### Phase 4: Legacy Code Conditional Exclusion

**Objective**: Prevent conflicts between legacy threading and Zephyr threading.

#### Files to Modify

**`ports/stm32/pendsv.c`**:
Modify PendSV handler assembly (line 107):

```c
#if MICROPY_PY_THREAD && !MICROPY_ZEPHYR_THREADING
        // Legacy threading: do context switch
        "push {r4-r11, lr}\n"
        "vpush {s16-s31}\n"
        // ... rest of legacy context switch ...
#elif MICROPY_ZEPHYR_THREADING
        // Zephyr threading: handled by Zephyr's z_arm_pendsv
        // This PendSV handler only handles dispatch, not threading
        "bx lr\n"
#else
        // No threading: spurious pendsv, just return
        "bx lr\n"
#endif
```

**Alternative**: Exclude entire file when Zephyr enabled:
```makefile
# In Makefile ZEPHYR block:
SRC_C := $(filter-out pendsv.c,$(SRC_C))
```
And provide minimal pendsv.c with only dispatch support.

**`ports/stm32/systick.c`**:
Modify SysTick_Handler (line 61):

```c
#if MICROPY_PY_THREAD && !MICROPY_ZEPHYR_THREADING
    // Legacy threading timeslicing
    if (pyb_thread_enabled) {
        if (pyb_thread_cur->timeslice == 0) {
            if (pyb_thread_cur->run_next != pyb_thread_cur) {
                SCB->ICSR = SCB_ICSR_PENDSVSET_Msk;
            }
        } else {
            --pyb_thread_cur->timeslice;
        }
    }
#elif MICROPY_ZEPHYR_THREADING
    // Zephyr threading: call Zephyr timer subsystem
    // NOTE: SysTick_Handler is provided by zephyr_arch_cortex_m.c,
    // so this handler may not be called at all when Zephyr enabled.
    // If we reach here, it means we're using hybrid mode.
    extern void sys_clock_announce(int32_t ticks);
    sys_clock_announce(1);

    // Check if reschedule needed
    extern struct z_kernel _kernel;
    if (_kernel.ready_q.cache != NULL &&
        _kernel.ready_q.cache != _kernel.cpus[0].current) {
        SCB->ICSR = SCB_ICSR_PENDSVSET_Msk;
    }
#endif
```

**Alternative**: Exclude entire SysTick_Handler when Zephyr enabled, as Zephyr provides its own.

**`ports/stm32/main.c`**:
Add conditional initialization:

```c
#if MICROPY_ZEPHYR_THREADING
// Zephyr threading initialization in z_main_thread context
void micropython_main_thread_entry(void *p1, void *p2, void *p3) {
    (void)p1; (void)p2; (void)p3;

    // Enable SysTick interrupt now that kernel is initialized
    extern void mp_zephyr_arch_enable_systick_interrupt(void);
    mp_zephyr_arch_enable_systick_interrupt();

    // STM32-specific post-init
    extern void stm32_zephyr_postinit(void);
    stm32_zephyr_postinit();

    // Initialize MicroPython threading
    char stack_dummy;
    if (!mp_thread_init(&stack_dummy)) {
        mp_printf(&mp_plat_print, "Failed to initialize threading\n");
        for (;;) {}  // Can't return from thread context
    }

    // Continue with existing main loop...
    goto soft_reset;  // Jump to existing code
#else
// Existing main() function
int main(void) {
    // ... existing STM32 initialization ...

    #if MICROPY_PY_THREAD
    pyb_thread_init(&pyb_thread_main);
    #endif
```

Add at end of existing main initialization (before soft_reset):
```c
#if MICROPY_ZEPHYR_THREADING
    // When Zephyr enabled, use z_cstart() instead of continuing here
    extern void z_cstart(void);
    z_cstart();  // Never returns
#endif
```

#### Testing
- Build with legacy threading: verify no changes
- Build with Zephyr threading: verify legacy code excluded
- Check binary size difference
- Verify no linker errors for duplicate symbols

---

### Phase 5: REPL/Event Poll Integration

**Objective**: Ensure responsive REPL with proper thread yielding.

#### Critical Learning from QEMU

**QEMU's k_sleep() Problem**:
- Adding `k_sleep(K_MSEC(1))` to stdin polling causes 1ms latency per poll
- Test framework via PTY cannot tolerate this latency
- All tests timeout (both threading and non-threading)

**STM32's Working Pattern**:
- Uses `MICROPY_EVENT_POLL_HOOK` with `pyb_thread_yield()`
- Only yields when threads enabled, otherwise uses `__WFI()`
- No fixed sleep duration - yields immediately if no work to do

#### Implementation

Already covered in Phase 3 `mpconfigport.h` changes:
```c
#define MICROPY_EVENT_POLL_HOOK \
    do { \
        extern void mp_handle_pending(bool); \
        mp_handle_pending(true); \
        extern void k_yield(void); \
        MP_THREAD_GIL_EXIT(); \
        k_yield(); \
        MP_THREAD_GIL_ENTER(); \
    } while (0);
```

#### Difference from QEMU

**QEMU** (incorrect):
```c
#if MICROPY_PY_THREAD
k_sleep(K_MSEC(1));  // WRONG: Causes timeouts
#endif
```

**STM32 with Zephyr** (correct):
```c
k_yield();  // RIGHT: Yields without delay
```

#### Why k_yield() Works

`k_yield()` in Zephyr:
- Immediately gives up CPU to equal/higher priority threads
- Returns immediately if no other threads ready
- No minimum sleep time
- Ideal for polling loops that need to remain responsive

#### Testing
- Test REPL interactivity: type commands, observe prompt responsiveness
- Test long-running Python code with Ctrl+C interrupt
- Test `time.sleep()` accuracy
- Verify no regressions in non-threaded builds

---

### Phase 6: Testing and Validation

**Objective**: Verify Zephyr threading works correctly on NUCLEO_WB55 hardware.

#### Test Environment Setup

**Hardware**:
- NUCLEO_WB55 board
- ST-Link programmer (built into Nucleo)
- USB serial connection for REPL

**Build and Flash**:
```bash
cd ports/stm32
make BOARD=NUCLEO_WB55 BOARD_VARIANT=ZEPHYR
make BOARD=NUCLEO_WB55 BOARD_VARIANT=ZEPHYR deploy-stlink
```

**Connect**:
```bash
mpremote connect /dev/serial/by-id/usb-STMicroelectronics_* resume
```

#### Test Suite

**Test 1: Basic Thread Creation**
```python
import _thread

def worker():
    print("Thread started")

tid = _thread.start_new_thread(worker, ())
print(f"Created thread {tid}")
```

**Expected**: Thread executes, prints message, returns non-zero thread ID.

**Test 2: Thread Arguments**
```python
import _thread

def worker(name, count):
    for i in range(count):
        print(f"{name}: {i}")

_thread.start_new_thread(worker, ("Thread-A", 3))
_thread.start_new_thread(worker, ("Thread-B", 3))
```

**Expected**: Both threads execute concurrently, output interleaved.

**Test 3: Lock/Mutex**
```python
import _thread
import time

lock = _thread.allocate_lock()
counter = 0

def worker(n):
    global counter
    for _ in range(100):
        lock.acquire()
        counter += 1
        lock.release()
    print(f"Worker {n} done, counter={counter}")

_thread.start_new_thread(worker, (1,))
_thread.start_new_thread(worker, (2,))
time.sleep(1)
print(f"Final counter: {counter}")
```

**Expected**: Final counter = 200 (no race conditions).

**Test 4: FPU Context Preservation**
```python
import _thread
import time

results = {}

def fpu_worker(thread_id):
    # Perform FPU operations
    base = thread_id * 1000.0
    for i in range(10):
        a = base + float(i)
        b = a * 1.5
        c = b / 2.0
        d = c + a * 0.25
        time.sleep_ms(1)  # Force context switch
        e = d * 2.0 - a
        result = e
    results[thread_id] = result
    print(f"Thread {thread_id}: result={result:.6f}")

_thread.start_new_thread(fpu_worker, (1,))
_thread.start_new_thread(fpu_worker, (2,))
_thread.start_new_thread(fpu_worker, (3,))
time.sleep(1)
print(f"Results: {results}")
```

**Expected**: Each thread produces correct result, no FPU corruption.

**Test 5: REPL Responsiveness**
```python
import _thread
import time

def busy_thread():
    while True:
        # Busy loop
        pass

_thread.start_new_thread(busy_thread, ())
time.sleep(0.1)
# Now try to interact with REPL
print("Can still use REPL")
```

**Expected**: REPL remains responsive despite busy thread.

**Test 6: Thread Count**
```python
import _thread

threads = []
for i in range(10):
    tid = _thread.start_new_thread(lambda: None, ())
    threads.append(tid)

print(f"Created {len(threads)} threads")
```

**Expected**: Successfully creates 10 threads (tests thread limit).

**Test 7: GC During Threading**
```python
import _thread
import gc

def allocate_memory():
    for _ in range(100):
        x = [1] * 1000
    print("Thread done")

_thread.start_new_thread(allocate_memory, ())
gc.collect()  # Force GC while thread running
```

**Expected**: GC completes without crashing, thread continues.

#### Performance Benchmarks

**Context Switch Overhead**:
```python
import _thread
import time

COUNT = 1000
lock = _thread.allocate_lock()

def worker():
    for _ in range(COUNT):
        lock.acquire()
        lock.release()

lock.acquire()
start = time.ticks_ms()
_thread.start_new_thread(worker, ())
for _ in range(COUNT):
    lock.release()
    lock.acquire()
elapsed = time.ticks_diff(time.ticks_ms(), start)
print(f"Context switches: {COUNT * 2}, time: {elapsed}ms")
print(f"Per switch: {elapsed / (COUNT * 2):.3f}ms")
```

**Expected**: Context switch time < 0.1ms on 64MHz CPU.

#### Regression Testing

**Non-Threaded Build**:
```bash
make BOARD=NUCLEO_WB55  # No BOARD_VARIANT
make BOARD=NUCLEO_WB55 deploy-stlink
```

Run standard test suite to ensure no regressions:
```bash
../../tests/run-tests.py --target stm32 --device /dev/serial/by-id/usb-*
```

**Legacy Threading Build**:
```bash
make BOARD=PYBV11 BOARD_VARIANT=THREAD
```

Verify legacy threading still works correctly.

#### Validation Criteria

✓ All basic tests pass
✓ FPU context preserved across threads
✓ REPL remains responsive
✓ GC works correctly with threads
✓ Performance acceptable (< 10% overhead vs legacy)
✓ No regressions in non-threaded builds
✓ Legacy threading still functional

---

## 4. File-by-File Migration Matrix

### Extmod Files

| File | Current State | Required Changes | Final State |
|------|--------------|------------------|-------------|
| `extmod/modtime.c` | Has debug fprintf() | Remove debug statements | Clean |
| `extmod/zephyr_kernel/arch/cortex_m/cortex_m_arch.c` | **Doesn't exist** | Move from `ports/qemu/zephyr_arch_cortex_m.c`, generalize | **New** |
| `extmod/zephyr_kernel/arch/cortex_m/cortex_m_arch.h` | **Doesn't exist** | Create header with arch interface | **New** |
| `extmod/zephyr_kernel/arch/cortex_m/README.md` | **Doesn't exist** | Document arch layer requirements | **New** |
| `extmod/zephyr_kernel/zephyr_kernel.mk` | Exists | Update to include cortex_m_arch.c | Modified |
| (other extmod/zephyr_kernel/*) | Exist | No changes | Unchanged |

### QEMU Port Files

| File | Current State | Required Changes | Final State |
|------|--------------|------------------|-------------|
| `ports/qemu/zephyr_arch_cortex_m.c` | Port-specific | **MOVE to extmod** | **Deleted** |
| `ports/qemu/Makefile` | Includes zephyr_arch_cortex_m.c | Remove (now in ZEPHYR_ARCH_SRC_C) | Modified |
| `ports/qemu/main.c` | Conditional Zephyr init | No changes | Unchanged |
| `ports/qemu/mpconfigport.h` | Zephyr config block | No changes | Unchanged |
| `ports/qemu/mpthreadport.h` | Zephyr mutex types | No changes | Unchanged |
| `ports/qemu/mphalport.h` | Atomic sections | No changes | Unchanged |
| `ports/qemu/mphalport.c` | Has k_sleep() | No changes (issue documented) | Unchanged |

### STM32 Port Files (New/Modified)

| File | Current State | Required Changes | Final State |
|------|--------------|------------------|-------------|
| `ports/stm32/boards/NUCLEO_WB55/mpconfigvariant_ZEPHYR.mk` | **Doesn't exist** | Create with MICROPY_ZEPHYR_THREADING=1 | **New** |
| `ports/stm32/mpthreadport_zephyr.h` | **Doesn't exist** | Create Zephyr mutex/thread types | **New** |
| `ports/stm32/zephyr_stm32.c` | **Doesn't exist** | Create STM32-specific integration | **New** |
| `ports/stm32/main.c` | Legacy threading init | Add conditional Zephyr init path | Modified |
| `ports/stm32/mpconfigport.h` | Legacy MICROPY_EVENT_POLL_HOOK | Add Zephyr config block with k_yield() | Modified |
| `ports/stm32/mphalport.h` | No atomic sections | Add Zephyr atomic section macros | Modified |
| `ports/stm32/mpthreadport.h` | Uses pybthread.h | Add conditional include of mpthreadport_zephyr.h | Modified |
| `ports/stm32/Makefile` | No Zephyr support | Add conditional Zephyr build block | Modified |
| `ports/stm32/pybthread.c` | Legacy threading impl | Exclude when MICROPY_ZEPHYR_THREADING=1 | Conditional |
| `ports/stm32/mpthreadport.c` | Legacy port layer | Exclude when MICROPY_ZEPHYR_THREADING=1 | Conditional |
| `ports/stm32/pendsv.c` | Legacy context switch | Exclude thread switch when Zephyr | Modified |
| `ports/stm32/systick.c` | Legacy timeslicing | Add Zephyr clock announce branch | Modified |

### STM32 Port Files (Unchanged)

| File | Status | Reason |
|------|--------|--------|
| `ports/stm32/pybthread.h` | Unchanged | Only included in legacy mode |
| `ports/stm32/gccollect.c` | Unchanged | Thread scanning generic |
| `ports/stm32/modmachine.c` | Unchanged | No threading-specific code |
| `ports/stm32/irq.c` | Unchanged | No threading-specific code |

---

## 5. Technical Deep Dives

### 5.1 SysTick Integration

#### Legacy STM32 (systick.c)

```c
void SysTick_Handler(void) {
    uwTick++;  // HAL tick counter
    SysTick->CTRL;  // Clear COUNTFLAG

    // Dispatch handlers
    systick_dispatch_table[uwTick & (SLOTS - 1)]();

    // Soft timer check
    if (soft_timer_next == uwTick) {
        pendsv_schedule_dispatch(PENDSV_DISPATCH_SOFT_TIMER, soft_timer_handler);
    }

    // Threading timeslicing
    if (pyb_thread_enabled) {
        if (pyb_thread_cur->timeslice == 0) {
            if (pyb_thread_cur->run_next != pyb_thread_cur) {
                SCB->ICSR = SCB_ICSR_PENDSVSET_Msk;  // Trigger context switch
            }
        } else {
            --pyb_thread_cur->timeslice;  // Decrement 4ms timeslice
        }
    }
}
```

#### Zephyr (cortex_m_arch.c)

```c
void SysTick_Handler(void) {
    cortexm_arch_state.ticks++;  // Zephyr tick counter

    // Call Zephyr timer subsystem
    extern void sys_clock_announce(int32_t ticks);
    sys_clock_announce(1);  // This processes timeouts, wakes threads

    // Check if reschedule needed
    extern struct z_kernel _kernel;
    if (_kernel.ready_q.cache != NULL &&
        _kernel.ready_q.cache != _kernel.cpus[0].current) {
        SCB->ICSR = SCB_ICSR_PENDSVSET_Msk;  // Trigger PendSV
    }
}
```

#### Key Differences

| Feature | Legacy STM32 | Zephyr |
|---------|-------------|--------|
| Tick counter | `uwTick` (HAL) | `cortexm_arch_state.ticks` |
| Dispatch | systick_dispatch_table | N/A (Zephyr uses work queues) |
| Soft timer | pendsv_schedule_dispatch | N/A (handled by Zephyr timer subsystem) |
| Timeslicing | Fixed 4ms via counter decrement | Configurable via `CONFIG_TIMESLICE_SIZE` |
| Thread wakeup | Manual in pyb_thread_next | Automatic via `sys_clock_announce()` |
| Scheduling decision | Check run_next pointer | Check `_kernel.ready_q.cache` |

#### Integration Strategy for STM32

**Option A: Replace SysTick_Handler entirely**
- When `MICROPY_ZEPHYR_THREADING=1`, Zephyr's handler takes over
- STM32's systick dispatch and soft timer must integrate with Zephyr

**Option B: Hybrid handler**
- Keep STM32's dispatch and soft timer logic
- Add Zephyr's `sys_clock_announce()` when threading enabled
- More complex, but preserves existing functionality

**Recommendation**: Option A - let Zephyr own SysTick completely, integrate dispatch via Zephyr work queues.

### 5.2 PendSV Handler Replacement

#### Legacy STM32 (pendsv.c)

```asm
__asm volatile (
    // Check for pending dispatches
    "ldr r1, pendsv_dispatch_active_ptr\n"
    "ldr r0, [r1]\n"
    "cmp r0, #0\n"
    "beq .no_dispatch\n"
    "mov r2, #0\n"
    "str r2, [r1]\n"
    "b pendsv_dispatch_handler\n"
    ".no_dispatch:\n"

    // Thread context switch
    "push {r4-r11, lr}\n"        // Save callee-saved registers
    "vpush {s16-s31}\n"          // Save FPU registers
    "mrs r5, primask\n"          // Save interrupt state
    "cpsid i\n"                  // Disable interrupts
    "mov r0, sp\n"               // Current SP
    "mov r4, lr\n"               // Save LR
    "bl pyb_thread_next\n"       // Get next thread SP
    "mov lr, r4\n"               // Restore LR
    "mov sp, r0\n"               // Switch to new thread SP
    "msr primask, r5\n"          // Restore interrupt state
    "vpop {s16-s31}\n"           // Restore FPU registers
    "pop {r4-r11, lr}\n"         // Restore registers
    "bx lr\n"                    // Return
);
```

#### Zephyr (swap_helper.S)

Zephyr provides `z_arm_pendsv` in `arch/arm/core/cortex_m/swap_helper.S`:
- Saves full context (r0-r3, r12, lr, pc, xPSR automatically by hardware)
- Saves r4-r11, optionally FPU registers based on CONFIG_FPU_SHARING
- Calls `z_get_next_switch_handle()` to select next thread
- Switches stack pointer
- Restores context and returns

#### Integration Strategy

**Dispatch Handling**:
- STM32's `pendsv_dispatch_handler` is orthogonal to threading
- Keep dispatch logic, but delegate threading to Zephyr

**Modified PendSV_Handler**:
```c
__attribute__((naked)) void PendSV_Handler(void) {
    __asm volatile (
        #if defined(PENDSV_DISPATCH_NUM_SLOTS)
        // Check if there are any pending dispatches
        "ldr r1, pendsv_dispatch_active_ptr\n"
        "ldr r0, [r1]\n"
        "cmp r0, #0\n"
        "beq .no_dispatch\n"
        "mov r2, #0\n"
        "str r2, [r1]\n"
        "b pendsv_dispatch_handler\n"
        ".no_dispatch:\n"
        #endif

        #if MICROPY_ZEPHYR_THREADING
        // Zephyr threading: jump to Zephyr's handler
        "b z_arm_pendsv\n"
        #elif MICROPY_PY_THREAD
        // Legacy threading
        "push {r4-r11, lr}\n"
        // ... legacy context switch ...
        #else
        // No threading
        "bx lr\n"
        #endif

        // Data
        ".align 2\n"
        #if defined(PENDSV_DISPATCH_NUM_SLOTS)
        "pendsv_dispatch_active_ptr: .word pendsv_dispatch_active\n"
        #endif
    );
}
```

**Key Point**: Dispatch handler returns normally, then falls through to threading handler.

### 5.3 FPU Context Switching

#### Legacy STM32 Manual FPU Save

In `pybthread.c`, thread stack frame includes:
```c
stack_top -= 16;  // s16-s31 (we assume all threads use FP registers)
```

In `pendsv.c`, assembly manually saves/restores:
```asm
"vpush {s16-s31}\n"  // Save 16 FPU registers (64 bytes)
// ...
"vpop {s16-s31}\n"   // Restore 16 FPU registers
```

**Hardware automatic save**: s0-s15, FPSCR (r0-r3, r12, lr, pc, xPSR also saved by hardware)
**Software manual save**: s16-s31 (callee-saved registers)

#### Zephyr FPU Handling

**Configuration**:
```c
#define CONFIG_FPU 1
#define CONFIG_FPU_SHARING 1  // Enable lazy FPU context preservation
```

**Lazy Stacking (FPCCR register)**:
```c
// In cortex_m_arch.c
FPU->FPCCR = FPU_FPCCR_ASPEN_Msk | FPU_FPCCR_LSPEN_Msk;
```

- **ASPEN** (Automatic State Preservation): Hardware automatically saves FPU state on exception
- **LSPEN** (Lazy State Preservation): Only save if FPU used since last exception

**Context Switch Behavior**:
1. Thread A uses FPU → FPU context marked "dirty"
2. Exception occurs → Hardware saves s0-s15, FPSCR to Thread A's stack (if lazy mode enabled)
3. Thread B runs, uses FPU → Triggers lazy state save, completes save of Thread A's FPU state
4. Thread B's FPU context saved when it switches out
5. Return to Thread A → Hardware restores FPU state

**Zephyr's swap_helper.S**:
```asm
#ifdef CONFIG_FPU_SHARING
    // Check if FPU context needs saving (FPCCR.LSPACT bit)
    // If needed, trigger lazy save by accessing FPU register
    // Allocate space for full FPU context on stack (s0-s31, FPSCR)
#endif
```

#### Why Zephyr's Approach is Better

| Aspect | Legacy Manual | Zephyr Lazy |
|--------|--------------|-------------|
| **Code complexity** | Manual assembly in PendSV | Hardware-assisted + Zephyr helper |
| **Performance** | Always saves s16-s31 | Only saves if FPU used |
| **Correctness** | Easy to miss edge cases | Hardware enforced |
| **Portability** | Port-specific | Works on all ARMv7-M with FPU |

#### Verification Test

Already provided in QEMU port as `test_fpu_threading.py`:
- Creates 3 threads performing FPU-intensive calculations
- Forces context switches via `time.sleep_ms(1)`
- Verifies each thread produces correct result
- Detects FPU register corruption

**Adapt for STM32**:
```python
# Same test, but run on NUCLEO_WB55
# Expected: All threads pass, no FPU corruption
```

### 5.4 Thread-Local Storage

#### Legacy STM32

**Simple pointer per thread**:
```c
struct pyb_thread {
    // ...
    void *local_state;  // Points to mp_state_thread_t
    // ...
};
```

**Access**:
```c
void pyb_thread_set_local(void *state) {
    pyb_thread_cur->local_state = state;
}

void *pyb_thread_get_local(void) {
    return pyb_thread_cur->local_state;
}
```

**MicroPython usage**:
```c
mp_thread_set_state(&mp_state_ctx.thread);
struct _mp_state_thread_t *state = mp_thread_get_state();
```

#### Zephyr

**Built-in TLS support**:
```c
// In mpthread_zephyr.c
static void mp_thread_entry(void *p1, void *p2, void *p3) {
    struct _mp_state_thread_t *state = (struct _mp_state_thread_t *)p1;
    // Store in thread-local storage
    k_thread_custom_data_set(state);
    // ...
}

struct _mp_state_thread_t *mp_thread_get_state(void) {
    return (struct _mp_state_thread_t *)k_thread_custom_data_get();
}
```

**Zephyr API**:
- `k_thread_custom_data_set(void *data)`: Store pointer in current thread
- `k_thread_custom_data_get()`: Retrieve pointer from current thread

**Implementation**: Stored in `struct k_thread` member `void *custom_data`

#### STM32 Integration

No changes needed - MicroPython's `mpthread_zephyr.c` handles TLS via Zephyr API.

### 5.5 Mutex Implementation

#### Legacy STM32

**Type**:
```c
typedef void *pyb_mutex_t;
#define PYB_MUTEX_UNLOCKED ((void *)0)
#define PYB_MUTEX_LOCKED ((void *)1)
```

**States**:
- `NULL` (0) = unlocked
- `0x1` = locked, no waiters
- `thread_ptr` = locked, head of waiter queue

**Lock**:
```c
int pyb_mutex_lock(pyb_mutex_t *m, int wait) {
    uint32_t irq_state = RAISE_IRQ_PRI();  // Mask lower priority IRQs
    if (*m == PYB_MUTEX_UNLOCKED) {
        *m = PYB_MUTEX_LOCKED;
        RESTORE_IRQ_PRI(irq_state);
        return 1;  // Acquired
    }
    if (!wait) {
        RESTORE_IRQ_PRI(irq_state);
        return 0;  // Try-lock failed
    }
    // Add current thread to wait queue
    if (*m == PYB_MUTEX_LOCKED) {
        *m = pyb_thread_cur;
    } else {
        // Append to queue
        for (pyb_thread_t *n = *m;; n = n->queue_next) {
            if (n->queue_next == NULL) {
                n->queue_next = pyb_thread_cur;
                break;
            }
        }
    }
    pyb_thread_cur->queue_next = NULL;
    pyb_thread_remove_from_runable(pyb_thread_cur);
    SCB->ICSR = SCB_ICSR_PENDSVSET_Msk;  // Trigger context switch
    RESTORE_IRQ_PRI(irq_state);
    return 1;  // Will block, acquired when woken
}
```

**Unlock**:
```c
void pyb_mutex_unlock(pyb_mutex_t *m) {
    uint32_t irq_state = RAISE_IRQ_PRI();
    if (*m == PYB_MUTEX_LOCKED) {
        *m = PYB_MUTEX_UNLOCKED;
    } else {
        pyb_thread_t *th = *m;
        *m = (th->queue_next == NULL) ? PYB_MUTEX_LOCKED : th->queue_next;
        pyb_thread_add_to_runable(th);
    }
    RESTORE_IRQ_PRI(irq_state);
}
```

**Features**:
- Simple FIFO queue
- No priority inheritance
- No timeout support
- Non-recursive

#### Zephyr

**Type**:
```c
struct k_mutex {
    struct k_thread *owner;
    uint32_t lock_count;
    int original_priority;
    _wait_q_t wait_q;
};
```

**Features**:
- **Recursive**: Same thread can lock multiple times (lock_count)
- **Priority inheritance**: If high-priority thread blocks, boosts lock holder's priority
- **Timeout support**: Can wait with timeout
- **Robust**: Handles thread termination while holding lock

**API**:
```c
void k_mutex_init(struct k_mutex *mutex);
int k_mutex_lock(struct k_mutex *mutex, k_timeout_t timeout);
int k_mutex_unlock(struct k_mutex *mutex);
```

**MicroPython Integration**:
```c
// In mpthreadport_zephyr.h
typedef struct _mp_thread_mutex_t {
    struct k_mutex handle;
} mp_thread_mutex_t;

// In mpthread_zephyr.c
void mp_thread_mutex_init(mp_thread_mutex_t *m) {
    k_mutex_init(&m->handle);
}

int mp_thread_mutex_lock(mp_thread_mutex_t *m, int wait) {
    if (wait) {
        return k_mutex_lock(&m->handle, K_FOREVER) == 0 ? 1 : 0;
    } else {
        return k_mutex_lock(&m->handle, K_NO_WAIT) == 0 ? 1 : 0;
    }
}

void mp_thread_mutex_unlock(mp_thread_mutex_t *m) {
    k_mutex_unlock(&m->handle);
}
```

#### Comparison

| Feature | Legacy STM32 | Zephyr k_mutex |
|---------|-------------|----------------|
| Recursive | No | Yes (built-in) |
| Priority inheritance | No | Yes |
| Timeout | No | Yes |
| Error handling | Minimal | Robust (detects errors) |
| Code size | ~100 bytes | ~500 bytes |
| Performance | Slightly faster (simpler) | Slightly slower (more features) |

---

## 6. Known Issues & Risks

### 6.1 REPL Timeout Issue (QEMU)

**Issue**: Adding `k_sleep(K_MSEC(1))` to stdin polling loop causes all tests to timeout.

**Root Cause**: 1ms sleep per poll iteration introduces unacceptable latency for PTY communication.

**Status**: **RESOLVED** - Use `k_yield()` instead of `k_sleep()` (documented in Phase 5).

**STM32 Mitigation**: STM32 will use `MICROPY_EVENT_POLL_HOOK` with `k_yield()`, avoiding this issue.

### 6.2 FPU Register Preservation

**Risk**: Zephyr's lazy FPU stacking may not work correctly on all STM32 variants.

**Mitigation**:
- Comprehensive FPU test (test_fpu_threading.py)
- Test on multiple Cortex-M4F/M7F boards
- Verify FPCCR configuration matches hardware capabilities

**Validation**: Run FPU test on NUCLEO_WB55 before broader rollout.

### 6.3 Code Size Increase

**Impact**: Zephyr kernel adds ~15KB compared to legacy threading (~0.7KB).

**Mitigation**:
- Only enable on boards with sufficient flash (512KB+)
- Board variant approach allows per-board decision
- Consider making Zephyr threading opt-in via BOARD_VARIANT

**NUCLEO_WB55**: 1MB flash, 256KB RAM - sufficient capacity.

### 6.4 Performance Overhead

**Risk**: Zephyr's more complex scheduler may have higher context switch overhead.

**Mitigation**:
- Benchmark context switch time on target hardware
- Compare thread creation/destruction performance
- If significant degradation, consider optimizations:
  - Reduce `CONFIG_NUM_PREEMPT_PRIORITIES` (default 15)
  - Disable unused Zephyr features
  - Tune `CONFIG_TIMESLICE_SIZE`

**Acceptance Criteria**: < 20% overhead vs legacy threading.

### 6.5 Compatibility with STM32 HAL

**Risk**: Zephyr's SysTick handler conflicts with STM32 HAL's use of `uwTick`.

**Mitigation**:
- Ensure `uwTick` is still incremented (HAL functions depend on it)
- Option A: Keep incrementing in Zephyr's SysTick_Handler
- Option B: Redirect HAL_GetTick() to use Zephyr's tick count

**Recommendation**: Option A (simpler, less invasive).

### 6.6 Bluetooth Stack Interaction (WB55)

**Risk**: NUCLEO_WB55 uses STM32WB's BLE coprocessor. Thread switching timing may affect BLE.

**Mitigation**:
- Test BLE functionality with Zephyr threading enabled
- Verify BLE event processing doesn't starve other threads
- May need BLE thread priority tuning

**Test Plan**: Run BLE examples while threading active.

### 6.7 Soft Timer Integration

**Risk**: STM32's soft timer dispatch may conflict with Zephyr's timer subsystem.

**Mitigation**:
- Option A: Migrate soft timer to use Zephyr work queues
- Option B: Keep pendsv_dispatch_table for soft timer, use Zephyr only for threading
- Option C: Replace soft timer with Zephyr k_timer API

**Recommendation**: Option B (least invasive for Phase 1).

---

## 7. Configuration Reference

### 7.1 Zephyr Kernel Configuration

**File**: `extmod/zephyr_kernel/zephyr_config_cortex_m.h`

**Key Configuration Options**:

```c
// Threading
#define CONFIG_MULTITHREADING 1
#define CONFIG_NUM_PREEMPT_PRIORITIES 15
#define CONFIG_TIMESLICE_SIZE 10  // 10ms timeslice (10 ticks @ 1000Hz)
#define CONFIG_TIMESLICING 1

// FPU
#define CONFIG_FPU 1
#define CONFIG_FPU_SHARING 1  // Lazy FPU context save

// Scheduler
#define CONFIG_SCHED_SCALABLE 1  // Use priority-based scalable queue
#define CONFIG_WAITQ_SCALABLE 1

// Synchronization
#define CONFIG_MUTEX 1
#define CONFIG_SEM 1
#define CONFIG_CONDVAR 1

// Timing
#define CONFIG_SYS_CLOCK_EXISTS 1
#define CONFIG_SYS_CLOCK_TICKS_PER_SEC 1000  // 1ms ticks

// Memory
#define CONFIG_KERNEL_MEM_POOL 1

// Disabled features
#define CONFIG_USERSPACE 0  // No userspace, supervisor only
#define CONFIG_DEVICE 0     // No device drivers
#define CONFIG_LOG 0        // No logging subsystem
#define CONFIG_PRINTK 0     // No printk
```

### 7.2 Port Configuration (STM32)

**File**: `ports/stm32/boards/NUCLEO_WB55/mpconfigboard.h`

**Required Defines**:

```c
// CPU frequency for SysTick calculation
#define MICROPY_HW_CPU_FREQ_HZ  (64000000)  // 64MHz

// Optional: Override tick frequency (default 1000Hz)
// #define CONFIG_SYS_CLOCK_TICKS_PER_SEC  1000
```

### 7.3 Build Configuration

**File**: `ports/stm32/boards/NUCLEO_WB55/mpconfigvariant_ZEPHYR.mk`

```makefile
# Enable Zephyr threading
MICROPY_ZEPHYR_THREADING = 1

# Pass to compiler
CFLAGS += -DMICROPY_ZEPHYR_THREADING=1

# Optional: Adjust thread stack size
# CFLAGS += -DMICROPY_THREAD_STACK_SIZE=8192
```

---

## 8. Build Commands

### 8.1 STM32 with Zephyr Threading

```bash
cd ports/stm32

# Clean build
make BOARD=NUCLEO_WB55 BOARD_VARIANT=ZEPHYR clean

# Build
make BOARD=NUCLEO_WB55 BOARD_VARIANT=ZEPHYR

# Deploy via ST-Link
make BOARD=NUCLEO_WB55 BOARD_VARIANT=ZEPHYR deploy-stlink

# Deploy via DFU (if bootloader installed)
make BOARD=NUCLEO_WB55 BOARD_VARIANT=ZEPHYR deploy
```

### 8.2 STM32 with Legacy Threading

```bash
# Use existing THREAD variant (if available)
make BOARD=PYBV11 BOARD_VARIANT=THREAD
make BOARD=PYBV11 BOARD_VARIANT=THREAD deploy-stlink
```

### 8.3 STM32 without Threading

```bash
# Default build (no variant)
make BOARD=NUCLEO_WB55
make BOARD=NUCLEO_WB55 deploy-stlink
```

### 8.4 QEMU with Zephyr Threading

```bash
cd ports/qemu

# Build MPS2_AN386 board (Cortex-M4 with threading)
make BOARD=MPS2_AN386

# Run
./build-MPS2_AN386/firmware.elf

# Run with test
echo "import _thread; print('Thread test')" | ./build-MPS2_AN386/firmware.elf
```

---

## 9. Debugging Guide

### 9.1 Build Issues

**Error: "MICROPY_HW_CPU_FREQ_HZ not defined"**
- Add to `mpconfigboard.h`: `#define MICROPY_HW_CPU_FREQ_HZ (64000000)`

**Error: "zephyr/kernel.h: No such file"**
- Check `ZEPHYR_INC` in Makefile includes `lib/zephyr/include`
- Ensure `git submodule update --init lib/zephyr` was run

**Error: "Undefined reference to z_arm_pendsv"**
- Ensure `swap_helper.S` is built and linked
- Check `ZEPHYR_SWAP_HELPER_O` in Makefile

**Error: "Multiple definition of SysTick_Handler"**
- Ensure legacy `systick.c` excludes handler when Zephyr enabled
- Or exclude entire `systick.c` in Makefile ZEPHYR block

### 9.2 Runtime Issues

**Symptom: Firmware doesn't boot**
- Check if `z_cstart()` is called from startup code
- Verify `micropython_main_thread_entry` is defined
- Enable early debug output via UART

**Symptom: REPL unresponsive**
- Check `MICROPY_EVENT_POLL_HOOK` uses `k_yield()` not `k_sleep()`
- Verify SysTick interrupt is enabled
- Test with simple `print("hello")` command

**Symptom: Threads don't start**
- Verify `mp_thread_init()` is called after kernel initialization
- Check thread stack size is sufficient (8KB recommended)
- Enable Zephyr debug output (if available)

**Symptom: FPU context corruption**
- Verify `CONFIG_FPU=1` and `CONFIG_FPU_SHARING=1`
- Check FPCCR configuration in `mp_zephyr_arch_init()`
- Run FPU test to identify which thread corrupts state

**Symptom: Mutex deadlock**
- Enable Zephyr debugging: `CONFIG_OBJECT_TRACING=1`
- Use `k_thread_dump()` to inspect thread states (if implemented)
- Check for missing `mp_thread_mutex_unlock()` calls

### 9.3 GDB Debugging

**Attach GDB**:
```bash
# Terminal 1: Start OpenOCD
openocd -f interface/stlink.cfg -f target/stm32wbx.cfg

# Terminal 2: Start GDB
arm-none-eabi-gdb build-NUCLEO_WB55-ZEPHYR/firmware.elf
(gdb) target remote :3333
(gdb) monitor reset halt
(gdb) load
(gdb) break micropython_main_thread_entry
(gdb) continue
```

**Useful Breakpoints**:
- `SysTick_Handler`: Check timer is running
- `PendSV_Handler`: Check context switches
- `z_arm_pendsv`: Zephyr context switch entry
- `mp_thread_create_ex`: Thread creation
- `k_mutex_lock`: Mutex operations

**Inspect Thread State**:
```gdb
(gdb) print _kernel.cpus[0].current
(gdb) print _kernel.ready_q.cache
(gdb) print ((struct k_thread*)_kernel.cpus[0].current)->base.prio
```

---

## 10. Next Steps

### Immediate Actions

1. **Create this document**: Save as `docs/STM32_ZEPHYR_THREADING_MIGRATION.md`
2. **Clean up debug code**: Remove fprintf() from `extmod/modtime.c`
3. **Choose starting point**: Decide which phase to begin implementation

### Phase 1 Start (Recommended)

Begin with code refactoring to establish clean foundation:
- Move `zephyr_arch_cortex_m.c` to extmod
- Create architecture layer interface header
- Update QEMU to use refactored code
- Validate no regressions in QEMU

### Alternative: Jump to Phase 2

If confident in current QEMU code, jump directly to STM32 build integration:
- Create `mpconfigvariant_ZEPHYR.mk`
- Add Makefile Zephyr block
- Attempt initial build
- Debug build issues as they arise

### Long-Term Roadmap

1. **Complete STM32 migration** (this document)
2. **Validate on multiple boards**: PYBD_SF6, NUCLEO_F767ZI, etc.
3. **Port to other Cortex-M platforms**: nRF52, RP2040, ESP32-C3
4. **Consider deprecating legacy threading**: After Zephyr proven stable
5. **Upstream contribution**: Submit PR to MicroPython mainline

---

## Appendices

### A. Quick Reference: Key Files

**Extmod (Portable)**:
- `extmod/zephyr_kernel/zephyr_kernel.mk` - Build system integration
- `extmod/zephyr_kernel/kernel/mpthread_zephyr.c` - MicroPython threading layer
- `extmod/zephyr_kernel/arch/cortex_m/cortex_m_arch.c` - Cortex-M arch bridge

**STM32 Port**:
- `ports/stm32/Makefile` - Build system (add Zephyr block)
- `ports/stm32/mpconfigport.h` - Configuration (add Zephyr config)
- `ports/stm32/main.c` - Initialization (add z_cstart path)
- `ports/stm32/mpthreadport_zephyr.h` - Thread port layer (new)
- `ports/stm32/zephyr_stm32.c` - STM32-specific integration (new)

**Board Configuration**:
- `ports/stm32/boards/NUCLEO_WB55/mpconfigvariant_ZEPHYR.mk` - Enable Zephyr (new)
- `ports/stm32/boards/NUCLEO_WB55/mpconfigboard.h` - CPU frequency (existing)

### B. Key Configuration Macros

```c
MICROPY_ZEPHYR_THREADING        // Enable Zephyr threading (0 or 1)
MICROPY_HW_CPU_FREQ_HZ          // CPU frequency for SysTick
CONFIG_SYS_CLOCK_TICKS_PER_SEC  // Tick frequency (default 1000)
CONFIG_FPU                      // Enable FPU support
CONFIG_FPU_SHARING              // Enable lazy FPU context save
CONFIG_TIMESLICE_SIZE           // Timeslice in ticks
```

### C. Zephyr Threading API

**Thread Management**:
```c
k_tid_t k_thread_create(struct k_thread *new_thread, k_thread_stack_t *stack,
                        size_t stack_size, k_thread_entry_t entry,
                        void *p1, void *p2, void *p3,
                        int prio, uint32_t options, k_timeout_t delay);
void k_yield(void);
void k_sleep(k_timeout_t timeout);
```

**Synchronization**:
```c
void k_mutex_init(struct k_mutex *mutex);
int k_mutex_lock(struct k_mutex *mutex, k_timeout_t timeout);
int k_mutex_unlock(struct k_mutex *mutex);

void k_sem_init(struct k_sem *sem, unsigned int initial_count, unsigned int limit);
int k_sem_take(struct k_sem *sem, k_timeout_t timeout);
void k_sem_give(struct k_sem *sem);
```

**Timing**:
```c
k_timeout_t K_NO_WAIT;   // Don't wait
k_timeout_t K_FOREVER;   // Wait indefinitely
k_timeout_t K_MSEC(ms);  // Wait specified milliseconds
```

### D. Performance Expectations

**Context Switch Time** (Cortex-M4 @ 64MHz):
- Legacy STM32: ~50 CPU cycles (~0.8μs)
- Zephyr: ~100-200 CPU cycles (~1.5-3μs)

**Thread Creation**:
- Legacy STM32: ~5000 CPU cycles (~80μs)
- Zephyr: ~10000-15000 CPU cycles (~150-230μs)

**Memory Usage per Thread**:
- Legacy STM32: ~120 bytes (struct pyb_thread)
- Zephyr: ~200 bytes (struct k_thread)

**Code Size**:
- Legacy STM32: ~2KB (threading + port)
- Zephyr: ~17KB (kernel + threading + port)

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-10-27 | MicroPython AI Assistant | Initial document |

---

**End of Document**
