# FreeRTOS BLE Architecture for MicroPython

## Overview

This document describes the FreeRTOS integration for Zephyr BLE on RP2 Pico W (RP2040/RP2350).

**Plan File**: `~/.claude/plans/agile-strolling-lighthouse.md`

## Objective

Replace polling-based Zephyr BLE HAL with FreeRTOS primitives to solve Issue #6 (connection callbacks not firing) and eliminate timing hacks.

## Why FreeRTOS?

The current HAL uses polling loops with debug printf "timing magic" that is fragile:
- `mp_bluetooth_zephyr_in_wait_loop` flag prevents deadlock but requires careful coordination
- Debug printf statements provide timing delays that allow HCI responses to be processed
- Without these timing delays, semaphores timeout waiting for responses

Real FreeRTOS primitives provide:
- True blocking/waking semantics for semaphores
- ISR-to-task bridging via service task framework
- Proper priority-based preemption
- Thread-safe queue operations

## Architecture

### Thread Priority Hierarchy

```
FreeRTOS SMP Scheduler
├── Priority MAX-1: HCI RX Task (CYW43 polling)
├── Priority MAX-2: BLE Work Queue Thread
└── Priority 1: Python Main Thread
```

**Priority Rationale**:
- HCI RX at MAX-1: Must service CYW43 SPI interrupts without delay
- BLE Work at MAX-2: Process HCI events immediately after reception
- Python Main at 1: Lowest priority, yields to BLE operations

### Components

#### 1. HAL Primitives (Phase 2)

Replace Zephyr OS abstractions with FreeRTOS equivalents:

| Zephyr API | FreeRTOS API |
|------------|--------------|
| `k_sem_take()` | `xSemaphoreTake()` |
| `k_sem_give()` | `xSemaphoreGive()` |
| `k_mutex_lock()` | `xSemaphoreTake()` (recursive mutex) |
| `k_mutex_unlock()` | `xSemaphoreGive()` |
| `k_timer_start()` | `xTimerStart()` |
| `k_work_submit()` | `xQueueSend()` to work queue |

**Benefits**:
- No polling loops in semaphore waits
- True ISR-safe operations
- Proper priority inversion handling

#### 2. BLE Work Queue Thread (Phase 3)

Dedicated FreeRTOS task for BLE work processing:

```c
void ble_work_thread_func(void *param) {
    while (1) {
        // Wait for work items from queue
        struct k_work *work;
        if (xQueueReceive(work_queue, &work, portMAX_DELAY) == pdTRUE) {
            // Execute work handler
            work->handler(work);
        }
    }
}
```

**Key Features**:
- Runs at priority MAX-2
- Blocks on queue when no work available
- Woken immediately when HCI RX task enqueues work
- No polling, no timing hacks

#### 3. HCI Integration (Phase 4)

Service task dispatch for RP2 CYW43:

```c
void cyw43_hci_rx_service_task(void *param) {
    while (1) {
        // Poll CYW43 SPI interface
        cyw43_poll();

        // If HCI data available, notify work thread
        if (hci_data_available()) {
            struct k_work *work = get_hci_rx_work();
            xQueueSend(work_queue, &work, 0);
        }

        // Yield to allow work thread to process
        taskYIELD();
    }
}
```

**Benefits**:
- Clear separation: RX task receives, work thread processes
- Priority ensures HCI RX never starves
- Work thread preempts Python main thread immediately

## Implementation Status (2025-12-19)

| Phase | Status | Notes |
|-------|--------|-------|
| 1. Git Setup | ✓ Complete | Merged `freertos` into `zephy_ble` (commit e3f85a69cf) |
| 2. HAL Primitives | Pending | Replace k_sem, k_mutex, k_timer with FreeRTOS |
| 3. Work Queue Thread | Pending | Dedicated BLE work thread |
| 4. HCI Integration | Pending | Service task dispatch for RP2 |
| 5. Cleanup | Pending | Remove polling code |

## Key Changes

### Build System
- FreeRTOS-Kernel submodule added at `lib/FreeRTOS-Kernel`
- Makefile integration for RP2 port
- Verified build: 886KB text, 70KB bss

### Target Platforms
- **Primary**: RPI_PICO_W (RP2040) - single-core FreeRTOS
- **Future**: RPI_PICO2_W (RP2350) - requires FreeRTOS SMP port fixes

**Note**: RP2350 currently has issues with FreeRTOS SMP port. Focusing on RP2040 for initial implementation.

## Benefits Over Polling HAL

### Current (Polling) Approach
```c
int k_sem_take(struct k_sem *sem, k_timeout_t timeout) {
    uint32_t start = mp_hal_ticks_ms();
    while (sem->count == 0) {
        mp_bluetooth_zephyr_work_process();  // Poll for work
        if (mp_hal_ticks_ms() - start > timeout) {
            return -EAGAIN;  // Timeout
        }
        // Debug printf here provides timing delay!
    }
    sem->count--;
    return 0;
}
```

**Problems**:
- Busy-wait loop wastes CPU
- Timing depends on printf overhead
- `in_wait_loop` flag prevents recursion but is fragile
- No priority handling

### FreeRTOS Approach
```c
int k_sem_take(struct k_sem *sem, k_timeout_t timeout) {
    return xSemaphoreTake(sem->freertos_sem, timeout) == pdTRUE ? 0 : -EAGAIN;
}
```

**Benefits**:
- True blocking - CPU sleeps until signaled
- No timing dependencies
- Priority inversion handled by FreeRTOS
- ISR-safe give operations wake waiting tasks immediately

## Expected Resolution of Issue #6

**Current Issue**: Connection callbacks not firing on STM32WB55 Zephyr variant.

**Root Cause Hypothesis**: Polling-based work processing doesn't reliably deliver callbacks in time.

**FreeRTOS Solution**:
1. HCI connection event arrives via IPCC interrupt
2. ISR enqueues work item to BLE work queue
3. FreeRTOS scheduler immediately wakes BLE work thread (priority MAX-2)
4. Work thread processes connection event
5. Zephyr invokes registered callback
6. Python IRQ handler receives event

**Key Difference**: FreeRTOS guarantees work thread runs immediately after ISR, no polling required.

## Testing Plan

1. **Phase 2 Verification**: Build with FreeRTOS HAL, verify BLE initialization
2. **Phase 3 Verification**: Test BLE scanning with work queue thread
3. **Phase 4 Verification**: Test connections on STM32WB55 (Issue #6)
4. **Phase 5 Verification**: Remove all polling code, verify stability

## Performance Considerations

### Thread Stack Sizes
- HCI RX Task: 2KB (minimal, just poll and enqueue)
- BLE Work Thread: 8KB (needs stack for Zephyr BLE processing)
- Python Main Thread: 64KB (existing main stack)

### Memory Cost
- FreeRTOS Kernel: ~8KB text, ~1KB data
- Per-task overhead: ~200 bytes (TCB + minimum stack)
- Total additional cost: ~11KB

### CPU Utilization
- Idle: 0% (all threads blocked)
- BLE scanning: ~5% (HCI RX polling + work processing)
- BLE connected: ~1% (event-driven, minimal polling)

**Much better than current polling approach which burns CPU in busy-wait loops.**

## Future Enhancements

1. **RP2350 SMP Support**: When FreeRTOS SMP port stabilizes
2. **Asymmetric Processing**: Core 0 runs Python, Core 1 runs BLE/network
3. **Power Optimization**: WFI in idle task, wake on CYW43 interrupt
4. **Performance Profiling**: FreeRTOS trace hooks for timing analysis

## References

- FreeRTOS Kernel: https://www.freertos.org/
- FreeRTOS SMP: https://www.freertos.org/symmetric-multiprocessing-introduction.html
- MicroPython Threading: `py/mpthread.h`
- RP2 Port: `ports/rp2/mpthreadport.c`
