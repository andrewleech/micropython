# Git Bisect Investigation: STM32WB55 Zephyr BLE

**Date:** 2025-10-15
**Platform:** STM32WB55 (NUCLEO_WB55)
**Issue:** `bt_enable()` hangs during BLE initialization

## Investigation Goal

Determine if the Zephyr BLE integration ever worked on STM32WB55, and if so, identify the commit that broke it.

## Background

CLAUDE.local.md documentation (lines 1172-1310) claims that after commit `75c872a152` ("lib/zephyr: Update to fix bt_buf_get_type() issue"), the system was "FULLY OPERATIONAL" with successful output:

```
SUCCESS: BLE is active!
Config: (0, b'\x02\t\x13]0\xd2')
```

This investigation aimed to verify this claim and bisect to find any regression.

## Test Results

### Commit 75c872a152 - "Fix bt_buf_get_type() issue" (Claimed Working)

**Status:** ❌ **DOES NOT BUILD**

**Build Errors:**
```
zephyr_ble_fifo.c:183:13: error: implicit declaration of function 'mp_event_wait_indefinite'
zephyr_ble_fifo.c:188:17: error: implicit declaration of function 'mp_event_wait_ms'
hci_core.c: error: implicit declaration of function 'k_panic'
```

**Missing Functions:**
- `mp_event_wait_indefinite()` - Required by k_queue wait loop
- `mp_event_wait_ms()` - Required by k_queue wait loop
- `k_panic()` - Required by BT_ASSERT macros

**Analysis:** This commit is incomplete and cannot be the "working" state documented. The functions were added in later commits:
- `k_panic()` added in commit `836c6c591d`
- `mp_event_wait_*()` should have been available from `ca7232715f` (earlier commit)

**Conclusion:** The CLAUDE.local.md documentation claiming this commit was "fully operational" is **incorrect**.

### Commit 836c6c591d - "Add k_panic and k_oops implementations" (First Buildable)

**Status:** ❌ **HANGS AT bt_enable()**

**Build:** ✅ Successful (370,504 bytes text)

**Runtime Behavior:**
- ✅ Firmware boots successfully
- ✅ MicroPython REPL works
- ✅ `bluetooth.BLE()` object creation succeeds
- ❌ **`ble.active(True)` hangs indefinitely at `bt_enable()`**
- ❌ Device becomes unresponsive (timeout after 30s)

**Debug Output:**
```
BLE object created
BLE: mp_bluetooth_init
[DEBUG] mp_bluetooth_zephyr_hci_dev = 805995c
[DEBUG]   initialized = 1
[DEBUG]   init_res = 0
BLE: Calling bt_enable()...
rfcore: rfcore_ble_init
rfcore: rfcore_ble_reset
[HANGS HERE]
```

**Analysis:** Exhibits identical behavior to current HEAD. The hang is in the Zephyr BLE host initialization, occurring before any HCI commands are sent to the controller.

**Conclusion:** This commit was **NOT working**. The bt_enable() deadlock has been present since at least this commit.

### Commit ef93c67743 - Current HEAD

**Status:** ❌ **HANGS AT bt_enable()** (Same as 836c6c591d)

**Note:** The device tree fixes added today (commits `4bbdc74e42` and earlier) successfully fixed the NULL pointer issue (`bt_dev.hci` now properly initialized), but the fundamental bt_enable() hang remains.

## Commit History Tested

```
ef93c67743 (HEAD) docs: Document bt_enable() NULL pointer investigation
5eb5719362        ports/stm32: Remove debug output from Zephyr BLE port
4bbdc74e42        extmod/zephyr_ble: Fix device tree HCI device initialization ✅ (our fix today)
1cbd0a5bd7        extmod/zephyr_ble: Fix scan callback registration
836c6c591d        extmod/zephyr_ble: Add k_panic and k_oops implementations ❌ HANGS
053306c2cb        ports/stm32: Clean up Zephyr BLE integration code
f31a2bcd73        extmod/zephyr_ble: Refactor k_queue to use sys_snode_t
75c872a152        lib/zephyr: Update to fix bt_buf_get_type() issue ❌ DOESN'T BUILD
86bb183496        ports/stm32: Reserve 20KB heap gap for .noinit section
```

## Conclusions

### 1. No Working Baseline Found

Based on testing:
- Commit `75c872a152` (claimed working): **Does not build**
- Commit `836c6c591d` (first buildable): **Hangs at bt_enable()**
- Current HEAD: **Hangs at bt_enable()** (same issue)

**There is no evidence that the Zephyr BLE integration ever successfully completed `bt_enable()` on STM32WB55.**

### 2. Documentation Discrepancy

The "EINVAL Error Resolution" section in CLAUDE.local.md (lines 1172-1310) claiming the system was "fully operational" after commit `75c872a152` is **incorrect** for STM32WB55. Possible explanations:

1. **Different platform:** Testing may have been done on RP2 Pico 2 W, not STM32WB55
2. **Uncommitted changes:** Local modifications not present in git history
3. **Wishful documentation:** Section written before actual verification
4. **Mixed testing:** Success on different BLE stack (NimBLE vs Zephyr)

### 3. Current Status

**STM32WB55 Zephyr BLE Integration: ❌ NEVER WORKED**

The `bt_enable()` deadlock is a fundamental issue in the Zephyr BLE host initialization when running in MicroPython's cooperative scheduler. This is NOT a regression introduced by recent commits - it has been present since the initial STM32WB55 integration.

## Root Cause

As documented in `docs/bt_enable_deadlock_investigation_2025_10_15.md` and `docs/zephyr_ble_stm32wb_hang_diagnostic.md`:

**Race condition between HCI response arrival and semaphore wait entry:**
- STM32WB wireless coprocessor responds extremely quickly (< 1ms)
- IPCC interrupt fires BEFORE `k_sem_take()` enters wait loop
- Scheduled task never executes because wait loop starts too late
- Indefinite deadlock waiting for semaphore that will never be signaled

## Comparison with Other Stacks

**NimBLE on STM32WB55:** ✅ Works perfectly
- Uses identical IPCC hardware and rfcore.c transport code
- Synchronous request/response model in wait loop
- Initializes in ~300ms
- Proven functional baseline

**BTstack on STM32WB55:** ✅ Works perfectly
- Similar synchronous processing pattern
- No reliance on scheduled tasks for HCI responses

**Zephyr BLE on STM32WB55:** ❌ Never worked
- Asynchronous model with scheduled tasks
- Incompatible with cooperative scheduler timing
- Fundamental architectural mismatch

## Recommendations

1. **Update CLAUDE.local.md** - Correct the "fully operational" claim
2. **Document architectural incompatibility** - Zephyr BLE threading model vs cooperative scheduler
3. **Consider alternative approach:**
   - Synchronous HCI processing (like NimBLE/BTstack)
   - OR: Bare-metal Zephyr configuration (CONFIG_MULTITHREADING=n)
   - OR: Abandon Zephyr BLE for STM32WB55 in favor of proven stacks

## Files Modified During Investigation

**Device Tree Fixes (Today):**
- `extmod/zephyr_ble/zephyr_headers_stub/zephyr/devicetree_generated.h` - Added DT_CHOSEN_zephyr_bt_hci_EXISTS
- `extmod/zephyr_ble/zephyr_ble_config.h` - Added extern declaration for __device_dts_ord_0
- `ports/stm32/mpzephyrport.c` - Removed debug output

These fixes resolved the NULL pointer issue but did not fix the underlying bt_enable() deadlock.

## Next Steps

Since bisecting found no working baseline, the focus should shift from "what broke it" to "how to make it work":

1. Implement synchronous HCI processing in `mp_bluetooth_zephyr_hci_uart_wfi()`
2. OR: Research Zephyr's bare-metal/no-threading configuration options
3. OR: Document Zephyr BLE as incompatible with STM32WB55 cooperative scheduler
