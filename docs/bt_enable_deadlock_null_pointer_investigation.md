# bt_enable() Deadlock: NULL Pointer Investigation

**Date:** 2025-10-15
**Status:** Hypothesis Disproven
**Platform:** STM32WB55 (NUCLEO_WB55)

## Investigation Summary

Investigated whether `bt_dev.hci` being NULL was the root cause of the `bt_enable()` deadlock.

**Result:** NULL pointer hypothesis is **DISPROVEN**. The HCI device is properly initialized.

## Findings

### Build Configuration

Successfully compiled with device tree fixes:

**Firmware Size:**
- text: 370,692 bytes
- data: 1,196 bytes
- bss: 39,928 bytes

**Files Modified:**
1. `extmod/zephyr_ble/zephyr_headers_stub/zephyr/devicetree_generated.h`
   - Added: `#define DT_CHOSEN_zephyr_bt_hci_EXISTS 1`

2. `extmod/zephyr_ble/zephyr_ble_config.h`
   - Added: `extern const struct device __device_dts_ord_0;`

### Runtime Verification

Added debug output to `bt_enable()` in `hci_core.c`:

```c
extern void mp_bluetooth_zephyr_debug_device(const struct device *dev);

int bt_enable(bt_ready_cb_t cb)
{
    int err;

    // DEBUG: Print bt_dev.hci value
    mp_bluetooth_zephyr_debug_device(bt_dev.hci);

    if (IS_ENABLED(CONFIG_ZTEST) && bt_dev.hci == NULL) {
        LOG_ERR("No DT chosen property for HCI");
        return -ENODEV;
    }
    // ...
}
```

**Debug Output Captured:**

```
MicroPython v1.27.0-preview.343.g5eb571936 on 2025-10-15; Pyboard with STM32WB55RG
Type "help()" for more information.
>>> import bluetooth
>>> ble = bluetooth.BLE()
>>> ble.active(True)
[DEBUG hci_core.c] bt_dev.hci = 0x8059a7c
[DEBUG hci_core.c]   name = HCI_STM32
[DEBUG hci_core.c]   state = 0x200184f0
[DEBUG hci_core.c]     initialized = 1
[DEBUG hci_core.c]     init_res = 0
[HANG - timeout after 30 seconds]
```

### Key Observations

1. ✅ **`bt_dev.hci` is NOT NULL** - it has valid address `0x8059a7c`
2. ✅ **HCI device name** is `HCI_STM32` (correctly set)
3. ✅ **Device state shows initialization succeeded:**
   - `initialized = 1`
   - `init_res = 0` (success)
4. ✅ **Code reaches `bt_enable()`** successfully
5. ❌ **Hang occurs AFTER device verification** completes

### Conclusion

The device tree integration is **working correctly**. The fixes applied successfully initialize `bt_dev.hci` with a valid device structure.

The `bt_enable()` deadlock is **NOT caused by a NULL pointer**. The hang occurs deeper in the Zephyr BLE stack initialization, likely in:

1. Work queue processing
2. Semaphore synchronization
3. HCI command/response handling
4. RF core communication

## Root Cause Still Unknown

The actual deadlock mechanism remains unidentified. From previous HCI trace analysis (`docs/zephyr_ble_stm32wb_hang_diagnostic.md`), we know:

- NimBLE successfully initializes in ~300ms
- Zephyr stack never sends any HCI commands
- Hang occurs **before** any HCI communication
- Likely an internal Zephyr host deadlock

## Next Steps

Consider investigating:

1. Work queue initialization and processing
2. Semaphore wait conditions in `bt_hci_cmd_send_sync()`
3. Thread context expectations (cooperative scheduler vs threading)
4. Required Zephyr subsystem initialization that may be missing

## Commits

- `4bbdc74e42` - extmod/zephyr_ble: Fix device tree HCI device initialization.
- `5eb5719362` - ports/stm32: Remove debug output from Zephyr BLE port.
