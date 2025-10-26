# MicroPython QEMU Port Threading Investigation Notes

## Problem Statement
The QEMU port with Zephyr threading enabled exhibits busy-polling behavior during stdin operations, preventing proper thread scheduling.

## Investigation History

### Initial Discovery (Commit 7584bfa256)
- **Issue**: mp_hal_stdin_rx_chr() busy-polled UART at 100% CPU
- **Location**: ports/qemu/mphalport.c:47 (in uart_rx_chr polling loop)
- **Symptom**: Thread tests hung with QEMU at 100% CPU
- **Debug Method**: GDB backtrace showed code stuck in polling loop

### Attempted Fix #1: Direct k_sleep() in stdin polling
- **Implementation**: Added k_sleep(K_MSEC(1)) to mphalport.c:57 polling loop
- **Result**: **FAILED** - All tests timeout (threading AND non-threading)
- **Root Cause**: 1ms sleep makes REPL too slow for PTY communication
- **Test Results**:
  - thread/thread_create_basic.py: Timeout
  - basics/int_big_cmp.py: Timeout

### Attempted Fix #2: MICROPY_EVENT_POLL_HOOK Pattern
- **Rationale**: Other ports (STM32) use MICROPY_EVENT_POLL_HOOK for thread-aware polling
- **Implementation**:
  - Added MICROPY_EVENT_POLL_HOOK macro to ports/qemu/mpconfigport.h
  - Macro calls mp_handle_pending() + k_sleep(K_MSEC(1))
  - Modified mphalport.c to use MICROPY_EVENT_POLL_HOOK instead of direct k_sleep()
- **Result**: **FAILED** - Same timeout behavior as Fix #1
- **Conclusion**: QEMU port has different requirements than STM32 port

### Key Findings

1. **Architectural Difference**: The QEMU port did NOT use MICROPY_EVENT_POLL_HOOK before threading was added, indicating it may not be needed for this port

2. **The Dilemma**:
   - **Without k_sleep()**: Busy-polling at 100% CPU, prevents thread scheduling
   - **With k_sleep()**: REPL becomes non-responsive, all tests timeout

3. **Stdin Polling Latency**: The test framework communicates via PTY and cannot tolerate the 1ms latency introduced by k_sleep()

## Current Code State

### ports/qemu/mphalport.c (lines 31-59)
```c
#if MICROPY_PY_THREAD
#include "zephyr/kernel.h"
#endif

int mp_hal_stdin_rx_chr(void) {
    for (;;) {
        #if USE_UART
        int c = uart_rx_chr();
        if (c >= 0) {
            return c;
        }
        #endif
        #if USE_SEMIHOSTING
        char str[1];
        int ret = mp_semihosting_rx_chars(str, 1);
        if (ret == 0) {
            return str[0];
        }
        #endif
        #if MICROPY_PY_THREAD
        // Sleep briefly to allow other threads to run while waiting for input
        k_sleep(K_MSEC(1));
        #endif
    }
}
```

### ports/qemu/mpconfigport.h (lines 88-94)
```c
// Zephyr threading configuration
#if MICROPY_ZEPHYR_THREADING
#define MICROPY_PY_THREAD 1
#define MICROPY_PY_THREAD_GIL 1
#define MICROPY_PY_THREAD_GIL_VM_DIVISOR 32
#define MICROPY_ENABLE_FINALISER 1  // Enable __del__ method for GC
#endif
```

## Next Steps / Potential Solutions

1. **Event-driven UART**: Modify uart.c to use interrupt/event-driven approach instead of polling
2. **Conditional sleep**: Only sleep when no data available AND no other threads are runnable
3. **Shorter sleep**: Try microsecond-level sleeps (k_usleep) if available
4. **Zephyr k_yield()**: Use k_yield() instead of k_sleep() to yield without delay
5. **Selective threading**: Only enable threading for specific test scenarios
6. **QEMU-specific solution**: Leverage QEMU's specific capabilities for better stdin handling

## Test Files
- tests/thread/thread_create_basic.py: Basic thread creation test
- tests/basics/int_big_cmp.py: Non-threading test used to verify REPL functionality

## Debugging Commands
```bash
# Build and test
cd ports/qemu
make BOARD=MPS2_AN386 clean
make BOARD=MPS2_AN386
make BOARD=MPS2_AN386 test RUN_TESTS_EXTRA="thread/thread_create_basic.py"

# GDB debugging
qemu-system-arm -M mps2-an386 -nographic -monitor null -semihosting \
  -kernel build-MPS2_AN386/firmware.elf -S -s &
arm-none-eabi-gdb build-MPS2_AN386/firmware.elf \
  -ex "target remote localhost:1234" \
  -ex "continue"
```
