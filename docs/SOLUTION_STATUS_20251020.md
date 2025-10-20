# Zephyr BLE Solution Status - 2025-10-20

## Summary

Zephyr BLE integration is **PARTIALLY WORKING** but has critical bugs preventing multi-instance testing and connection handling.

### What Works ✓
- BLE Initialization (`ble.active(True)`)
- Activate/Deactivate cycles within single REPL session
- Simple advertising without payload data
- BLE Scanning (69-92 devices detected vs 227 for NimBLE)
- Can BE connected to by NimBLE central (but no IRQ events)

### What Fails ✗
- **Multi-instance test framework (crashes on initialization)** - BLOCKING
- Advertising with payload data (EINVAL)
- Connection IRQ events never delivered - callbacks never fire
- Second connection attempt crashes (assertion failure)
- Central mode not implemented (stubbed out)

## Critical Bug: Soft Reset Incompatibility

**Assertion**: `conn.c:1526` - "Conn reference counter is 0"

**When**: ONLY during multi-instance test framework execution, NOT during manual testing

**Root Cause**: Fundamental incompatibility between Zephyr BLE stack persistence and MicroPython's soft reset mechanism.

### Analysis of Soft Reset Issue

The STM32 main.c soft reset loop (line 736-738) DOES call `mp_bluetooth_deinit()`, but:

1. Zephyr BLE stack **cannot be turned off** (per Zephyr design)
2. Our deinit sets state to `SUSPENDED` but Zephyr keeps running
3. Zephyr has internal connection state we cannot access/clear
4. On soft reset, orphaned Zephyr connections trigger reference counting assertion

### Fixes Attempted (ALL Unsuccessful)

1. ✗ Corrected `bt_conn_unref()` target to stored pointer
2. ✗ Added connection cleanup loop in deinit
3. ✗ Added safety guards to callbacks (check ACTIVE state)
4. ✗ **Unregistered connection callbacks in deinit** (commit this session)
5. ✗ **Forcibly disconnected all Zephyr connections** (commit this session)

None of these fixes resolved the crash. The problem is deeper than our code can address.

### Key Observations

**Connection callbacks NEVER execute:**
- Added extensive debug logging to `mp_bt_zephyr_connected()` and `mp_bt_zephyr_disconnected()`
- These callbacks **NEVER FIRE** even though Pyboard-D successfully connects
- Suggests either:
  - Callback registration fails silently
  - STM32WB55 controller doesn't send connection events properly
  - Zephyr BLE host incompatibility with STM32WB55 IPCC

**Manual testing works perfectly:**
```python
ble.active(True)
ble.gap_advertise(20000)  # Works!
ble.active(False)
ble.active(True)  # Reactivation works!
```

**Multi-test framework crashes immediately:**
```
gap_advertise
ASSERT FAILED at ../../lib/zephyr/subsys/bluetooth/host/conn.c:1526
RuntimeError: BLE stack fatal error (k_panic)
```

## Recommendation

**DO NOT use Zephyr BLE for STM32WB55** in production. Critical issues:

1. **Test framework incompatibility** - Cannot run automated tests
2. **Connection callbacks don't fire** - No connection state visibility
3. **Soft reset crashes** - Incompatible with MicroPython reset model
4. **Central mode not implemented** - Can only be peripheral

**Use NimBLE (default variant)** which is fully functional and passes all tests.

Zephyr BLE **might** be viable for:
- Scanning-only applications (works but 30-40% performance vs NimBLE)
- Single-session applications (no soft resets)
- Beacon/advertising-only applications (if payload format fixed)

## Files Modified (Final State)

### Core Implementation
- `extmod/zephyr_ble/modbluetooth_zephyr.c:218-224` - Added ACTIVE state check to connected callback
- `extmod/zephyr_ble/modbluetooth_zephyr.c:244-250` - Added ACTIVE state check to disconnected callback
- `extmod/zephyr_ble/modbluetooth_zephyr.c:498-508` - Added force-disconnect all connections in deinit
- `extmod/zephyr_ble/modbluetooth_zephyr.c:525-529` - Added callback unregister in deinit

### Documentation
- `docs/MULTITEST_RESULTS_20251020.md` - Multi-test investigation
- `docs/GDB_INVESTIGATION_20251020.md` - GDB debugging findings
- `docs/BUFFER_FIX_VERIFICATION.md` - Scanning performance
- `docs/SOLUTION_STATUS_20251020.md` - This document

## Conclusion

**Zephyr BLE is NOT production-ready for STM32WB55.** The fundamental architecture mismatch between Zephyr's persistent BLE stack and MicroPython's soft reset model cannot be resolved without upstream Zephyr changes or a complete redesign of the integration.

**Recommendation: Abandon Zephyr BLE integration** and focus on NimBLE, which is proven and fully functional.
