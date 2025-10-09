# Zephyr BLE Stack Dependency Analysis

## Summary
The Zephyr BLE host stack has **deep integration** with the Zephyr kernel. Isolation without the kernel will require significant abstraction.

## Zephyr Kernel Primitives Found in BLE Host

### 1. Work Queues (k_work) - **CRITICAL DEPENDENCY**
- **Usage count**: ~161 occurrences
- **Key files**: `hci_core.c`, `long_wq.c`, `conn.c`, `gatt.c`
- **Purpose**:
  - Dedicated work queue threads (`bt_workq`, `bt_long_wq`)
  - Deferred work execution (`k_work_delayable`)
  - RX packet processing (`rx_work_handler`)
- **Complexity**: **HIGH** - Requires thread management and scheduling

### 2. Semaphores (k_sem) - **CRITICAL DEPENDENCY**
- **Usage count**: ~30 occurrences
- **Key files**: `hci_core.c`, `conn.c`
- **Purpose**:
  - HCI command synchronization (`sync_sem` in `bt_hci_cmd_send_sync`)
  - Flow control (`ncmd_sem` - number of command buffers available)
  - ACL/ISO packet counting (`acl_pkts`, `iso_pkts`)
- **Complexity**: **MEDIUM** - Can be mapped to atomic counters + busy-wait or event loops

### 3. Mutexes (k_mutex) - **MEDIUM DEPENDENCY**
- **Usage count**: ~15 occurrences
- **Purpose**: Protecting critical sections
- **Complexity**: **LOW** - Can use atomic sections like NimBLE

### 4. Work Queue Infrastructure
- **Dedicated threads**:
  - `rx_thread_stack` (CONFIG_BT_RX_STACK_SIZE)
  - `bt_lw_stack_area` (CONFIG_BT_LONG_WQ_STACK_SIZE)
- **Thread creation**: `k_work_queue_start()` spawns kernel threads
- **Complexity**: **VERY HIGH** - MicroPython doesn't have kernel threads

### 5. Other Kernel APIs
- `k_sleep()`: Blocking delays
- `k_yield()`: Thread yielding (already abstracted)
- `k_current_get()`: Current thread identification
- `k_mem_slab_free()`: Memory management
- `k_poll_signal_raise()`: Polling mechanism

## Proposed MicroPython Abstractions

### Option 1: Event Loop + Scheduler (NimBLE pattern)
**Pros:**
- No threads needed
- Works with MicroPython scheduler
- Proven with NimBLE

**Cons:**
- Requires rewriting work queue logic
- Semaphore busy-waits may block scheduler
- May not handle all Zephyr BLE use cases

**Mapping:**
- `k_work` → `mp_sched_schedule()` + event queue
- `k_work_delayable` → callouts polled by scheduler
- `k_sem` → atomic counters + `MICROPY_EVENT_POLL_HOOK` busy-wait
- `k_mutex` → `MICROPY_PY_BLUETOOTH_ENTER/EXIT`

### Option 2: Zephyr Minimal Kernel (Not Preferred Per User)
Include minimal Zephyr kernel as library. User specified **not to pursue this**.

### Option 3: Hybrid Approach
- Abstract synchronous operations (semaphores, mutexes)
- Convert async work queue operations to MicroPython scheduler
- May require patching Zephyr BLE source

## Key Challenges

### 1. Work Queue Thread Model
Zephyr BLE expects:
```c
// Dedicated thread running work queue
k_work_queue_start(&bt_workq, rx_thread_stack, ...);

// Submit work from interrupt/callback
k_work_submit_to_queue(&bt_workq, &rx_work);
```

MicroPython has:
- Single-threaded event loop
- Scheduler for deferred execution (`mp_sched_schedule`)
- No kernel threads

**Solution**: Rewrite work submission to use `mp_sched_schedule`, poll work queues from scheduler.

### 2. Semaphore Synchronization
Zephyr BLE uses semaphores for synchronous HCI commands:
```c
k_sem_init(&sync_sem, 0, 1);
bt_hci_cmd_send(...);
k_sem_take(&sync_sem, HCI_CMD_TIMEOUT); // BLOCKS until response
```

MicroPython can't block the scheduler.

**Solution**: Convert to async with timeout polling + `MICROPY_EVENT_POLL_HOOK`.

### 3. Code Size
- Zephyr BLE host: ~26 C files + dependencies
- NimBLE: ~87 files (for comparison)
- BTstack: Comparable

Adding Zephyr abstractions will increase extmod size significantly.

## Recommendations for User Review

### Questions:
1. **Acceptable to rewrite/patch Zephyr BLE source?** The work queue model doesn't map 1:1 to MicroPython's scheduler.

2. **Which BLE features are priority?**
   - Basic GATT server/client (most common)
   - L2CAP channels
   - Pairing/bonding
   - ISO channels (LE Audio)

3. **Code size budget?** Zephyr BLE + abstractions may be larger than NimBLE.

4. **Maintenance commitment?** Keeping sync with upstream Zephyr will require ongoing work on abstraction layer.

### Proposed Path Forward:
1. Start with **minimal BLE host** (GATT only, no L2CAP/ISO/Classic)
2. Implement k_work → scheduler abstraction
3. Test on ports/zephyr first (where real kernel exists) to validate
4. Incrementally port abstractions to non-Zephyr ports
5. Add features as abstractions stabilize

### Alternative:
If abstractions prove too complex, consider **improving NimBLE or BTstack** instead, as they're already designed for portability.

## Next Steps (Pending User Approval)
1. Create k_work abstraction layer
2. Create k_sem abstraction layer
3. Create k_mutex abstraction layer (trivial, like NimBLE)
4. Implement work queue → scheduler mapping
5. Build minimal Zephyr BLE (GATT only) with abstractions
6. Test on ports/zephyr
7. Port to RP2/STM32
