# FreeRTOS BLE Architecture for RP2 Pico W

## Problem Statement

The Zephyr BLE stack expects a multi-threaded RTOS with blocking semaphore waits.
MicroPython on RP2 uses FreeRTOS but the Zephyr BLE integration was designed for
a cooperative polling model. This creates a fundamental mismatch.

## Current Architecture (Broken)

```
┌─────────────────────────────────────────────────────────────────┐
│ Main Task (Python REPL)                                          │
│                                                                  │
│  ble.active(True)                                                │
│       │                                                          │
│       ▼                                                          │
│  bt_enable() → k_sem_take(&ncmd_sem)                            │
│       │                                                          │
│       ▼                                                          │
│  xSemaphoreTake(10ms timeout)  ◄──── blocks main task           │
│       │                                                          │
│       ├──► timeout, call mp_bluetooth_zephyr_hci_uart_wfi()     │
│       │         │                                                │
│       │         ▼                                                │
│       │    mp_bluetooth_zephyr_poll_uart()                      │
│       │         │                                                │
│       │         ▼                                                │
│       │    cyw43_bluetooth_hci_read() ◄── reads SPI directly    │
│       │         │                                                │
│       │         ▼                                                │
│       │    recv_cb() → signals semaphore                        │
│       │         │                                                │
│       └─────────┘  (loop until semaphore acquired)              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Problem**: This polling approach works but:
1. Adds latency (10ms poll intervals)
2. Busy-waits the main task
3. Doesn't leverage FreeRTOS task scheduling
4. Can cause timing issues with other operations

## Proper FreeRTOS Architecture

With FreeRTOS, we should use **true task separation**:

```
┌──────────────────────────────────────────────────────────────────┐
│                     FreeRTOS Scheduler                            │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ HCI RX Task (Priority: configMAX_PRIORITIES - 1)            │ │
│  │                                                              │ │
│  │  while (running) {                                           │ │
│  │      // Wait for CYW43 data available (IRQ or poll)         │ │
│  │      cyw43_bluetooth_hci_read()                              │ │
│  │      if (data) {                                             │ │
│  │          recv_cb(buf)  → signals waiting semaphores          │ │
│  │      }                                                       │ │
│  │      vTaskDelay(1)  // yield to other tasks                  │ │
│  │  }                                                           │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                              │                                    │
│                              │ signals semaphore                  │
│                              ▼                                    │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ Work Queue Task (Priority: configMAX_PRIORITIES - 2)        │ │
│  │                                                              │ │
│  │  while (running) {                                           │ │
│  │      xSemaphoreTake(work_sem, portMAX_DELAY)                │ │
│  │      process_all_work_items()                                │ │
│  │  }                                                           │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ Main Task (Priority: 1) - Python REPL                       │ │
│  │                                                              │ │
│  │  ble.active(True)                                            │ │
│  │      │                                                       │ │
│  │      ▼                                                       │ │
│  │  bt_enable() → k_sem_take()                                  │ │
│  │      │                                                       │ │
│  │      ▼                                                       │ │
│  │  xSemaphoreTake(portMAX_DELAY)  ◄── blocks, yields CPU      │ │
│  │      │                                                       │ │
│  │      │  (HCI RX task runs, processes response,               │ │
│  │      │   calls recv_cb which signals this semaphore)         │ │
│  │      │                                                       │ │
│  │      ▼                                                       │ │
│  │  semaphore signaled, task resumes                            │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

## Key Differences

| Aspect | Polling Model | FreeRTOS Model |
|--------|--------------|----------------|
| HCI RX | Called from k_sem_take() poll loop | Dedicated high-priority task |
| Blocking | Busy-wait with timeouts | True FreeRTOS blocking |
| Latency | 10ms poll interval | Immediate (task switch) |
| CPU usage | Higher (polling) | Lower (event-driven) |
| Complexity | Simpler, but fragile | More robust |

## Implementation Plan

### Phase 6: Create HCI RX Task

Location: `ports/rp2/mpzephyrport_rp2.c`

```c
// HCI RX task - runs continuously, processes incoming HCI data
static TaskHandle_t hci_rx_task_handle = NULL;
static StaticTask_t hci_rx_task_tcb;
static StackType_t hci_rx_task_stack[1024];  // 4KB stack

static void hci_rx_task(void *arg) {
    (void)arg;

    while (hci_rx_task_running) {
        if (recv_cb != NULL) {
            uint32_t len = 0;
            int ret = cyw43_bluetooth_hci_read(hci_rx_buffer, sizeof(hci_rx_buffer), &len);

            if (ret == 0 && len > CYW43_HCI_HEADER_SIZE) {
                // Process HCI packet and call recv_cb
                // This signals semaphores that waiting tasks block on
                process_hci_packet(len);
            }
        }

        // Small delay to prevent tight loop, allow other tasks
        vTaskDelay(pdMS_TO_TICKS(1));
    }

    vTaskDelete(NULL);
}

void mp_bluetooth_zephyr_hci_rx_task_start(void) {
    hci_rx_task_running = true;
    hci_rx_task_handle = xTaskCreateStatic(
        hci_rx_task,
        "hci_rx",
        sizeof(hci_rx_task_stack) / sizeof(StackType_t),
        NULL,
        configMAX_PRIORITIES - 1,  // Highest priority
        hci_rx_task_stack,
        &hci_rx_task_tcb
    );
}
```

### Semaphore Changes

With HCI RX in its own task, `k_sem_take()` can use **true blocking**:

```c
int k_sem_take(struct k_sem *sem, k_timeout_t timeout) {
    TickType_t ticks;
    if (timeout.ticks == 0) {
        ticks = 0;
    } else if (timeout.ticks == 0xFFFFFFFF) {
        ticks = portMAX_DELAY;
    } else {
        ticks = pdMS_TO_TICKS(timeout.ticks);
    }

    // Can truly block - HCI RX task will signal when response arrives
    BaseType_t result = xSemaphoreTake(sem->handle, ticks);
    return (result == pdTRUE) ? 0 : -EAGAIN;
}
```

### Task Lifecycle

```
BLE Activation:
1. mp_bluetooth_init()
2. mp_bluetooth_zephyr_hci_rx_task_start()   ← Start HCI RX task
3. bt_enable()  → can now truly block
4. mp_bluetooth_zephyr_work_thread_start()   ← Start work thread

BLE Deactivation:
1. mp_bluetooth_deinit()
2. mp_bluetooth_zephyr_work_thread_stop()
3. mp_bluetooth_zephyr_hci_rx_task_stop()
4. Cleanup
```

## CYW43 Considerations

The CYW43 driver has some constraints:
- SPI bus is shared with WiFi
- `cyw43_bluetooth_hci_read()` is non-blocking
- No interrupt-driven notification of HCI data availability

For optimal performance, could investigate:
- Using CYW43 HOST_WAKE GPIO for interrupt-driven wake
- Service task integration for ISR-to-task bridging

## Memory Requirements

| Component | Stack Size | Static Allocation |
|-----------|-----------|-------------------|
| HCI RX Task | 4KB | Yes (xTaskCreateStatic) |
| Work Queue Task | 4KB | Yes |
| Semaphore storage | ~80 bytes each | Yes (StaticSemaphore_t) |

Total additional RAM: ~8-10KB

## Testing Checklist

- [ ] BLE activation without timeout
- [ ] BLE scanning finds devices
- [ ] Connection establishment
- [ ] Connection callbacks invoked
- [ ] Clean deactivation
- [ ] No memory leaks
- [ ] Stress test: repeated activate/deactivate cycles
