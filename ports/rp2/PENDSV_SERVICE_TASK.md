# PendSV Service Task Architecture

This document describes the background dispatch mechanism used by the RP2 port
when FreeRTOS threading is enabled.

## Background

MicroPython's RP2 port uses a background dispatch system for:
- Soft timer callbacks
- CYW43 WiFi chip polling
- LWIP network stack processing
- Wiznet ethernet polling

Originally, this used the ARM Cortex-M PendSV interrupt at lowest priority.
However, FreeRTOS also uses PendSV for context switching, creating a conflict
when threading is enabled.

## Solution: Service Task

Instead of sharing PendSV between MicroPython dispatch and FreeRTOS context
switching, we use a dedicated FreeRTOS task for background dispatch:

```
┌─────────────────────────────────────────────────────────────┐
│                    ISR (timer, GPIO, etc.)                  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
              pendsv_schedule_dispatch(slot, callback)
                              │
                              ▼
              ┌───────────────────────────────────┐
              │  vTaskNotifyGiveFromISR()         │
              │  (wake service task)              │
              └───────────────────────────────────┘
                              │
                              ▼
              ┌───────────────────────────────────┐
              │  Service Task                     │
              │  Priority: configMAX_PRIORITIES-1│
              │  Stack: 512 bytes                 │
              └───────────────────────────────────┘
                              │
                              ▼
              ┌───────────────────────────────────┐
              │  Process dispatch table           │
              │  (soft_timer, cyw43, lwip, etc.)  │
              └───────────────────────────────────┘
```

## API Compatibility

The public API remains unchanged:

- `pendsv_init()` - Initialize (creates service task when threading enabled)
- `pendsv_schedule_dispatch(slot, callback)` - Schedule background work
- `pendsv_suspend()` / `pendsv_resume()` - Critical section protection
- `pendsv_is_pending(slot)` - Check if work is pending

Callers (soft_timer.c, mpnetworkport.c, etc.) require no changes.

## Implementation Details

### Service Task

```c
static void pendsv_service_task(void *arg) {
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);  // Block until signaled

        if (mp_thread_recursive_mutex_lock(&pendsv_mutex, 0)) {
            for (size_t i = 0; i < PENDSV_DISPATCH_NUM_SLOTS; ++i) {
                if (pendsv_dispatch_table[i] != NULL) {
                    pendsv_dispatch_t f = pendsv_dispatch_table[i];
                    pendsv_dispatch_table[i] = NULL;
                    f();
                }
            }
            mp_thread_recursive_mutex_unlock(&pendsv_mutex);
        }
    }
}
```

### ISR Context Detection

The dispatch function detects ISR context using the ARM IPSR register:

```c
static inline bool pendsv_in_isr(void) {
    uint32_t ipsr;
    __asm volatile ("mrs %0, ipsr" : "=r" (ipsr));
    return ipsr != 0;
}
```

This allows correct API usage (`vTaskNotifyGiveFromISR` vs `xTaskNotifyGive`).

### Suspend/Resume

The existing recursive mutex mechanism is preserved. When `pendsv_suspend()`
is called, the service task will fail to acquire the mutex and skip processing.
When `pendsv_resume()` is called, it notifies the service task to retry.

## Timing Characteristics

| Mechanism | Latency | Notes |
|-----------|---------|-------|
| PendSV interrupt | ~1-5 µs | Runs before returning to thread context |
| Service task | ~5-20 µs | Requires FreeRTOS context switch |

The additional latency is negligible for the use cases (1ms soft timer
resolution, network polling).

## Non-Threaded Builds

When `MICROPY_PY_THREAD=0`, the original PendSV interrupt mechanism is used.
FreeRTOS is not linked, so there is no conflict.

## References

- FreeRTOS Task Notifications: https://www.freertos.org/RTOS-task-notifications.html
- ARM Cortex-M PendSV: ARM Architecture Reference Manual
