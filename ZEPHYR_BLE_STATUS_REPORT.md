# Zephyr BLE Integration for MicroPython - Status Report

**Date**: 2025-12-05
**Branch**: `zephy_ble`
**Base**: `master`
**Commits**: 90 commits ahead of master
**Files Changed**: 545 files (+17,105 / -9,184 lines)

---

## Goal

Add Zephyr BLE host stack as an alternative to NimBLE/BTstack for all MicroPython ports. This enables using Zephyr's BLE implementation with MicroPython's cooperative runtime, targeting boards where Zephyr's BLE stack may be more suitable or where NimBLE has limitations.

---

## Architecture

The implementation creates an OS abstraction layer (`extmod/zephyr_ble/hal/`) that maps Zephyr kernel primitives to MicroPython's cooperative scheduler:

- **k_sem** - Semaphores mapped to polling loops with scheduler yields
- **k_mutex** - Mutexes using MicroPython's GIL (single-threaded)
- **k_work** - Work queues integrated with MicroPython's scheduler
- **atomic ops** - Compiler builtins or port-specific implementations

Key integration layer: `ports/stm32/mpzephyrport.c` bridges IPCC hardware interrupts (HCI transport) to MicroPython's task scheduling.

```
┌─────────────────────────────────────────────────────────────────┐
│                     STM32WB55 Dual Core MCU                     │
├──────────────────────────────┬──────────────────────────────────┤
│   Cortex-M4 (Application)    │   Cortex-M0+ (Wireless)          │
│                              │                                   │
│  ┌────────────────────────┐  │  ┌────────────────────────────┐  │
│  │   MicroPython VM       │  │  │  BLE Controller Firmware   │  │
│  │  (Cooperative Sched)   │  │  │  (Vendor-provided)         │  │
│  └────────┬───────────────┘  │  └────────┬───────────────────┘  │
│           │                  │           │                      │
│  ┌────────▼───────────────┐  │  ┌────────▼───────────────────┐  │
│  │  Zephyr BLE Host Stack │  │  │  BLE Radio Stack           │  │
│  │  (hci_core, id, etc.)  │  │  │  (HCI command processor)   │  │
│  └────────┬───────────────┘  │  └────────┬───────────────────┘  │
│           │                  │           │                      │
│  ┌────────▼───────────────┐  │           │                      │
│  │  mpzephyrport.c        │  │           │                      │
│  │  (Integration Layer)   │◄─┼───────────┘                      │
│  └────────────────────────┘  │      IPCC Hardware               │
│                              │  (Mailbox + Interrupts)          │
└──────────────────────────────┴──────────────────────────────────┘
```

---

## Current Status

### STM32 NUCLEO_WB55

#### NimBLE (Default Variant) - FULLY WORKING

All BLE functionality operational:
- Initialization, advertising, scanning (227 devices/5s)
- Connections (peripheral + central roles)
- All IRQ events delivered correctly

#### Zephyr Variant (`BOARD_VARIANT=zephyr`)

| Feature | Status | Notes |
|---------|--------|-------|
| Initialization | ✓ Working | `bt_enable()` completes |
| Advertising | ✓ Working | Legacy mode |
| Scanning | ✓ Working | 69 devices/5s (~30% of NimBLE) |
| Peripheral Role | ⚠ Partial | **Issue #6**: Callbacks not invoked |
| Central Role | ✗ Stub | Returns `EOPNOTSUPP` |
| GATT Server | ✓ Working | Service registration, notify/indicate |
| GATT Client | ✗ Stub | Returns `EOPNOTSUPP` |
| Pairing/Bonding | ✗ Stub | Returns `EOPNOTSUPP` |

---

## Resolved Issues (7 Major Fixes)

### Fix #1: HOST_BUFFER_SIZE Command Incompatibility
- **Problem**: STM32WB controller returned error 0x12 when Zephyr sent HOST_BUFFER_SIZE command
- **Solution**: Disabled `CONFIG_BT_HCI_ACL_FLOW_CONTROL` (use `#undef`, not `#define 0`)

### Fix #2: BLE Scanning EPERM Error
- **Problem**: `ble.gap_scan()` returned `OSError: [Errno 1] EPERM`
- **Root Cause**: Zephyr attempted to set random address while advertising active
- **Solution**: Enabled `CONFIG_BT_SCAN_WITH_IDENTITY` to use identity address for scanning

### Fix #3: Connection Events Not Delivered
- **Problem**: No `_IRQ_CENTRAL_CONNECT` events received
- **Root Cause**: STM32WB sends legacy LE Connection Complete events, but Zephyr requested enhanced events when `CONFIG_BT_SMP=1`
- **Solution**: Disabled `CONFIG_BT_SMP`

### Fix #4: IPCC Memory Sections
- **Problem**: BLE activation failed with ETIMEDOUT on both stacks
- **Root Cause**: Linker script missing IPCC SECTIONS, buffers placed in wrong RAM region
- **Solution**: Restored IPCC SECTIONS to `stm32wb55xg.ld` placing buffers in RAM2A/RAM2B

### Fix #5: RX Queue Bottleneck
- **Problem**: Buffer exhaustion during scanning, only 40 devices detected
- **Root Cause**: `RX_QUEUE_SIZE=8` insufficient for burst of advertising reports
- **Solution**: Increased `RX_QUEUE_SIZE` to 32

### Fix #6: Zephyr Buffer Pools
- **Problem**: Continued buffer allocation failures
- **Solution**: Increased `CONFIG_BT_BUF_EVT_RX_COUNT` (16→32), `CONFIG_BT_BUF_ACL_RX_COUNT` (8→16), `CONFIG_BT_BUF_EVT_DISCARDABLE_COUNT` (3→8)

### Fix #7: Recursion Prevention Deadlock
- **Problem**: HCI command/response flow deadlocked
- **Root Cause**: Work queue recursion prevention blocked processing during semaphore waits
- **Solution**: Added `in_wait_loop` flag, fixed H4 buffer format, added work processing to WFI function

---

## Open Issue

### Issue #6: Connection Callbacks Not Invoked (BLOCKING)

**Symptoms**: When STM32WB55 acts as peripheral, `_IRQ_CENTRAL_CONNECT` and `_IRQ_CENTRAL_DISCONNECT` events never fire despite:
- Callbacks properly registered with `bt_conn_cb_register()`
- HCI connection events received and processed by Zephyr
- Central device (PYBD/NimBLE) successfully connects

**Evidence**:
- HCI LE Connection Complete received and enqueued
- Event dequeued from RX queue and passed to Zephyr
- Zephyr processed event (took ~6ms)
- `mp_bt_zephyr_connected()` callback NEVER invoked
- PYBD (central) received `_IRQ_PERIPHERAL_CONNECT` (connection worked)

**Root Cause Hypothesis**: `find_pending_connect()` in Zephyr's `hci_core.c` returns NULL for peripheral connections, causing early return before callback invocation. Possibly related to address matching logic where peripheral uses `BT_ADDR_LE_NONE` but HCI event contains central's actual address.

**Investigation Status**: Source code analysis exhausted. Runtime debugging with GDB recommended as next step.

**Impact**: Multi-test `ble_gap_connect.py` fails. Cannot track incoming connections, preventing reliable peripheral role operation.

---

## Phase 1 Limitations (By Design)

The implementation is explicitly "Phase 1: Server-only":

- Central role (initiating connections) not implemented
- GATT Client operations not implemented
- Pairing/bonding not implemented
- All return `EOPNOTSUPP` when called

---

## Memory Impact

- BSS increase: ~2KB (buffer pools + RX queue)
- Build system: CMake and Make integration for Zephyr variant

---

## Files Added/Modified

### New Directories
- `extmod/zephyr_ble/` - Main implementation (57KB modbluetooth_zephyr.c, HAL layer, config)
- `ports/stm32/boards/NUCLEO_WB55/mpconfigvariant_zephyr.mk` - Zephyr variant build config

### Key Files
- `ports/stm32/mpzephyrport.c` - HCI integration layer
- `ports/stm32/boards/stm32wb55xg.ld` - IPCC memory sections restored
- `extmod/zephyr_ble/zephyr_ble_config.h` - All Kconfig defines

---

## Test Commands

```bash
# Build Zephyr variant
cd ports/stm32
make BOARD=NUCLEO_WB55 BOARD_VARIANT=zephyr

# Run multi-test (WB55 as peripheral, PYBD as central)
cd tests
./run-multitests.py -t a1 -t a0 multi_bluetooth/ble_gap_connect.py
```

---

# Threading Analysis

## The Core Architectural Problem

Zephyr's BLE stack assumes a preemptive RTOS with:
- Real threads that can block on semaphores
- Dedicated work queue threads running continuously
- Interrupt handlers that wake blocked threads immediately

We're emulating this on MicroPython's cooperative scheduler where:
- Only one execution context exists
- "Blocking" semaphores must poll in a loop
- Work items only run when Python code yields
- HCI response processing depends on scheduler timing

## Evidence of Threading/Timing Issues

1. **Debug printf "timing magic"** - Initialization only works with SEM debug printfs enabled, which add ~15-18ms delays per iteration
2. **Scan stop timeouts** - HCI commands work during init but fail during active scanning
3. **Issue #6** - Callbacks registered correctly but never invoked, suggesting execution ordering problems
4. **Multiple timing-related fixes** - Fixes #5, #6, #7 all addressed timing/buffering issues

## Would Real Threading Help?

| Problem | Current Workaround | With Real Threads |
|---------|-------------------|-------------------|
| Semaphore waits | Poll loop + scheduler yield | Actual thread block/wake |
| Work queue | Scheduled tasks (opportunistic) | Dedicated thread |
| HCI response timing | Debug printf delays | Immediate wake on interrupt |
| Issue #6 callbacks | Unknown timing/ordering issue | Deterministic execution order |

**Assessment**: Real threading would likely solve Issue #6 and eliminate timing fragility.

## Costs of Real Threading

### Memory Overhead
```
Per thread stack: 2-8KB (embedded minimum)
3 Zephyr threads (rx, tx, work): 6-24KB additional RAM
STM32WB55 has 256KB RAM - manageable but not free
```

### Complexity
- Thread synchronization bugs are hard to debug on embedded
- MicroPython's GIL still serializes Python execution
- Need careful locking around shared state
- GC interactions with threads running C code

### Port Compatibility
- Not all MicroPython ports support threading
- STM32 port has `_thread` module but it's optional
- Would limit where Zephyr BLE could be used

## Proposed Solution: Single HCI Thread

A middle ground - one dedicated thread just for HCI RX processing:

```c
// In ports/stm32/mpzephyrport.c

static void *hci_rx_thread(void *arg) {
    while (hci_thread_running) {
        // Block on IPCC semaphore (real OS blocking)
        mp_thread_sem_take(&ipcc_rx_sem, MP_THREAD_SEM_MAX_TIMEOUT);

        // Process all queued HCI packets
        while (rx_queue_head != rx_queue_tail) {
            struct net_buf *buf = rx_queue[rx_queue_tail];
            rx_queue_tail = (rx_queue_tail + 1) % RX_QUEUE_SIZE;

            // Call Zephyr's receive callback
            if (recv_cb) {
                recv_cb(buf);
            }
        }
    }
    return NULL;
}

// IPCC interrupt handler
void IPCC_C1_RX_IRQHandler(void) {
    // ... read packet into rx_queue ...

    // Wake the HCI thread (instead of scheduling task)
    mp_thread_sem_give(&ipcc_rx_sem);
}

// Initialization
int mp_bluetooth_zephyr_port_init(void) {
    mp_thread_sem_init(&ipcc_rx_sem);
    mp_thread_create(hci_rx_thread, NULL, &hci_thread_stack, HCI_THREAD_STACK_SIZE);
    // ...
}
```

### Benefits
- Eliminates scheduler dependency for HCI reception
- HCI responses processed immediately when they arrive
- Keeps complexity contained to one thread
- Does not require full Zephyr threading model
- Cost: ~2-4KB stack

### Implementation Effort
- Estimated: 1-2 days
- Uses existing MicroPython threading primitives
- Minimal changes to Zephyr integration code

## Alternative: Full Zephyr Threading Model

Implement all Zephyr kernel threading primitives with real threads:

```c
// k_thread mapped to mp_thread
struct k_thread {
    mp_thread_t handle;
    void *stack;
    size_t stack_size;
    // ...
};

// k_sem with real blocking
int k_sem_take(struct k_sem *sem, k_timeout_t timeout) {
    return mp_thread_sem_take(&sem->sem, timeout);
}
```

### Benefits
- Full compatibility with Zephyr's expected execution model
- Would enable all Zephyr BLE features
- More maintainable long-term

### Costs
- Estimated: 1-2 weeks implementation
- Higher memory usage (multiple thread stacks)
- More complex debugging
- Port compatibility concerns

## Recommendation

| Path | Effort | Risk | Outcome |
|------|--------|------|---------|
| Continue debugging Issue #6 | Unknown | High | May find workaround, may not |
| Single HCI thread | 1-2 days | Low | Likely solves timing issues |
| Full Zephyr threading | 1-2 weeks | Medium | Robust long-term solution |

**For production use**: Implement single HCI thread as immediate fix, consider full threading for Phase 2.

**For experimental/development**: Continue with current approach, use GDB to diagnose Issue #6.

---

## Next Steps

1. **Immediate**: Decide on threading approach
2. **If single HCI thread**: Implement and test against Issue #6
3. **If continue current path**: GDB runtime debugging of `find_pending_connect()`
4. **Later**: Test on RP2 Pico 2 W (CYW43 BT controller)
5. **Phase 2**: Central role, GATT client, pairing (requires threading decision)

---

## References

- `docs/BLE_TIMING_ARCHITECTURE.md` - Detailed timing flow analysis
- `docs/ZEPHYR_BLE_PHASE1_LIMITATIONS.md` - Phase 1 scope documentation
- `docs/BUFFER_FIX_VERIFICATION.md` - Buffer tuning results
- `ISSUE_6_FINAL_ANALYSIS.md` - Connection callback investigation
- `ISSUE_6_ROOT_CAUSE_SUMMARY.md` - Root cause hypotheses
