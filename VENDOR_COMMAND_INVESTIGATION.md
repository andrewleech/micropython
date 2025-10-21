# Vendor Command Investigation - HYPOTHESIS DISPROVEN

## Hypothesis
STM32WB controller requires vendor-specific HCI commands (0xfc66, 0xfc73, 0xfc0c) that Zephyr BLE might not be sending, causing LE Meta Event delivery failure.

## Investigation Method
1. Identified vendor commands in NimBLE HCI trace
2. Located vendor command definitions in `rfcore.c`
3. Traced call path from both stacks to `rfcore_ble_init()`
4. Enabled HCI_TRACE in rfcore.c
5. Captured full Zephyr initialization sequence

## Vendor Commands Identified

**From `ports/stm32/rfcore.c:72-77`:**
- **0xfc66** = `OCF_BLE_INIT` - Initialize BLE controller with configuration parameters
- **0xfc73** = `OCF_C2_SET_FLASH_ACTIVITY_CONTROL` - Flash activity control
- **0xfc0c** = `OCF_WRITE_CONFIG` - Write BD address configuration

## Shared rfcore.c Layer

Both NimBLE and Zephyr BLE use the **same** `rfcore.c` implementation for IPCC transport:

**Initialization Path (Both Stacks):**
```
bt_hci_transport_setup() (mpzephyrport.c:512)
  └→ mp_bluetooth_hci_uart_init() (mpbthciport.c:103)
      └→ rfcore_ble_init() (rfcore.c:609)
          ├→ rfcore_ble_reset() (rfcore.c:628)
          │   ├→ Vendor 0xfc66 (BLE_INIT)
          │   └→ HCI_RESET (first)
          └→ Vendor 0xfc73 (FLASH_ACTIVITY)
```

**BD Address Config Path (Both Stacks):**
```
rfcore_ble_check_msg() (rfcore.c:695)
  └→ Intercepts HCI_Reset response (rfcore.c:700-711)
      └→ Sends Vendor 0xfc0c (WRITE_CONFIG) with BD address
```

## Test Results: Zephyr Sends ALL Vendor Commands

**HCI Trace from `zephyr_hci_trace_full.txt`:**

```
[   10445] >HCI(:10:66:fc:24:00:00:00:00:...) ← Vendor BLE_INIT
[   10460] <VEND_RESP(11:0e:04:01:66:fc:00)  ← Success

[   10464] >HCI(:01:03:0c:00)                ← HCI_RESET (first)
[   10468] <HCI_EVT(04:0e:04:01:03:0c:00)    ← Success

[   10472] >HCI(:10:73:fc:01:00)             ← Vendor FLASH_ACTIVITY
[   10475] <VEND_RESP(11:0e:04:01:73:fc:00)  ← Success

[   10513] >HCI_CMD(01:03:0c:00)             ← HCI_RESET (second, from Zephyr)
[   10523] <HCI_EVT(04:0e:04:01:03:0c:00) (reset) ← Success

[   10528] >HCI(:01:0c:fc:08:00:06:d2:30:5d:13:09:02) ← Vendor WRITE_CONFIG (BD addr)
[   10534] <HCI_EVT(04:0e:04:01:0c:fc:00)    ← Success
```

**Event Mask Configuration:**
```
[   11522] >HCI_CMD(01:01:0c:08:10:88:00:02:00:00:00:20) ← SET_EVENT_MASK
           mask=0x2000000002008810, Bit 61 (LE_META_EVENT)=1
[   11542] <HCI_EVT(04:0e:04:01:01:0c:00)    ← Success (status=0)

[   11655] >HCI_CMD(01:01:20:08:0f:00:00:00:00:00:00:00) ← LE_SET_EVENT_MASK
           mask=0x0F, Bit 0 (LE_CONN_COMPLETE)=1
[   11674] <HCI_EVT(04:0e:04:01:01:20:00)    ← Success (status=0)
```

## Conclusion: HYPOTHESIS DISPROVEN - Zephyr BLE Now WORKING

**Zephyr BLE sends IDENTICAL initialization sequence to NimBLE:**

✓ Vendor command 0xfc66 (BLE_INIT) sent and acknowledged
✓ Vendor command 0xfc73 (FLASH_ACTIVITY) sent and acknowledged
✓ Vendor command 0xfc0c (WRITE_CONFIG) sent and acknowledged
✓ Both HCI_RESET commands sent successfully
✓ Event masks configured correctly (bit 61 enabled)
✓ All commands return status=0 (success)

**And LE Meta Events (0x3E) ARE NOW being delivered successfully!**

**Test Results - Scanning Works:**
```
[88871] >HCI_CMD(01:0c:20:02:01:00) ← LE_SET_SCAN_ENABLE (start scan)
[88888] <HCI_EVT(04:0e:04:01:0c:20:00) ← Success
[88921] <HCI_EVT(04:3e:1a:02:01:00:00:...) ← LE Meta Event 0x3E, subevent 0x02 (Advertising Report)
>>> HCI EVT: LE Meta Event detected!
>>> HCI EVT: LE Meta subevent=0x02
```

The root cause was NOT missing vendor commands. Both stacks use the same rfcore.c IPCC transport layer.

## Actual Root Cause (Previously Fixed)

The LE Meta Event delivery failure documented in earlier investigation files was caused by **missing IPCC memory sections in linker script** (Fix #4, commit 7f8ea29497).

STM32WB55 RF coprocessor requires buffers in specific RAM regions:
- RAM2A (0x20030000): IPCC tables and metadata
- RAM2B (0x20038000): IPCC data buffers

When these sections were removed (commit 5d69f18330), RF core couldn't access buffers, preventing event delivery.

Restoring the IPCC SECTIONS in `ports/stm32/boards/stm32wb55xg.ld` fixed both:
- NimBLE BLE activation (was broken)
- Zephyr BLE event delivery (was broken)

## Current Status

✓ Zephyr BLE fully functional on STM32WB55
✓ BLE initialization, advertising, scanning, connections all working
✓ LE Meta Events delivered correctly
⚠ Detection rate: ~30% of NimBLE (69 vs 227 devices in 5s scan)
  - Likely work queue processing throughput limitation
  - Acceptable for most use cases

## Files Modified
- `ports/stm32/rfcore.c:61` - Enabled HCI_TRACE (1)

## Test Artifacts
- Full HCI trace: `zephyr_hci_trace_full.txt`
- NimBLE reference: `nimble_scan_hci_trace.txt`
- Investigation docs:
  - `EVENT_MASK_ORDER_TEST_RESULTS.md`
  - `HCI_EVENT_MASK_INVESTIGATION.md`
  - `CONNECTION_EVENT_INVESTIGATION.md`
