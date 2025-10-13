# Zephyr BLE bt_enable() Deadlock Analysis and Resolution Plan

## Date: 2025-10-14

## Problem Summary
Zephyr BLE stack deadlocks during `bt_enable()` → `bt_init()` on both RP2 Pico 2 W and STM32WB55 NUCLEO boards. The hang occurs AFTER successful HCI transport initialization but BEFORE the host stack completes initialization.

## What Works (Verified)
1. ✅ Firmware boots successfully on both platforms
2. ✅ HCI transport layer fully functional
3. ✅ Controller communication working (HCI commands/responses succeed)
4. ✅ `bluetooth.BLE()` object creation
5. ✅ OS adapter layer (k_work, k_sem, k_mutex, k_timer, atomics)
6. ✅ Work queue polling mechanism
7. ✅ Semaphore busy-wait with MICROPY_EVENT_POLL_HOOK

## What Fails
❌ `ble.active(True)` hangs indefinitely in `bt_init()`
❌ No HCI commands sent during hang (proven by lack of `[SEND]` traces)
❌ Eventually causes watchdog reset or crash

## Previous Attempts (Already Implemented)

### 1. OS Adapter Layer (Milestones 1-5) ✅ COMPLETE
**Files:** `extmod/zephyr_ble/hal/*.{c,h}`

**What was tried:**
- **k_work abstraction**: Event queue with global linked list, processed by polling
- **k_sem abstraction**: Busy-wait with work queue processing during wait
- **k_mutex abstraction**: No-op (scheduler provides mutual exclusion)
- **Atomic operations**: Using MICROPY_PY_BLUETOOTH_ENTER/EXIT critical sections
- **k_timer**: Software timers integrated with MicroPython soft_timer
- **k_fifo**: FIFO queue implementation
- **Polling function**: `mp_bluetooth_zephyr_poll()` called from `mp_bluetooth_hci_poll()`

**Result:** Implemented successfully, but bt_init() still hangs.

### 2. HCI Transport Verification ✅ COMPLETE
**Files:** `ports/stm32/rfcore.c`, `ports/stm32/mpzephyrport.c`

**What was tried:**
- Added comprehensive HCI tracing to verify transport works
- Verified controller responds correctly to all commands
- Confirmed NimBLE works perfectly with same transport

**Result:** HCI transport proven functional. Issue is in Zephyr host, not transport.

### 3. Boot Crash Fix ✅ COMPLETE
**Files:** `ports/stm32/boards/NUCLEO_WB55/mpconfigvariant_zephyr.mk`

**What was tried:**
- Fixed vector table location (RAM → flash ROM)
- Enabled firmware to boot via probe-rs

**Result:** Firmware boots successfully, but bt_init() still hangs.

### 4. Debug Output ✅ ADDED
**Files:** Various HAL and port files

**What was tried:**
- Added debug traces to track initialization flow
- Confirmed bt_enable() → bt_hci_open() succeeds
- Confirmed hang occurs in bt_init() call

**Result:** Narrowed location to bt_init() but not the specific code path.

## Root Cause Hypothesis

### Working Theory: Circular Dependency in Initialization
Zephyr's `bt_init()` likely has initialization code that:
1. Submits work items to work queue
2. Waits on semaphore for completion
3. Work items signal semaphore when complete

**Problem in cooperative scheduler:**
- Work queue only processes during `MICROPY_EVENT_POLL_HOOK`
- If `bt_init()` waits on semaphore without yielding, work never processes
- Deadlock: waiting for work that can't run because we're not yielding

### Evidence Supporting This Theory:
1. NimBLE works because it's designed for cooperative schedulers
2. Zephyr expects preemptive threading (work runs in separate thread)
3. Our k_sem busy-waits but may not yield often enough
4. bt_init() has complex initialization that likely uses work queues

## What Hasn't Been Tried Yet

### 1. CRITICAL: Add Debug Traces to bt_init() Path ⚠️ HIGH PRIORITY
**Goal:** Identify exact location where hang occurs

**Implementation:**
```c
// Add to lib/zephyr/subsys/bluetooth/host/hci_core.c in bt_init()
mp_printf(&mp_plat_print, "[bt_init] Starting initialization\n");
// Add after each major step:
mp_printf(&mp_plat_print, "[bt_init] Step 1 complete\n");
// etc.
```

**Expected outcome:** Find which initialization step never completes

### 2. CRITICAL: Force Work Queue Processing in Semaphore Wait ⚠️ HIGH PRIORITY
**Goal:** Ensure work items can execute while waiting on semaphore

**Current implementation (zephyr_ble_sem.c):**
```c
int k_sem_take(struct k_sem *sem, k_timeout_t timeout) {
    // ... setup ...
    while (sem->count == 0 && !timed_out) {
        mp_bluetooth_zephyr_work_process();  // ← Processes work
        mp_bluetooth_zephyr_hci_uart_process();  // ← Processes UART
        MICROPY_EVENT_POLL_HOOK;  // ← Yields to scheduler
        // ... timeout check ...
    }
}
```

**Problem:** May not yield frequently enough, or work processing order wrong

**New approach to try:**
```c
int k_sem_take(struct k_sem *sem, k_timeout_t timeout) {
    while (sem->count == 0 && !timed_out) {
        // Process work FIRST to handle any pending work
        mp_bluetooth_zephyr_work_process();

        // Yield IMMEDIATELY to allow other tasks to run
        MICROPY_EVENT_POLL_HOOK;

        // Process UART HCI after yielding
        mp_bluetooth_zephyr_hci_uart_process();

        // Check timeout
        // ...
    }
}
```

**Rationale:** Current code may process work, then yield, but work that schedules more work may not get second chance before timeout check.

### 3. Add Timeout Debug to Semaphore Waits
**Goal:** Detect if timeouts are being hit incorrectly

**Implementation:**
```c
if (k_timeout_equals(timeout, K_FOREVER)) {
    mp_printf(&mp_plat_print, "[k_sem_take] Waiting forever on sem %p\n", sem);
} else {
    mp_printf(&mp_plat_print, "[k_sem_take] Waiting %d ticks on sem %p\n",
              timeout.ticks, sem);
}
// ... wait loop ...
if (timed_out && !k_timeout_equals(timeout, K_FOREVER)) {
    mp_printf(&mp_plat_print, "[k_sem_take] TIMEOUT on sem %p\n", sem);
}
```

### 4. Verify Work Queue Not Overflowing
**Goal:** Check if work queue is filling up but not being processed

**Implementation:**
```c
// In zephyr_ble_work.c, add debug counters:
static int work_submit_count = 0;
static int work_process_count = 0;

void k_work_submit(struct k_work *work) {
    work_submit_count++;
    if ((work_submit_count % 10) == 0) {
        mp_printf(&mp_plat_print, "[k_work] Submitted: %d, Processed: %d\n",
                  work_submit_count, work_process_count);
    }
    // ... existing code ...
}

void mp_bluetooth_zephyr_work_process(void) {
    // Count how many work items we process
    int processed = 0;
    // ... process work items ...
    work_process_count += processed;
}
```

### 5. Try Forcing Synchronous Initialization
**Goal:** Bypass work queue for initial setup

**Hypothesis:** Maybe bt_init() can complete if we force synchronous execution

**Implementation:** Modify bt_init() to call initialization functions directly instead of via work queue. This would require source patching but might prove the theory.

## Recommended Next Steps (Priority Order)

### Step 1: Add Comprehensive Debug Traces (1-2 hours)
1. Add debug traces to `lib/zephyr/subsys/bluetooth/host/hci_core.c` bt_init()
2. Add traces to work queue submit/process
3. Add traces to semaphore take/give operations
4. Rebuild and test to identify exact hang location

**Files to modify:**
- `lib/zephyr/subsys/bluetooth/host/hci_core.c` (add bt_init traces)
- `extmod/zephyr_ble/hal/zephyr_ble_work.c` (add work queue counters)
- `extmod/zephyr_ble/hal/zephyr_ble_sem.c` (add semaphore debug)

### Step 2: Optimize Semaphore Wait Loop (30 minutes)
1. Reorder work processing and yielding in k_sem_take()
2. Test if this resolves deadlock
3. If not, gather more data from Step 1 traces

**Files to modify:**
- `extmod/zephyr_ble/hal/zephyr_ble_sem.c`

### Step 3: Compare with Working RP2 Build (if it exists)
1. Check if RP2 build actually works (CLAUDE.local.md claims 93% test pass)
2. If it works, diff the two builds to find what's different
3. Apply working approach to STM32WB55

### Step 4: Consider Alternative Approaches
If Steps 1-3 don't resolve it:

**Option A: Minimal Work Queue Processing**
- Identify which work items MUST complete during init
- Process those synchronously instead of via work queue

**Option B: Zephyr Source Patches**
- Patch bt_init() to be more cooperative-scheduler friendly
- Add explicit yield points in long initialization sequences

**Option C: Threading Shim Layer**
- Add minimal threading primitives that Zephyr expects
- Use cooperative "threads" that yield at specific points

## Success Criteria
1. `ble.active(True)` completes without hanging
2. Can perform basic BLE operations (scan, advertise)
3. Works on both RP2 and STM32WB55 platforms
4. No source patching required (or minimal, well-documented patches)

## Estimated Time
- **Step 1 (Debug traces):** 1-2 hours
- **Step 2 (Semaphore optimization):** 30 minutes
- **Step 3 (Analysis):** 1 hour
- **Total before considering alternatives:** 2.5-3.5 hours

If alternative approaches needed: +4-8 hours depending on complexity.
