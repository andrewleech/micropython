# HCI Init Hang Analysis

## Problem

After fixing the net_buf allocation crash (see NET_BUF_CRASH_FIX.md), firmware now progresses into `hci_init()` but hangs during HCI command/response processing.

## Observed Behavior

```
Creating BLE object...
BLE_NEW: enter, bluetooth=0
BLE_NEW: allocating object
BLE_NEW: object at 20016f30
BLE_NEW: returning 20016f30
Activating BLE...
CHECKPOINT 1: bt_init() entered
CHECKPOINT 2: Calling hci_init()...
[FIFO] k_fifo_put(200014ec, 20014fa8)
[FIFO] k_fifo_get(200014ec, timeout=0)
<HANG>
```

**Key Observations:**
- ✓ Net_buf allocation working (crash fixed)
- ✓ bt_init() entry successful
- ✓ hci_init() called
- ✓ FIFO operations working (k_fifo_put/get)
- ✗ No further output after FIFO operations
- ✗ Device completely hung (no response to serial)

## Code Analysis

### Call Stack at Hang

```
mp_bluetooth_init()
└─ bt_enable()
   └─ bt_init()
      └─ hci_init()
         └─ common_init()
            └─ bt_hci_cmd_send_sync(BT_HCI_OP_RESET, ...)  ← LIKELY HANG HERE
```

### hci_init() Entry Point (lib/zephyr/subsys/bluetooth/host/hci_core.c:4170)

```c
static int hci_init(void)
{
	int err;

	#if defined(CONFIG_BT_HCI_SETUP)
	err = bt_hci_setup(bt_dev.hci, &setup_params);
	if (err && err != -ENOSYS) {
		return err;
	}
	#endif

	err = common_init();  // ← First operation in hci_init()
	if (err) {
		return err;
	}

	err = le_init();
	// ... more initialization
```

### common_init() First HCI Command (lib/zephyr/subsys/bluetooth/host/hci_core.c:3440)

```c
static int common_init(void)
{
	struct net_buf *rsp;
	int err;

	if (!drv_quirk_no_reset()) {
		/* Send HCI_RESET */
		err = bt_hci_cmd_send_sync(BT_HCI_OP_RESET, NULL, NULL);  // ← FIRST HCI COMMAND
		if (err) {
			return err;
		}
		hci_reset_complete();
	}

	/* Read Local Supported Features */
	err = bt_hci_cmd_send_sync(BT_HCI_OP_READ_LOCAL_FEATURES, NULL, &rsp);
	// ... more HCI commands
```

## Root Cause Hypothesis

The hang occurs during the first HCI command synchronous operation. The sequence is:

1. **Command Allocation**: `bt_hci_cmd_send_sync()` allocates a command buffer (using net_buf - now working)
2. **Command Send**: Command is sent via HCI driver to controller
3. **FIFO Operations**: Command queued to controller (we see `k_fifo_put()`)
4. **Wait for Response**: Code blocks on semaphore waiting for HCI response
5. **Response Processing**: HCI response should arrive, trigger interrupt, be processed via work queue
6. **Semaphore Release**: Work queue processes response and signals semaphore
7. **Command Complete**: `bt_hci_cmd_send_sync()` returns

**The hang suggests step 5 or 6 is failing** - either:
- Response arrives but isn't processed by work queue
- Work queue doesn't run or is blocked
- Semaphore never gets signaled
- ISR → work queue handoff is broken

## Relevant Code Sections

### HCI Command Send Sync (lib/zephyr/subsys/bluetooth/host/hci_core.c)

The synchronous command send uses a semaphore to wait for completion:

```c
int bt_hci_cmd_send_sync(uint16_t opcode, struct net_buf *buf, struct net_buf **rsp)
{
	struct bt_hci_cmd_state_set state;
	int err;

	if (!buf) {
		buf = bt_hci_cmd_alloc(opcode, 0);
		if (!buf) {
			return -ENOBUFS;
		}
	}

	LOG_DBG("opcode 0x%04x len %u", opcode, buf->len);

	// Set up synchronization
	state.opcode = opcode;
	state.rsp = rsp;
	k_sem_init(&state.sem, 0, 1);

	// Send command
	err = bt_hci_cmd_send(opcode, buf);
	if (err) {
		return err;
	}

	// Wait for response with timeout
	err = k_sem_take(&state.sem, HCI_CMD_TIMEOUT);  // ← LIKELY HANGS HERE
	if (err) {
		return err;
	}

	return state.status;
}
```

### Work Queue Processing

HCI responses are processed via work queue in `extmod/zephyr_ble/hal/zephyr_ble_work.c`. If this isn't running or is blocked, responses won't be processed and semaphores won't be signaled.

## Comparison with STM32WB55 Zephyr BLE

**STM32WB55 Status:**
- ✓ bt_enable() returns 0 (initialization completes)
- ✓ HCI commands work
- ⚠ Scanning partially works but times out on stop
- ⚠ Requires SEM debug printfs for timing

**Key Difference:**
- STM32WB55 uses IPCC (Inter-Processor Communication Controller) for HCI transport
- RP2 Pico W uses CYW43 BT controller via SPI
- Different HCI driver implementations may have different timing requirements

## Debugging Blocked by probe-rs Bug

Attempted GDB diagnosis but encountered probe-rs bug:

```
thread 'tokio-runtime-worker' panicked at probe-rs/src/architecture/arm/communication_interface.rs:472:18:
internal error: entered unreachable code: Did not expect to be called with FullyQualifiedApAddress { dp: Default, ap: V2(ApV2Address(Some(2000))) }. This is a bug, please report it.
```

This prevents:
- Attaching GDB to running device
- Resetting device via probe-rs
- Flashing new firmware via probe-rs

**Workaround Required:** Physical device reset or use USB bootloader for reflashing.

## Instrumentation Recommendations

To diagnose further without GDB, add debug output to:

### 1. HCI Command Send (lib/zephyr/subsys/bluetooth/host/hci_core.c)

```c
int bt_hci_cmd_send_sync(uint16_t opcode, struct net_buf *buf, struct net_buf **rsp)
{
	// ... setup code ...

	mp_printf(&mp_plat_print, "[HCI_CMD] Sending opcode=0x%04x\n", opcode);

	err = bt_hci_cmd_send(opcode, buf);
	if (err) {
		mp_printf(&mp_plat_print, "[HCI_CMD] Send failed: %d\n", err);
		return err;
	}

	mp_printf(&mp_plat_print, "[HCI_CMD] Waiting for response (timeout=%dms)...\n",
	          HCI_CMD_TIMEOUT);

	err = k_sem_take(&state.sem, HCI_CMD_TIMEOUT);
	if (err) {
		mp_printf(&mp_plat_print, "[HCI_CMD] Semaphore timeout: %d\n", err);
		return err;
	}

	mp_printf(&mp_plat_print, "[HCI_CMD] Response received, status=%d\n", state.status);
	return state.status;
}
```

### 2. HCI Response Receive (lib/zephyr/subsys/bluetooth/host/hci_core.c)

Find where HCI responses are received (likely `bt_recv()` or `hci_cmd_complete()`) and add:

```c
mp_printf(&mp_plat_print, "[HCI_RESP] Received opcode=0x%04x\n", opcode);
```

### 3. Work Queue Processing (extmod/zephyr_ble/hal/zephyr_ble_work.c)

```c
void k_work_submit(struct k_work *work)
{
	mp_printf(&mp_plat_print, "[WORK] Submitting work=%p handler=%p\n", work, work->handler);
	// ... existing code ...
}

void mp_bluetooth_zephyr_work_process(void)
{
	mp_printf(&mp_plat_print, "[WORK] Processing work queue...\n");
	// ... existing code ...
	mp_printf(&mp_plat_print, "[WORK] Work queue processing complete\n");
}
```

### 4. Semaphore Operations (extmod/zephyr_ble/hal/zephyr_ble_sem.c)

Already has DEBUG_SEM_printf - ensure it's enabled.

## Expected Output with Full Instrumentation

```
CHECKPOINT 2: Calling hci_init()...
[HCI_CMD] Sending opcode=0x0c03 (RESET)
[FIFO] k_fifo_put(200014ec, 20014fa8)
[HCI] Sent command to controller
[HCI_CMD] Waiting for response (timeout=10000ms)...

<-- Controller interrupt fires -->

[ISR] HCI response received
[WORK] Submitting work=... handler=...
[WORK] Processing work queue...
[HCI_RESP] Received opcode=0x0c03
[SEM] k_sem_give called
[WORK] Work queue processing complete

<-- Semaphore wait completes -->

[HCI_CMD] Response received, status=0
<continues to next HCI command>
```

If output stops at "Waiting for response", then:
- Response isn't arriving from controller, OR
- Response arrives but work queue doesn't run, OR
- Work queue runs but doesn't process the response

## Next Steps

1. **Add comprehensive HCI/work queue instrumentation** as described above
2. **Physical device reset** to recover from current hang (probe-rs not working)
3. **Reflash with instrumented firmware** via USB bootloader
4. **Capture full debug output** showing where response processing fails
5. **Compare with STM32WB55** working HCI trace to identify differences

## Alternative: FreeRTOS Integration

The hang may be due to fundamental timing/synchronization issues in the polling-based HAL implementation. Consider:

1. **Phase 2B (FreeRTOS HAL)**: Replace polling-based k_sem/k_work with real FreeRTOS primitives
2. **Service Task Framework**: Proper ISR → task handoff for HCI responses
3. **Work Queue Thread**: Dedicated thread for work queue processing

This would eliminate timing dependencies and provide proper blocking/waking semantics.

---

**Document Created**: 2025-12-22
**Related Issues**: Net_buf crash (FIXED), HCI init hang (INVESTIGATING)
**Files**: lib/zephyr/subsys/bluetooth/host/hci_core.c, extmod/zephyr_ble/hal/
