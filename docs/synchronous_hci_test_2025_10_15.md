# Synchronous HCI Processing Test (2025-10-15)

## Hypothesis

Based on bisect investigation findings, commit 053306c2cb ("Clean up Zephyr BLE integration code") changed from synchronous to asynchronous HCI packet delivery. The hypothesis was that this change introduced the bt_enable() deadlock.

**Before cleanup (f31a2bcd73):**
- `h4_uart_byte_callback()` called `recv_cb(hci_dev, buf)` directly (synchronous)
- Simple while loop in `mp_bluetooth_zephyr_hci_uart_wfi()`

**After cleanup (053306c2cb):**
- `h4_uart_byte_callback()` queues buffer and schedules task (asynchronous)
- Complex event waiting and queue processing in `mp_bluetooth_zephyr_hci_uart_wfi()`

## Test Procedure

1. Checked out commit f31a2bcd73 (pre-cleanup, synchronous version)
2. Applied necessary fixes:
   - Device tree fixes (DT_CHOSEN_zephyr_bt_hci_EXISTS, __device_dts_ord_0)
   - Added k_panic/k_oops (missing in old commit, added in 836c6c591d)
   - Fixed format warning in hci_core.c (Build %u → %lu)
3. Built firmware: 370,944 bytes (text) + 1,192 bytes (data)
4. Flashed to NUCLEO_WB55 via probe-rs
5. Tested `ble.active(True)` via mpremote

## Test Results

**HYPOTHESIS DISPROVEN:** Synchronous version ALSO hangs at bt_enable()

```
BLE: mp_bluetooth_init
BLE: mp_bluetooth_deinit 0
BLE: Starting BLE initialization (state=OFF)
[FIFO] Debug output enabled
[DEBUG] mp_bluetooth_zephyr_hci_dev = 8059b78
[DEBUG]   name = HCI_STM32
[DEBUG]   state = 200004a4
[DEBUG]     initialized = 1
[DEBUG]     init_res = 0
BLE: Calling bt_enable()...
<TIMEOUT - HANG>
```

## Conclusions

1. **The async vs sync change was NOT the root cause** of the bt_enable() deadlock
2. **The deadlock existed in the synchronous version** (commit f31a2bcd73)
3. **No working baseline exists** - bisect findings confirmed
4. The cleanup commit 053306c2cb improved code quality but did not introduce the bug

## Root Cause Analysis

The bt_enable() deadlock has existed since initial integration. Previous HCI trace diagnostic (docs/zephyr_ble_stm32wb_hang_diagnostic.md) showed:

- Zephyr BLE host deadlocks BEFORE sending any HCI commands
- Transport is functional (proven by NimBLE success)
- Issue is internal to Zephyr host stack initialization

**Likely causes:**
- Work queue deadlock: Initialization waiting on work items never submitted
- Semaphore ordering: Code waits on semaphore signaled by un-queued work
- Missing kernel init: Zephyr subsystem not properly initialized
- Thread context assumption: Zephyr expects threading unavailable in cooperative scheduler

## Next Steps

The synchronous vs asynchronous HCI delivery was a red herring. Investigation should focus on:

1. **GDB debugging of bt_enable()**: Single-step through initialization to find exact deadlock location
2. **Work queue instrumentation**: Add debug output to all work queue and semaphore operations
3. **Zephyr initialization requirements**: Review what threading/kernel features bt_enable() expects
4. **Compare with working stacks**: How do NimBLE/BTstack differ in initialization approach?

## Test Configuration

- **Hardware:** NUCLEO-WB55 (STM32WB55RGVx)
- **Commit tested:** f31a2bcd73 (pre-cleanup synchronous version)
- **Device tree fixes:** Applied from later commits
- **k_panic/k_oops:** Backported from commit 836c6c591d
- **Firmware size:** 370,944 bytes (text) + 1,192 bytes (data)
- **Date:** 2025-10-15
