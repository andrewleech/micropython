# Issue #10: Soft Reset Hang After BLE Deactivation

## Problem Statement

Device hangs when executing `machine.soft_reset()` after BLE usage on RP2 Pico W with Zephyr BLE stack.

**Hang Location**: `shared/runtime/pyexec.c:239` - `mp_hal_stdout_tx_strn("\x04", 1)`

**Test Case**:
```python
import bluetooth, machine
ble = bluetooth.BLE()
ble.active(True)
ble.active(False)
machine.soft_reset()  # Hangs here
```

## Root Cause Analysis

### Timing Issue
EOF character is printed during exception handling in `shared/runtime/pyexec.c` **before** reaching `soft_reset_exit` label in `ports/rp2/main.c` where CYW43 mutex is reset.

**Sequence**:
1. `machine.soft_reset()` raises SystemExit
2. pyexec exception handler catches SystemExit
3. pyexec attempts to print EOF at line 239 → **HANGS**
4. Never reaches `soft_reset_exit` where `cyw43_smp_mutex_reset()` would be called

### Technical Details

**CYW43 Mutex Contention**:
- BLE stack (Zephyr) uses CYW43 SPI bus for HCI communication
- USB CDC also uses CYW43 SPI bus (shared resource)
- After `ble.active(False)`, CYW43 mutex may be left locked or FreeRTOS scheduler in bad state
- USB CDC `mp_usbd_cdc_tx_strn()` attempts to acquire mutex → deadlock

**USB CDC State**:
- `mp_hal_stdio_poll(MP_STREAM_POLL_WR)` returns writable (false positive)
- USB CDC buffer nearly full (8-53 bytes free vs 40 at startup)
- `mp_usbd_cdc_tx_strn()` has 500ms timeout but doesn't trigger
- Hang occurs in `mp_event_wait_ms()` or `mp_usbd_task()` - FreeRTOS scheduler blocked

**Investigated Approaches**:
1. ❌ **Scheduler drain before EOF**: Made problem worse (filled USB buffer)
2. ❌ **USB writability poll check**: False positive - reports writable but hangs anyway
3. ❌ **Skip EOF for SystemExit**: Works but breaks mpremote/tooling integration

## Current State

**Attempted Fix #1**: Skip EOF for SystemExit in `shared/runtime/pyexec.c`
- **Result**: Device no longer hangs, soft reset succeeds
- **Problem**: mpremote times out waiting for EOF (10s default)
- **Status**: **REJECTED** - EOF is crucial for PC tooling integration

**Attempted Fix #2**: Port-specific soft_reset that resets CYW43 mutex before SystemExit
- **Implementation**:
  - Added `MICROPY_MACHINE_SOFT_RESET_OBJ` override in `extmod/modmachine.c`
  - Created `machine_soft_reset_rp2()` in `ports/rp2/modmachine.c` that calls `cyw43_smp_mutex_reset()` before raising SystemExit
  - Custom function confirmed called via debug output
- **Result**: Still hangs during EOF print even after mutex reset
- **Status**: **INSUFFICIENT** - Mutex reset alone doesn't fix the hang

**Attempted Fix #3**: Clear scheduler queue before SystemExit
- **Implementation**: Added scheduler queue clear in `machine_soft_reset_rp2()`:
  ```c
  MP_STATE_VM(sched_state) = MP_SCHED_IDLE;
  MP_STATE_VM(sched_head) = NULL;
  MP_STATE_VM(sched_tail) = NULL;
  ```
- **Result**: Still hangs during EOF print
- **Evidence**:
  ```
  DBG: machine_soft_reset_rp2 called
  DBG: scheduler queue cleared
  DBG: raising SystemExit
  DBG: about to print EOF
  [hang - "EOF printed" never appears]
  ```
- **Status**: **INSUFFICIENT** - Issue is deeper in FreeRTOS/GIL/TinyUSB layer

**Files Modified**:
- `extmod/modmachine.c` - Added `MICROPY_MACHINE_SOFT_RESET_OBJ` override mechanism
- `ports/rp2/modmachine.c` - Port-specific soft_reset with mutex reset
- `shared/runtime/pyexec.c` - Debug output (temporary)
- `ports/rp2/main.c` - Debug output removed
- `extmod/zephyr_ble/modbluetooth_zephyr.c` - Debug output removed

## Key Observation: ports/zephyr Doesn't Have This Issue

The upstream `ports/zephyr` port doesn't exhibit this hang behavior, suggesting there's an architectural pattern we should follow.

**Differences to investigate**:
1. How does ports/zephyr handle soft reset sequence?
2. Where does ports/zephyr print EOF relative to cleanup?
3. Does ports/zephyr have different exception handling flow?
4. How does ports/zephyr manage USB CDC and BLE resource sharing?

## Architectural Requirements for Correct Fix

1. **EOF must be printed** - Required for mpremote and other tooling
2. **No hangs** - Device must soft reset reliably
3. **Resource cleanup ordering** - CYW43 mutex must be available before EOF printing
4. **Minimal invasiveness** - Avoid major refactoring if possible

## Potential Solutions

### Option 1: Reset CYW43 Mutex Before EOF
Move `cyw43_smp_mutex_reset()` to execute **before** EOF printing.

**Challenges**:
- pyexec.c exception handler doesn't know about port-specific resources
- Would require port-specific hooks in shared/runtime/pyexec.c
- Need to identify when SystemExit is due to soft reset vs other causes

### Option 2: Defer EOF Until After Cleanup
Restructure exception handling to defer EOF printing until after `soft_reset_exit` cleanup.

**Challenges**:
- Major refactoring of exception handling flow
- EOF needs to be printed from within exception context for proper error handling
- Would affect all ports, not just RP2

### Option 3: Pre-Cleanup Hook in pyexec
Add a `BOARD_BEFORE_EOF` hook in pyexec.c that ports can use to reset resources.

**Approach**:
```c
// In shared/runtime/pyexec.c before EOF printing:
#ifdef MICROPY_BOARD_BEFORE_EOF
MICROPY_BOARD_BEFORE_EOF();
#endif
mp_hal_stdout_tx_strn("\x04", 1);
```

**Advantages**:
- Minimal changes to shared code
- Port-specific cleanup can run before EOF
- Follows existing pattern (MICROPY_BOARD_START_SOFT_RESET, etc.)

### Option 4: Follow ports/zephyr Pattern
Study how `ports/zephyr` avoids this issue and apply the same pattern.

**Next Steps**:
1. Examine `ports/zephyr/main.c` soft reset and exception handling
2. Examine `ports/zephyr` USB CDC integration
3. Identify key architectural differences
4. Apply pattern to `ports/rp2`

## Proposed Solution: Defer EOF Until After Cleanup

The fundamental issue is **timing**: EOF is printed during exception handling *before* reaching soft_reset_exit where cleanup happens. All attempts to pre-clean resources before raising SystemExit have failed.

**New Approach**: Print EOF at `soft_reset_exit` *after* cleanup, not during exception handling.

### Implementation Strategy

1. **Skip EOF in exception handler for SystemExit**:
   ```c
   // In shared/runtime/pyexec.c:223
   if (exec_flags & EXEC_FLAG_PRINT_EOF) {
       if (!(ret & PYEXEC_FORCED_EXIT)) {  // Don't print for SystemExit yet
           mp_hal_stdout_tx_strn("\x04", 1);
       }
   }
   ```

2. **Print EOF at soft_reset_exit after cleanup**:
   ```c
   // In ports/rp2/main.c at soft_reset_exit label, AFTER all cleanup
   soft_reset_exit:
       mp_printf(MP_PYTHON_PRINTER, "MPY: soft reboot\n");

       // All cleanup happens here (BLE deinit, mutex resets, etc.)
       ...

       // NOW it's safe to print EOF
       #if MICROPY_HW_ENABLE_USBDEV
       mp_hal_stdout_tx_strn("\x04", 1);
       #endif

       goto soft_reset;
   ```

### Advantages
- EOF printed after all resources cleaned up and reset
- Minimal changes to shared code
- Preserves EOF for tooling
- Works regardless of what caused the SystemExit

### Disadvantages
- EOF comes AFTER "MPY: soft reboot" message (reversed order)
- Only works for soft reset via Ctrl-D or machine.soft_reset()
- Other SystemExit paths (sys.exit() in scripts) won't get EOF

### Alternative: Flag-Based Approach

Add a flag to indicate SystemExit is for soft reset:
```c
// Set flag in machine_soft_reset_rp2 before raising
MP_STATE_VM(soft_reset_requested) = true;

// Check in soft_reset_exit and print EOF
if (MP_STATE_VM(soft_reset_requested)) {
    mp_hal_stdout_tx_strn("\x04", 1);
    MP_STATE_VM(soft_reset_requested) = false;
}
```

## Update: 2026-01-13 - Intermittent Hang After Multiple Test Cycles

### Recent Fixes Applied

Several fixes have been applied to address specific crash scenarios:

1. **net_buf pool_id mismatch** (86e96dd6dd): After soft reset, buffers in `.noinit` section retained stale `pool_id` values. Fixed by resetting `buf->pool_id` when getting buffers from LIFO.

2. **net_buf pool state crash** (a8c176ebf0): Fixed crash during `gap_advertise()` with 128-bit UUID services after soft reset.

3. **BLE hang during sleep** (89420f6ded): Fixed hang when advertising is active and device enters sleep.

### Current State (Post-Fixes)

**Test Results**:
- Individual BLE tests pass reliably
- First batch of 4 tests from fresh boot: **PASS**
- Subsequent test runs: **Eventually hang after 4-5 cumulative tests**

**Test Pattern**:
```
Fresh boot → gap_advertise ✓ → gap_connect ✓ → characteristic ✓ → data_transfer ✓
Second run → characteristic TIMEOUT (device hung)
```

**Observations**:
- The hang is intermittent - sometimes more tests pass before hang
- Hang occurs during soft reset between tests (Ctrl-D in test framework)
- Device becomes completely unresponsive (USB serial timeout)
- Requires power cycle to recover

### Root Cause Hypothesis

The previous fixes addressed specific crash scenarios but there appears to be an underlying **resource leak or state corruption** that accumulates over multiple soft reset cycles:

1. Each BLE test cycle (init → operations → deinit) may leave residual state
2. After 4-5 cycles, accumulated state causes hang during soft reset
3. The hang location varies - sometimes during BLE deinit, sometimes during USB EOF

### Affected Tests

All BLE multitest files are affected when run in sequence:
- `ble_gap_advertise.py`
- `ble_gap_connect.py`
- `ble_characteristic.py`
- `ble_gatt_data_transfer.py`

### Workaround

Power cycle the device between test batches to ensure clean state.

## Resolution (2026-01-13)

The intermittent hang after multiple BLE test cycles has been **FIXED** in commit 1cad43a469.

### Root Causes Identified

1. **GATT Service Memory Leak**: Service attributes, UUIDs, and bt_gatt_chrc structs
   allocated via `malloc()` were never freed on deinit.

2. **Static Flags Not Reset**: Several flags persisted across soft resets:
   - `mp_bt_zephyr_callbacks_registered` - caused callback re-registration issues
   - `mp_bt_zephyr_indicate_pool[].in_use` - leaked indication slots
   - `hci_rx_task_started/exited` - stale HCI RX task state

3. **Work Queue Linkage Bug**: `k_sys_work_q.nextq` retained stale value after soft
   reset, causing `k_work_queue_init()` to return early without reinitializing.

### Fixes Applied

1. **Memory Cleanup**: Added `mp_bt_zephyr_free_service()` to properly free:
   - Service and characteristic declaration UUIDs (all malloc'd by gatt_db_add)
   - Service declaration user_data (malloc'd UUID copy)
   - Characteristic declaration user_data (malloc'd bt_gatt_chrc struct)
   - Attributes array and service struct

2. **Immediate UUID Freeing**: Free temporary UUIDs from `create_zephyr_uuid()`
   right after `gatt_db_add()` copies them.

3. **Static Flag Reset**: Reset all static flags in `mp_bluetooth_deinit()`:
   - Reset `mp_bt_zephyr_callbacks_registered = false`
   - Clear `mp_bt_zephyr_indicate_pool[].in_use` flags
   - Reset HCI RX task flags in `mp_bluetooth_zephyr_port_deinit()`

4. **Work Queue Reset**: Added `mp_bluetooth_zephyr_work_reset()` to clear:
   - `global_work_q` linked list
   - `k_sys_work_q` and `k_init_work_q` head/nextq pointers
   - Recursion guards and init phase flags

### Verification

- 20+ consecutive BLE multitest cycles completed without hang
- Tests: ble_gap_advertise, ble_gap_connect, ble_characteristic, ble_gatt_data_transfer
- Previous failure point was 4-5 cumulative tests

## Action Items

1. ✅ Document root cause and current state
2. ✅ Examine ports/zephyr architecture
3. ✅ Discuss with code review agent for architectural guidance
4. ✅ Fix net_buf pool_id mismatch (86e96dd6dd)
5. ✅ Fix net_buf pool state crash (a8c176ebf0)
6. ✅ Fix cumulative resource leak after multiple soft resets (1cad43a469)
7. ~~Implement deferred EOF solution~~ (not needed - resource cleanup fixed the issue)
8. ~~Test with mpremote to verify EOF delivery~~ (not needed)

## References

- Fix commit: 1cad43a469
- Test script: `test_soft_reset_hang.py`
- Related: Issue #9 (HCI RX task hangs during cleanup)
