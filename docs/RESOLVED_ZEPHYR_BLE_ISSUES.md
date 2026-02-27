# Resolved Zephyr BLE Issues

Historical record of issues encountered and fixed during Zephyr BLE integration.

---

## Issue #6: Connection Callbacks (RP2 Pico W)
- Fixed via GATT client implementation and TX context management
- Commit: 2fe8901cec

## Issue #9: HCI RX Task Hang (RP2 Pico W)
- HCI RX task caused hangs during shutdown (gap_scan(None) or ble.active(False))
- Fixed: Stop task BEFORE bt_disable(), use task notification for immediate wakeup
- Commit: 82741d16dc

## Issue #10: Soft Reset Hang (RP2 Pico W)
- Resource leaks caused hang after 4-5 BLE test cycles
- Fixed: static flags reset, work queue reset, GATT memory freed
- Commit: 1cad43a469

## Issue #11: STM32WB55 Spurious Disconnect
- `mp_bluetooth_hci_poll()` called `mp_bluetooth_zephyr_poll()` but not `run_zephyr_hci_task()`, so HCI packets from IPCC were never processed during wait loops
- Fixed: `mp_bluetooth_hci_poll()` now calls `run_zephyr_hci_task()`

## Issue #12: STM32WB55 GATTC 5-Second Delay
- `machine.idle()` uses `__WFI()` but didn't process scheduler; scheduled work items never ran
- Fixed: `mp_handle_pending()` called from `machine.idle()` (04e3919eaf)

## Issue #13: STM32WB55 Zephyr Variant Boot Failure
- No longer occurring after general stabilization work

## Issue #14: STM32WB55 GATTC Delayed Operations
- Work queue items not processed immediately after GATTC API calls
- Fixed: Call `mp_bluetooth_zephyr_work_process()` after all GATTC operations (0ce75fef8c)

## Issue #15: RP2 Pico W Soft Reset Hang After BLE Test
- net_buf data arrays placed in `.noinit` section via `__noinit` attribute; stale buffer data corruption on reinit
- Fixed: `__noinit` defined as empty so net_buf data goes in regular BSS (ebb5fbd052)

## Issue #16: Zephyr-to-Zephyr Connection Callbacks Not Firing
- Resolved by Issue #15 fix (net_buf BSS placement)

## Issue #17: WB55 Central Role Broken
- Synchronous HCI callback race: connected callback fired DURING bt_conn_le_create() before return, causing refcount issues
- Fixed: detect synchronous callback and unref extra reference (92db92a75b, 1cee1b02e7)

## Issue #18: Pico W Central k_panic in Service Discovery
- FreeRTOS HCI RX task had only 4KB stack, insufficient for Zephyr BLE callback chains
- Fixed: Increased HCI_RX_TASK_STACK_SIZE from 1024 to 2048 words (db8543928e)

## Issue #20: L2CAP Peripheral Regression
- Zephyr has no `bt_l2cap_server_unregister()`. After soft reset, re-registering same PSM returned EADDRINUSE
- Fixed: Static L2CAP server structure that persists across soft resets (4454914a6e)

## Issue #21: BLE Operations Fail from IRQ Callback
- Connection inserted into tracking list AFTER Python callback fired
- Fixed: Insert connection BEFORE firing callback (227567a07f)

## Issue #22: ble_irq_calls.py GATT Read/Write Issues
- Multiple callback bugs: missing READ_RESULT for empty characteristics, duplicate WRITE_DONE on disconnect, info.id using wrong value, descriptor count differences between stacks
- Fixed: 45517be03d, 00d707e6b2

## SC Pairing as Peripheral (RP2 Pico W)
- TinyCrypt produces big-endian ECC keys, BLE SMP expects little-endian
- Fixed: byte-order conversion in `zephyr_ble_crypto.c` (1137c06770)

## STM32WB55 Pairing
- TinyCrypt sources not compiled, hardware RNG not working, bonded flag always 0
- Fixed: Makefile, HAL include, deferred encryption callback (70fad3b653)

## Pico W Pair+Bond (CONFIG_BT_SIGNING)
- `#define CONFIG_BT_SIGNING 0` treated as enabled by Zephyr's `#if defined()` guards
- Fixed: Leave disabled configs undefined (d454a5ba17)

## GATTC NOTIFY/INDICATE Callbacks
- Replaced `bt_gatt_resubscribe()` with proper `bt_gatt_subscribe()`/`bt_gatt_unsubscribe()` in CCCD write path

## GATTS Read Request Callback
- `mp_bt_zephyr_gatts_attr_read()` now calls `mp_bluetooth_gatts_on_read_request()` (9dbd432020)

## MTU Exchange (STM32WB55)
- Added `bt_gatt_cb` callback with `att_mtu_updated` handler (36bf75015a, 3e12e28d08)
- Limitation: Runtime `ble.config(mtu=X)` not supported (compile-time only)

## BLE Perf Test Fixes (RP2 Pico W)
- `perf_gatt_notify.py`: raw ATT PDU fallback for non-local handles
- `perf_l2cap.py`: fixed L2CAP SDU buffer sizing, increased CONFIG_BT_L2CAP_TX_MTU to 512, listen-before-advertise race (c0db699afa)

## STM32WB55 SRAM1 Corruption on BLE Deinit
- `rfcore_ble_reset()` in `mp_bluetooth_hci_uart_deinit()` sent HCI_Reset to CPU2 (M0+ RF coprocessor) which corrupted all 192KB of SRAM1 during reinit
- The Zephyr host also sent HCI_Reset via `bt_enable()` → `common_init()` → `hci_reset()`
- Fixed: Removed `rfcore_ble_reset()` from deinit path (controller reset only during init). Added `drv_quirk_no_reset()` returning true for `__ZEPHYR_BLE_STM32_PORT__` builds in `lib/zephyr/subsys/bluetooth/host/hci_core.c`
- Also added IPCC NVIC IRQ disable during teardown/deinit to prevent post-deinit callbacks from CPU2, re-enabled during setup/init
- Commit: 8fb70be3e8

## Scheduler Node NULL Callback Crash on BLE Deinit
- `mp_bluetooth_zephyr_poll_cleanup()` set `sched_node.callback = NULL` while the node was already enqueued in the scheduler queue
- `mp_sched_run_pending()` dequeued the node, loaded callback (NULL), called `callback(node)` → HardFault at PC=0x00000000 (CFSR=INVSTATE)
- Affected all ports using the Zephyr BLE poll infrastructure, observed on STM32WB55
- Fixed: Removed NULL assignment from `poll_cleanup()`. Added `mp_bluetooth_is_active()` guard to the weak default `run_task` and the nRF port override (STM32 and RP2 already had guards)
- Commit: bcc9d5198a
