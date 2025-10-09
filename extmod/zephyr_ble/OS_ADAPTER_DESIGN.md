# Zephyr BLE OS Adapter Layer Design

## Overview
Based on analysis of NimBLE and BTstack integration patterns, this document defines the OS adapter layer architecture for isolating Zephyr BLE from the Zephyr kernel.

## MicroPython BLE Stack Integration Pattern

### Common Integration Points

All BLE stacks follow this pattern:

1. **Polling Function**: `mp_bluetooth_hci_poll()` - port-specific, called periodically
2. **Scheduler Integration**: Called via `mp_sched_schedule()` (sync mode) or PendSV (async mode)
3. **Timer Scheduling**: `mp_bluetooth_hci_poll_in_ms()` - reschedule via soft_timer
4. **Event Processing**: Process HCI UART data, timers, and event queues

### NimBLE Pattern (extmod/nimble/nimble/nimble_npl_os.c)

**Event Queue System:**
```c
// Global linked list of event queues
struct ble_npl_eventq *global_eventq = NULL;

// Called by mp_bluetooth_hci_poll()
void mp_bluetooth_nimble_os_eventq_run_all(void) {
    while (true) {
        // Dequeue event from any queue
        struct ble_npl_event *ev = dequeue_from_global_eventq();
        if (!ev) break;

        // Execute event handler
        ev->fn(ev);
    }
}
```

**Callout (Timer) System:**
```c
// Global linked list of timers
static struct ble_npl_callout *global_callout = NULL;

// Called by mp_bluetooth_hci_poll()
void mp_bluetooth_nimble_os_callout_process(void) {
    uint32_t tnow = mp_hal_ticks_ms();
    for (callout in global_callout) {
        if (callout->active && tnow >= callout->ticks) {
            callout->active = false;
            if (callout->evq) {
                // Enqueue to event queue
                ble_npl_eventq_put(callout->evq, &callout->ev);
            } else {
                // Execute directly
                callout->ev.fn(&callout->ev);
            }
        }
    }
}
```

**Semaphore Pattern (busy-wait with HCI processing):**
```c
ble_npl_error_t ble_npl_sem_pend(struct ble_npl_sem *sem, ble_npl_time_t timeout) {
    if (sem->count == 0) {
        uint32_t t0 = mp_hal_ticks_ms();
        // Busy-wait but process HCI UART
        while (sem->count == 0 && mp_hal_ticks_ms() - t0 < timeout) {
            mp_bluetooth_nimble_hci_uart_wfi(); // Process UART, no events
        }
        if (sem->count == 0) return BLE_NPL_TIMEOUT;
    }
    sem->count -= 1;
    return BLE_NPL_OK;
}
```

**Mutex Pattern (no-op, all code runs in scheduler):**
```c
ble_npl_error_t ble_npl_mutex_pend(struct ble_npl_mutex *mu, ble_npl_time_t timeout) {
    // No-op: All NimBLE code executed by scheduler, implicitly mutexed
    ++mu->locked;
    return BLE_NPL_OK;
}
```

**Critical Sections:**
```c
uint32_t ble_npl_hw_enter_critical(void) {
    MICROPY_PY_BLUETOOTH_ENTER  // Port-defined atomic section
    return atomic_state;
}
```

### BTstack Pattern (ports/stm32/mpbtstackport.c)

**Timer Runloop:**
```c
static btstack_linked_list_t mp_btstack_runloop_timers;

// Called by mp_bluetooth_hci_poll()
void mp_bluetooth_hci_poll(void) {
    // Process UART
    mp_bluetooth_btstack_hci_uart_process();

    // Process timers
    while (mp_btstack_runloop_timers != NULL) {
        btstack_timer_source_t *tim = (btstack_timer_source_t *)mp_btstack_runloop_timers;
        int32_t delta_ms = tim->timeout - mp_hal_ticks_ms();
        if (delta_ms > 0) {
            mp_bluetooth_hci_poll_in_ms(delta_ms); // Reschedule
            break;
        }
        btstack_linked_list_pop(&mp_btstack_runloop_timers);
        tim->process(tim); // Execute timer callback
    }
}
```

### Port Integration (ports/stm32/mpbthciport.c)

**Scheduler Integration (Sync Mode):**
```c
static mp_sched_node_t mp_bluetooth_hci_sched_node;

static void run_events_scheduled_task(mp_sched_node_t *node) {
    mp_bluetooth_hci_poll(); // Process UART + timers + events
}

void mp_bluetooth_hci_poll_now_default(void) {
    mp_sched_schedule_node(&mp_bluetooth_hci_sched_node, run_events_scheduled_task);
}
```

**Soft Timer for Periodic Polling:**
```c
static soft_timer_entry_t mp_bluetooth_hci_soft_timer;

static void mp_bluetooth_hci_soft_timer_callback(soft_timer_entry_t *self) {
    mp_bluetooth_hci_poll_now(); // Schedule via mp_sched_schedule()
}

void mp_bluetooth_hci_poll_in_ms_default(uint32_t ms) {
    soft_timer_reinsert(&mp_bluetooth_hci_soft_timer, ms);
}
```

**UART RX IRQ Trigger:**
```c
mp_obj_t mp_uart_interrupt(mp_obj_t self_in) {
    mp_bluetooth_hci_poll_now(); // Schedule processing ASAP
    return mp_const_none;
}
```

## Zephyr Kernel to MicroPython Mapping

### 1. k_work (Work Queues) → Event Queue + Scheduler

**Zephyr Pattern:**
```c
// Dedicated kernel thread running work queue
k_work_queue_start(&bt_workq, rx_thread_stack, ...);
k_work_submit_to_queue(&bt_workq, &rx_work);
```

**MicroPython Abstraction:**
```c
struct k_work {
    k_work_handler_t handler;
    void *user_data;
    bool pending;
    struct k_work *next;
};

struct k_work_q {
    struct k_work *head;
    struct k_work_q *nextq;
};

// Global work queue list (like NimBLE event queues)
static struct k_work_q *global_work_q = NULL;

int k_work_submit_to_queue(struct k_work_q *queue, struct k_work *work) {
    // Add to queue's linked list
    enqueue(queue, work);
    work->pending = true;

    // NOT needed: MicroPython doesn't immediately schedule
    // Work is processed in mp_bluetooth_zephyr_poll()
    return 0;
}

// Called by mp_bluetooth_hci_poll()
void mp_bluetooth_zephyr_work_process(void) {
    for (struct k_work_q *q = global_work_q; q != NULL; q = q->nextq) {
        struct k_work *work = dequeue(q);
        if (work) {
            work->pending = false;
            work->handler(work); // Execute work handler
        }
    }
}
```

**k_work_delayable (Delayed Work):**
Map to timer + work submission:
```c
struct k_work_delayable {
    struct k_work work;
    struct k_timer timer; // Uses our existing k_timer abstraction
    uint32_t delay_ms;
};

int k_work_schedule(struct k_work_delayable *dwork, k_timeout_t delay) {
    // Start timer that will submit work when expired
    dwork->delay_ms = k_timeout_to_ms(delay);
    k_timer_start(&dwork->timer, dwork->delay_ms, 0);
    return 0;
}

// Timer callback submits work
static void delayed_work_timer_fn(struct k_timer *timer) {
    struct k_work_delayable *dwork = CONTAINER_OF(timer, struct k_work_delayable, timer);
    k_work_submit(&dwork->work);
}
```

### 2. k_sem (Semaphores) → Atomic Counter + Busy-Wait

**Zephyr Pattern:**
```c
k_sem_init(&sync_sem, 0, 1);
k_sem_take(&sync_sem, HCI_CMD_TIMEOUT); // BLOCKS
```

**MicroPython Abstraction (NimBLE-style):**
```c
struct k_sem {
    volatile uint16_t count;
};

int k_sem_take(struct k_sem *sem, k_timeout_t timeout) {
    if (sem->count == 0) {
        uint32_t t0 = mp_hal_ticks_ms();
        uint32_t timeout_ms = k_timeout_to_ms(timeout);

        // Busy-wait, but process work queues during wait
        while (sem->count == 0 && (mp_hal_ticks_ms() - t0) < timeout_ms) {
            // Process pending work (keeps BLE stack responsive)
            mp_bluetooth_zephyr_work_process();

            // Process HCI UART
            mp_bluetooth_zephyr_hci_uart_wfi();

            // Yield to prevent tight loop
            MICROPY_EVENT_POLL_HOOK;
        }

        if (sem->count == 0) {
            return -EAGAIN; // Timeout
        }
    }
    sem->count--;
    return 0;
}

void k_sem_give(struct k_sem *sem) {
    sem->count++;
}
```

### 3. k_mutex → No-Op (like NimBLE)

All Zephyr BLE code will run in scheduler context, so mutexes are implicitly satisfied.

```c
struct k_mutex {
    volatile uint8_t locked;
};

int k_mutex_lock(struct k_mutex *mutex, k_timeout_t timeout) {
    // No-op, all code runs in scheduler
    mutex->locked++;
    return 0;
}

void k_mutex_unlock(struct k_mutex *mutex) {
    assert(mutex->locked > 0);
    mutex->locked--;
}
```

### 4. Critical Sections → MICROPY_PY_BLUETOOTH_ENTER/EXIT

```c
typedef uint32_t k_spinlock_key_t;

static inline k_spinlock_key_t k_spin_lock(struct k_spinlock *lock) {
    (void)lock;
    MICROPY_PY_BLUETOOTH_ENTER
    return atomic_state;
}

static inline void k_spin_unlock(struct k_spinlock *lock, k_spinlock_key_t key) {
    (void)lock;
    uint32_t atomic_state = key;
    MICROPY_PY_BLUETOOTH_EXIT
}
```

### 5. k_sleep / k_yield → MICROPY_EVENT_POLL_HOOK

```c
void k_sleep(k_timeout_t timeout) {
    uint32_t ms = k_timeout_to_ms(timeout);
    mp_hal_delay_ms(ms);
}

void k_yield(void) {
    #ifdef MICROPY_EVENT_POLL_HOOK
    MICROPY_EVENT_POLL_HOOK
    #endif
}
```

## Integration Architecture

### File Structure
```
extmod/zephyr_ble/
├── modbluetooth_zephyr.c      # MicroPython bindings
├── zephyr_ble.mk              # Makefile
├── zephyr_ble.cmake           # CMake
└── hal/
    ├── zephyr_ble_timer.{h,c}      # k_timer (done)
    ├── zephyr_ble_work.{h,c}       # k_work, k_work_q
    ├── zephyr_ble_sem.{h,c}        # k_sem
    ├── zephyr_ble_mutex.{h,c}      # k_mutex (trivial)
    ├── zephyr_ble_atomic.{h,c}     # k_spinlock, atomic ops
    ├── zephyr_ble_kernel.{h,c}     # k_sleep, k_yield, misc
    └── zephyr_ble_poll.{h,c}       # Main polling function
```

### Port Integration (ports/*/mpzephyrbleport.c)

```c
void mp_bluetooth_hci_poll(void) {
    if (mp_bluetooth_zephyr_ble_state != ACTIVE) {
        return;
    }

    // 1. Process timers (k_timer, k_work_delayable)
    mp_bluetooth_zephyr_timer_process();

    // 2. Process work queues (k_work)
    mp_bluetooth_zephyr_work_process();

    // 3. Process HCI UART
    mp_bluetooth_zephyr_hci_uart_process();

    // 4. Reschedule
    mp_bluetooth_hci_poll_in_ms(128);
}
```

## Key Design Principles

1. **No Threads**: All code runs in MicroPython scheduler context
2. **Polling-Based**: Work queues and timers processed by periodic poll
3. **Busy-Wait Semaphores**: k_sem_take busy-waits while processing work
4. **No-Op Mutexes**: Scheduler provides implicit mutual exclusion
5. **Event-Driven**: UART RX IRQ triggers immediate scheduling
6. **Atomic Sections**: Use port-defined MICROPY_PY_BLUETOOTH_ENTER/EXIT

## Benefits of This Approach

- ✅ No Zephyr kernel threads required
- ✅ Proven pattern (NimBLE, BTstack)
- ✅ No patching of Zephyr BLE source code
- ✅ Works on all MicroPython ports
- ✅ Compatible with existing modbluetooth.c interface

## Implementation Order

1. ✅ k_timer (done)
2. k_work / k_work_q / k_work_delayable
3. k_sem
4. k_mutex (trivial)
5. k_spinlock / atomic ops
6. k_sleep / k_yield / misc
7. Main polling function (mp_bluetooth_zephyr_poll)
8. Port integration (mpzephyrbleport.c)
