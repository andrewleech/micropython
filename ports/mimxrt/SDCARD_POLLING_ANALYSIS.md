# SD Card Polling Mode Analysis - MIMXRT Port

## Objective

Implement SD card transfers with **scheduler yields during polling loops** (`mp_event_handle_nowait()`) to allow MicroPython background tasks to run during SD card operations, while avoiding cache coherency issues that cause data corruption when SDRAM is disabled.

## Background

When SDRAM is disabled on MIMXRT boards (e.g., SEEED_ARCH_MIX), the MicroPython GC heap resides in OCRAM. This creates cache coherency challenges for DMA transfers because OCRAM is cacheable memory.

The existing transfer modes are:
1. **SDK Blocking** (`USDHC_TransferBlocking`) - Works correctly but does tight polling with no scheduler yields
2. **Non-blocking/Interrupt** (`USDHC_TransferNonBlocking` + callbacks) - Has cache coherency issues causing data corruption

## Requirement

The polling implementation must call `mp_event_handle_nowait()` **during the actual SD card transfer polling loops** to allow the MicroPython scheduler to run background tasks.

## Approaches Attempted

### Approach 1: Manual Register Configuration with HW Flag Polling

**Implementation:**
- Call `USDHC_SetAdmaTableConfig()` for DMA setup
- Manually configure BLK_ATT and MIX_CTRL registers
- Call `USDHC_SendCommand()`
- Poll `kUSDHC_CommandFlag` and `kUSDHC_DataDMAFlag` with `mp_event_handle_nowait()` yields

**Result:** Intermittent EIO errors on larger transfers (37KB+)

**Analysis:**
The manual register configuration conflicted with SDK internal state:
- We set BLK_ATT after `USDHC_SetAdmaTableConfig()`, but SDK expects it set by `USDHC_SetTransferConfig()`
- Our MIX_CTRL manipulation cleared AC23EN unconditionally, while SDK only clears it if NOT using auto CMD23
- The order of register writes matters and our sequence didn't match SDK's expectations

**Key discrepancy from SDK's `USDHC_TransferBlocking` flow:**
```
SDK:
1. USDHC_SetAdmaTableConfig() - sets up ADMA, ADMA_SYS_ADDR, PROT_CTRL, enables DMAEN
2. USDHC_SetTransferConfig() - configures MIX_CTRL (preserving DMAEN), BLK_ATT
3. USDHC_SendCommand()
4. USDHC_WaitCommandDone() - polls kUSDHC_CommandFlag
5. USDHC_TransferDataBlocking() - polls kUSDHC_DataDMAFlag

Our attempt:
1. USDHC_SetAdmaTableConfig() - correct
2. Manually set BLK_ATT - redundant/conflicting
3. Manually configure MIX_CTRL - conflicting with SDK internal logic
4. USDHC_EnableInternalDMA() - redundant
5. USDHC_SendCommand()
6. Poll - correct
```

### Approach 2: USDHC_TransferNonBlocking + Disable Interrupts + Poll HW Flags

**Implementation:**
- Call `USDHC_TransferNonBlocking()` for correct register setup
- Immediately call `USDHC_DisableInterruptSignal(base, kUSDHC_AllInterruptFlags)`
- Poll `kUSDHC_CommandFlag` and `kUSDHC_DataDMAFlag` with scheduler yields
- Read response manually with `sdcard_polling_receive_response()`

**Result:** Intermittent EIO errors persisted

**Analysis:**
`USDHC_TransferNonBlocking()` enables interrupt signals internally before we can disable them. The SDK's internal state machine expects interrupts to be handled by the ISR. Disabling signals after the transfer starts may leave the SDK in an inconsistent state.

Additionally, the response reading function `sdcard_polling_receive_response()` was a manual reimplementation that may have subtle differences from SDK's `USDHC_ReceiveCommandResponse()`.

### Approach 3: USDHC_TransferNonBlocking + Interrupt Callbacks + Poll transfer_status Flag

**Implementation:**
- Enable USDHC interrupt and register callback
- Call `USDHC_TransferNonBlocking()`
- Callback sets `card->state->transfer_status` flags
- Poll the `transfer_status` volatile flag with `mp_event_handle_nowait()` yields
- Cache invalidation in callback with DSB/ISB barriers

**Result:** Data corruption on reads (zeros where data should be)

**Analysis:**
The cache coherency issue persists because:
1. DMA writes to RAM (OCRAM which is cacheable)
2. Interrupt fires, ISR calls callback
3. Callback does DSB/ISB and sets completion flag
4. But between DMA completion and polling code reading the buffer, the CPU may have speculatively loaded stale cache lines

The DSB/ISB in the callback ensures the flag write is visible, but doesn't prevent the CPU from having already cached stale buffer data before the interrupt even fired.

The additional `MP_HAL_CLEANINVALIDATE_DCACHE()` after detecting completion should help, but the corruption pattern (zeros) suggests the issue is more fundamental - possibly related to how/when the cache lines get filled speculatively.

### Approach 4: SDK Blocking with Pre-transfer Scheduler Yields

**Implementation:**
- Poll Data0 line for card ready with `mp_event_handle_nowait()` yields
- Call `USDHC_TransferBlocking()` for the actual transfer

**Result:** Works correctly (no corruption) but **does not meet the requirement**

**Analysis:**
This approach yields only during the wait-for-ready phase, not during the actual DMA transfer polling. The `USDHC_TransferBlocking()` function does tight polling internally:

```c
// Inside USDHC_WaitCommandDone:
while (!(IS_USDHC_FLAG_SET(interruptStatus, kUSDHC_CommandFlag)))
{
    interruptStatus = USDHC_GetInterruptStatusFlags(base);
}

// Inside USDHC_TransferDataBlocking:
while (!(IS_USDHC_FLAG_SET(interruptStatus, kUSDHC_DataDMAFlag | kUSDHC_TuningErrorFlag)))
{
    interruptStatus = USDHC_GetInterruptStatusFlags(base);
}
```

No scheduler yields occur during these loops.

## Reference Implementation Analysis

The reference implementation from NXP bootloader (`fsl_host.c`):

```c
static status_t USDHC_TransferFunction(USDHC_Type *base, usdhc_transfer_t *content)
{
    // ... setup ...

    do {
        error = USDHC_TransferNonBlocking(base, &g_usdhcHandle, &dmaConfig, content);
    } while (error == kStatus_USDHC_BusyTransferring);

    if ((error != kStatus_Success) ||
        (false == EVENT_Wait(kEVENT_TransferComplete, EVENT_TIMEOUT_TRANSFER_COMPLETE)) ||
        (g_reTuningFlag) || (!g_usdhcTransferSuccessFlag))
    {
        // error handling
    }
    return error;
}
```

Key differences from our environment:
1. Uses FreeRTOS `EVENT_Wait()` which blocks the task and yields to RTOS scheduler
2. Bootloader likely has different memory layout (not MicroPython with GC heap in OCRAM)
3. May have SDRAM enabled or different cache configuration

## SDK Cache Issue (GitHub Issue #20)

Related issue: https://github.com/nxp-mcuxpresso/mcuxsdk-core/issues/20

The SDK has a bug where it unconditionally invalidates D-Cache after transfers, which can corrupt other data sharing cache lines with the DMA buffer. This affects non-DMA fallback paths and cases where DMA buffers aren't cache-line aligned.

While not directly causing our read corruption issue (we see zeros, not corrupted other data), it indicates cache handling in the USDHC driver is problematic.

## ERR050396 Workaround

We implemented the ERR050396 workaround by clearing ARCACHE/AWCACHE bits in GPR28/GPR13:

```c
#ifdef MIMXRT117x_SERIES
IOMUXC_GPR->GPR28 &= ~(IOMUXC_GPR_GPR28_ARCACHE_USDHC_MASK | IOMUXC_GPR_GPR28_AWCACHE_USDHC_MASK);
#else
IOMUXC_GPR->GPR13 &= ~(IOMUXC_GPR_GPR13_ARCACHE_USDHC_MASK | IOMUXC_GPR_GPR13_AWCACHE_USDHC_MASK);
#endif
```

This should make USDHC DMA transactions non-cacheable at the AXI bus level, but corruption still occurs with interrupt-based transfers.

## Root Cause Hypothesis

The cache corruption with interrupt-based polling likely stems from:

1. **Speculative cache fills:** Between DMA starting and completion interrupt, the CPU may speculatively fill cache lines from the buffer address. These speculative fills contain stale (pre-DMA) data.

2. **Cache invalidation timing:** Even with DSB/ISB barriers and explicit cache invalidation, if the CPU has already loaded the data into registers or there are outstanding speculative accesses, the invalidation may not fully clear stale data.

3. **OCRAM cache behavior:** When GC heap is in OCRAM (SDRAM disabled), the cache behavior may differ from when using SDRAM. The ERR050396 workaround may not fully address all cache coherency scenarios.

4. **Interrupt latency window:** The time between DMA completion and ISR execution creates a window where the CPU can access stale cached data.

## Why SDK Blocking Works

`USDHC_TransferBlocking()` works because:
1. It polls hardware registers in a tight loop with no interrupts
2. The polling itself acts as a synchronization point - CPU is not doing other memory accesses
3. No interrupt latency window exists
4. Cache invalidation (done by our code before/after) is effective because CPU hasn't speculatively accessed the buffer during the transfer

## Potential Solutions Not Yet Attempted

1. **Place DMA buffers in non-cacheable memory:** Allocate SD card buffers from a non-cacheable region instead of GC heap. This would require significant changes to the VFS layer.

2. **Disable D-Cache during transfers:** Call `SCB_DisableDCache()` before transfer and `SCB_EnableDCache()` after. Heavy-handed but might work.

3. **Use MPU to mark buffer region non-cacheable:** Dynamically configure MPU to make the specific buffer non-cacheable during transfer.

4. **Modify SDK to add yield points:** Fork/patch the SDK's `USDHC_WaitCommandDone()` and `USDHC_TransferDataBlocking()` to add `mp_event_handle_nowait()` calls in their polling loops.

5. **Copy-based approach:** Always DMA to/from a small non-cacheable bounce buffer, then memcpy to/from the user buffer. Performance impact but guaranteed cache coherency.

## Conclusion

Achieving SD card transfers with scheduler yields while avoiding cache corruption when SDRAM is disabled has proven challenging. The fundamental issue is that interrupt-based polling creates windows for cache coherency problems, while SDK blocking mode doesn't yield.

The most promising unexplored solutions are:
- Modifying the SDK directly to add yield points (invasive but direct)
- Using a non-cacheable bounce buffer (performance impact but clean)
- Disabling cache during transfers (heavy-handed but simple)

## Files Modified

- `ports/mimxrt/sdcard.c` - Multiple implementations attempted
- `ports/mimxrt/sdcard.h` - Added `transfer_status` field for callback-based polling

## Test Results Summary

| Approach | Scheduler Yields | Data Integrity | Status |
|----------|-----------------|----------------|--------|
| SDK Blocking | No | ✓ Works | Doesn't meet requirement |
| Manual register + HW poll | Yes | ✗ Intermittent EIO | Failed |
| NonBlocking + disable IRQ + HW poll | Yes | ✗ Intermittent EIO | Failed |
| NonBlocking + callback + poll flag | Yes | ✗ Data corruption | Failed |
| SDK Blocking + pre-yield | Partial (wait-ready only) | ✓ Works | Doesn't meet requirement |
