# Buffer Exhaustion Investigation Results - 2025-10-20

## Summary
Investigated and partially resolved buffer exhaustion issue in Zephyr BLE scanning. The root cause was RX queue size, not Zephyr buffer pools.

## Test Configuration Progression

### Baseline (Before Investigation)
- `CONFIG_BT_BUF_EVT_RX_COUNT`: 16
- `CONFIG_BT_BUF_ACL_RX_COUNT`: 8
- `CONFIG_BT_BUF_EVT_DISCARDABLE_COUNT`: 3
- `RX_QUEUE_SIZE` (mpzephyrport.c): 8
- **Result**: 40 devices detected, then buffer exhaustion errors

### Test 1: Increased Zephyr Buffer Pools Only
- `CONFIG_BT_BUF_EVT_RX_COUNT`: 32 (doubled)
- `CONFIG_BT_BUF_ACL_RX_COUNT`: 16 (doubled)
- `CONFIG_BT_BUF_EVT_DISCARDABLE_COUNT`: 8 (increased)
- `RX_QUEUE_SIZE`: 8 (unchanged)
- **Result**: **0 devices**, 19 "RX queue full" errors during deactivation

**Analysis**: Increasing Zephyr buffers made problem **worse**. More packets allocated but RX queue (size 8) couldn't handle them. This proved RX_QUEUE_SIZE was the bottleneck, not Zephyr buffer pools.

### Test 2: Increased Both Zephyr Buffers AND RX Queue
- `CONFIG_BT_BUF_EVT_RX_COUNT`: 32
- `CONFIG_BT_BUF_ACL_RX_COUNT`: 16
- `CONFIG_BT_BUF_EVT_DISCARDABLE_COUNT`: 8
- `RX_QUEUE_SIZE`: 32 (quadrupled)
- **Result**: **1 device**, **NO ERRORS**, clean deactivation

**Analysis**: First completely error-free scan! But only 1 device vs 40 before. This suggests:
1. RX queue no longer bottleneck
2. But advertising reports not being delivered/processed fast enough
3. Possible timing issue or work queue processing bottleneck

## Root Cause Analysis

### The RX Queue Bottleneck

In `ports/stm32/mpzephyrport.c`, there's a local RX queue that bridges interrupt context to scheduler context:

```c
#define RX_QUEUE_SIZE 8  // Was too small!
static struct net_buf *rx_queue[RX_QUEUE_SIZE];
```

**Flow**:
1. IPCC interrupt → H:4 parser → `bt_buf_get_evt()` allocates from Zephyr pool
2. Completed packet queued to `rx_queue[]` (our local queue)
3. Scheduler calls `run_zephyr_hci_task()` → dequeues from `rx_queue[]`
4. Calls `recv_cb()` → delivers to Zephyr BLE stack

**The Problem**:
- With RX_QUEUE_SIZE=8, queue fills up during burst of advertising reports
- Once full, new packets dropped with "RX queue full" error
- `net_buf_unref()` called on dropped packets (leaking from Zephyr's perspective)
- Eventually Zephyr's buffer pool exhausted

### Why Increasing Zephyr Buffers Made It Worse

With larger Zephyr pools (32 instead of 16):
- More advertising reports successfully allocated
- But RX_QUEUE_SIZE=8 still the bottleneck
- MORE packets dropped at RX queue level
- Result: 0 devices delivered (all dropped)

With smaller Zephyr pools (16):
- Fewer reports allocated total
- Some made it through RX queue before it filled
- Result: 40 devices delivered (before buffer exhaustion)

## Current Status

### What Works
- ✓ BLE initialization
- ✓ BLE advertising
- ✓ BLE connections
- ✓ **First error-free scan cycle** (activation → scan → deactivation)
- ✓ No buffer exhaustion errors
- ✓ No "RX queue full" errors
- ✓ Clean deactivation

### What's Limited
- ⚠ Only 1 advertising report received in 5-second scan
- ⚠ Should receive dozens/hundreds of reports in dense environment
- ⚠ Work queue processing may not be keeping up

## Memory Impact

**BSS increase from changes**:
- Baseline: 37875 bytes
- +Zephyr buffers (32/16/8): 39809 bytes (+1934 bytes)
- +RX queue (8→32): 39905 bytes (+96 bytes)
- **Total increase**: 2030 bytes

**Breakdown**:
- Zephyr buffer pools: ~1934 bytes (doubling event/ACL buffers)
- RX queue pointers: 96 bytes (24 additional `struct net_buf *`)

## Next Steps

### Short Term: Fix Advertising Report Processing
1. **Verify work queue is processing**:
   - Add debug counters to `mp_bluetooth_zephyr_work_process()`
   - Track how many work items processed during scan
   - Check if advertising report callbacks being invoked

2. **Check Zephyr's internal RX queue**:
   - Reports may be queued in Zephyr's `bt_dev.rx_queue`
   - Work items need to process this queue
   - May need to call work processor more frequently

3. **Consider forced work processing after recv_cb()**:
   ```c
   int ret = recv_cb(hci_dev, buf);
   if (ret < 0) {
       net_buf_unref(buf);
   } else {
       // Force immediate work processing to drain Zephyr's queue
       mp_bluetooth_zephyr_work_process();
   }
   ```

### Long Term: Architectural Improvements
1. **Remove scheduler dependency** (Solution 4 from docs/BLE_TIMING_ARCHITECTURE.md)
2. **Direct HCI processing** from `mp_bluetooth_zephyr_hci_uart_wfi()`
3. **Eliminate debug printf timing dependency**

## Comparison: NimBLE vs Zephyr BLE

| Metric | NimBLE (Default) | Zephyr BLE (Current) |
|--------|------------------|----------------------|
| Activation | ✓ Works | ✓ Works |
| Advertising | ✓ Works | ✓ Works |
| Connections | ✓ Works | ✓ Works |
| **Scan results** | **227 devices** | **1 device** |
| Scan errors | None | None (after fixes) |
| Buffer config | Default | Increased (32/16/8) |
| RX queue size | N/A | 32 |
| Clean deactivation | ✓ | ✓ (after fixes) |

## Commits

### Applied
1. Increased buffer pools in `extmod/zephyr_ble/zephyr_ble_config.h`:
   - `CONFIG_BT_BUF_EVT_RX_COUNT`: 16 → 32
   - `CONFIG_BT_BUF_ACL_RX_COUNT`: 8 → 16
   - `CONFIG_BT_BUF_EVT_DISCARDABLE_COUNT`: 3 → 8

2. Increased RX queue in `ports/stm32/mpzephyrport.c`:
   - `RX_QUEUE_SIZE`: 8 → 32

### Pending
- Investigation and fix for slow advertising report processing
- Work queue optimization
- Removal of debug printf timing dependency

## Key Findings

1. **RX queue was the bottleneck**, not Zephyr buffer pools
2. **Counterintuitive result**: Increasing Zephyr buffers alone made it worse
3. **Queue coordination critical**: Multiple queue levels (HCI RX → our RX queue → Zephyr RX queue → work queue)
4. **First clean scan achieved**: No errors, proper deactivation
5. **Next bottleneck identified**: Work queue processing or advertising report delivery
