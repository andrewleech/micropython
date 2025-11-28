# USB-CDC Bulk Operations Implementation Plan

## Overview

Replace byte-by-byte processing in `tud_cdc_rx_cb()` with bulk operations to improve USB-CDC throughput from ~80 KB/s to potentially 500+ KB/s.

## Goals

1. Replace `tud_cdc_read_char()` + `ringbuf_put()` loop with bulk `tud_cdc_n_read()` + `ringbuf_put_bytes()`
2. Maintain interrupt character detection (`mp_interrupt_char`)
3. Maintain deferred processing when ringbuf is full (`cdc_itf_pending`)
4. Preserve all existing behavior and error handling

## Target File

**File:** `shared/tinyusb/mp_usbd_cdc.c`
**Function:** `void tud_cdc_rx_cb(uint8_t itf)` (lines 71-95)

## Current Implementation Analysis

### Current Code Flow

```c
void tud_cdc_rx_cb(uint8_t itf) {
    // Mark interface as not pending
    cdc_itf_pending &= ~(1 << itf);

    // Loop: for each byte available in TinyUSB buffer
    for (uint32_t bytes_avail = tud_cdc_n_available(itf); bytes_avail > 0; --bytes_avail) {
        // Check if ringbuf has space
        if (ringbuf_free(&stdin_ringbuf)) {
            // Read ONE byte from TinyUSB
            int data_char = tud_cdc_read_char();

            // Check for interrupt character
            #if MICROPY_KBD_EXCEPTION
            if (data_char == mp_interrupt_char) {
                // Clear ringbuf and schedule interrupt
                stdin_ringbuf.iget = stdin_ringbuf.iput = 0;
                mp_sched_keyboard_interrupt();
            } else {
                // Put ONE byte into ringbuf
                ringbuf_put(&stdin_ringbuf, data_char);
            }
            #else
            ringbuf_put(&stdin_ringbuf, data_char);
            #endif
        } else {
            // Ringbuf full - defer processing
            cdc_itf_pending |= (1 << itf);
            return;
        }
    }
}
```

### Issues with Current Implementation

1. **Function call overhead:** N calls to `tud_cdc_read_char()` + N calls to `ringbuf_put()`
2. **No bulk transfer:** TinyUSB can provide data in chunks (up to 64 bytes per USB packet)
3. **Inefficient memory access:** Multiple single-byte operations instead of bulk memory operations

## New Implementation Design

### Algorithm

```
1. While TinyUSB has data AND ringbuf has space:
   a. Calculate chunk size = MIN(temp buffer size, TinyUSB available, ringbuf free)
   b. Bulk read chunk from TinyUSB into temp buffer
   c. Scan temp buffer for interrupt character
   d. If interrupt char found:
      - Copy bytes BEFORE interrupt char to ringbuf (if any)
      - Clear ringbuf
      - Schedule keyboard interrupt
      - Discard remaining bytes (current behavior)
      - Return
   e. If no interrupt char:
      - Bulk copy entire chunk to ringbuf
   f. Continue loop
2. If TinyUSB still has data but ringbuf is full:
   - Mark interface as pending for later processing
```

### Key Design Decisions

**1. Temporary buffer size: 64 bytes**
- Matches typical USB packet size for full-speed CDC
- Reasonable stack usage
- Aligns with common packet boundaries

**2. Use `memchr()` for interrupt char detection**
- Standard C library function
- Optimized on most platforms
- Clear intent

**3. Interrupt character behavior**
- When found: clear ringbuf, schedule interrupt, discard all remaining bytes
- Matches current behavior exactly (bytes after interrupt char are lost)

**4. Deferred processing**
- Only mark pending if data remains AND ringbuf is full
- Matches current behavior

## Implementation Steps

### Step 1: Prepare Helper Function (if needed)

Check if `memchr()` is available. Include `<string.h>` if not already included.

**File:** `shared/tinyusb/mp_usbd_cdc.c`
**Location:** Top of file with other includes

```c
#include <string.h>  // For memchr()
```

### Step 2: Implement New `tud_cdc_rx_cb()`

**File:** `shared/tinyusb/mp_usbd_cdc.c`
**Function:** `void tud_cdc_rx_cb(uint8_t itf)` (lines 71-95)

**Replace entire function with:**

```c
void tud_cdc_rx_cb(uint8_t itf) {
    // Consume pending USB data immediately to free USB buffer and keep the endpoint from stalling.
    // In case the ringbuffer is full, mark the CDC interface that needs attention later on for polling.
    cdc_itf_pending &= ~(1 << itf);

    // Temporary buffer for bulk reads (matches USB packet size)
    uint8_t temp[64];

    while (tud_cdc_n_available(itf) > 0 && ringbuf_free(&stdin_ringbuf) > 0) {
        // Calculate chunk size: limited by temp buffer, USB available, and ringbuf space
        uint32_t chunk = tud_cdc_n_available(itf);
        if (chunk > sizeof(temp)) {
            chunk = sizeof(temp);
        }
        uint32_t free = ringbuf_free(&stdin_ringbuf);
        if (chunk > free) {
            chunk = free;
        }

        // Bulk read from TinyUSB
        uint32_t got = tud_cdc_n_read(itf, temp, chunk);
        if (got == 0) {
            break;  // No data available (shouldn't happen, but defensive)
        }

        #if MICROPY_KBD_EXCEPTION
        // Scan for interrupt character
        uint8_t *intr_pos = memchr(temp, mp_interrupt_char, got);
        if (intr_pos != NULL) {
            // Found interrupt character
            size_t pre_len = intr_pos - temp;

            // Copy bytes before interrupt char (if any)
            if (pre_len > 0) {
                ringbuf_put_bytes(&stdin_ringbuf, temp, pre_len);
            }

            // Clear the ring buffer
            stdin_ringbuf.iget = stdin_ringbuf.iput = 0;

            // Schedule keyboard interrupt
            mp_sched_keyboard_interrupt();

            // Stop processing (discard remaining bytes in temp and USB buffer)
            return;
        }
        #endif

        // No interrupt char found - bulk copy to ringbuf
        ringbuf_put_bytes(&stdin_ringbuf, temp, got);
    }

    // If USB still has data but ringbuf is full, mark interface for later polling
    if (tud_cdc_n_available(itf) > 0) {
        cdc_itf_pending |= (1 << itf);
    }
}
```

## Implementation Details

### Memory Safety

1. **Temp buffer:** Stack-allocated 64-byte array (safe, small)
2. **Chunk calculation:** Ensures we never:
   - Read more than temp buffer can hold
   - Read more than TinyUSB has available
   - Write more than ringbuf has space for
3. **Bounds checking:** All operations use explicit sizes

### Interrupt Character Handling

The interrupt character logic must handle these cases:

**Case 1: Interrupt char NOT found**
- Entire chunk copied to ringbuf
- Continue processing

**Case 2: Interrupt char at start of chunk**
- `pre_len = 0`, nothing copied before interrupt
- Ringbuf cleared
- Interrupt scheduled
- Function returns (remaining USB data discarded)

**Case 3: Interrupt char in middle of chunk**
- Bytes before interrupt copied to ringbuf
- Ringbuf cleared
- Interrupt scheduled
- Function returns (remaining bytes in chunk AND USB buffer discarded)

**Case 4: Interrupt char at end of chunk**
- Bytes before interrupt copied to ringbuf
- Ringbuf cleared
- Interrupt scheduled
- Function returns

### MICROPY_KBD_EXCEPTION Guard

The `#if MICROPY_KBD_EXCEPTION` guard must wrap:
- The `memchr()` call
- The interrupt character handling logic
- NOT the bulk copy (that happens regardless)

When `MICROPY_KBD_EXCEPTION` is disabled:
- No interrupt checking
- Simplified to just bulk read + bulk write

### Error Handling

**If `tud_cdc_n_read()` returns 0:**
- Break out of loop
- Should not happen if `tud_cdc_n_available()` reported data
- Defensive programming

**If `ringbuf_put_bytes()` fails:**
- Currently `ringbuf_put_bytes()` does not return error codes
- It assumes the caller checked `ringbuf_free()` first
- We guarantee space by limiting `chunk` size

## Testing Requirements

### Unit Test Cases

**Test 1: Bulk transfer without interrupt char**
- Send 128 bytes via USB
- Verify all 128 bytes appear in ringbuf
- Verify no interrupt scheduled

**Test 2: Interrupt char at start**
- Send: `[\x03, A, B, C]`
- Verify ringbuf is empty
- Verify interrupt scheduled
- Verify A, B, C discarded

**Test 3: Interrupt char in middle**
- Send: `[A, B, \x03, C, D]`
- Verify A, B not in ringbuf (ringbuf cleared)
- Verify interrupt scheduled
- Verify C, D discarded

**Test 4: Interrupt char at end**
- Send: `[A, B, C, \x03]`
- Verify A, B, C not in ringbuf (ringbuf cleared)
- Verify interrupt scheduled

**Test 5: Ringbuf full scenario**
- Fill ringbuf to capacity - N bytes free
- Send M bytes where M > N
- Verify N bytes received
- Verify `cdc_itf_pending` set
- Poll again, verify remaining M-N bytes received

**Test 6: Large transfer (multi-packet)**
- Send 1024 bytes
- Verify all received correctly
- Verify processed in chunks

### Integration Test Cases

**Test 7: File transfer via mount**
- Copy 100KB file via mounted filesystem
- Measure throughput (expect >200 KB/s improvement)
- Verify file integrity (checksum)

**Test 8: Raw paste mode**
- Execute code via raw paste mode
- Verify correct execution
- Measure throughput improvement

**Test 9: REPL interaction**
- Type in REPL including Ctrl-C
- Verify Ctrl-C triggers interrupt correctly
- Verify no data loss

**Test 10: Multi-interface USB-CDC**
- If board has multiple CDC interfaces
- Send data on both simultaneously
- Verify correct routing

## Performance Validation

### Before/After Metrics

Measure on Raspberry Pi Pico (RP2040):

| Metric | Before (byte-by-byte) | After (bulk) | Target |
|--------|----------------------|--------------|---------|
| Mount FS read (50KB) | 72-83 KB/s | ? | >200 KB/s |
| Raw paste mode | 18-20 KB/s | ? | >40 KB/s |
| % of USB potential | 8% | ? | >20% |

### Profiling Points

If performance doesn't meet targets, profile:
1. Time in `tud_cdc_n_read()`
2. Time in `memchr()`
3. Time in `ringbuf_put_bytes()`
4. TinyUSB internal buffer management

## Compatibility Considerations

### Affected Ports

All ports using TinyUSB for USB-CDC:
- **rp2** (Raspberry Pi Pico)
- **samd** (SAMD21, SAMD51)
- **stm32** (boards with USB-CDC enabled)
- **mimxrt** (i.MX RT series)
- **nrf** (nRF52840 with USB)
- **renesas-ra** (RA series with USB)

### Build Testing Required

Test on at least:
- RP2 (primary test platform - Pico available)
- One other port (ideally STM32 or SAMD)

### Backward Compatibility

This change is transparent to Python code and does not affect:
- Python API
- Binary compatibility
- Frozen bytecode

Risk is minimal - only affects internal data transfer path.

## Rollback Plan

If issues discovered after merge:

1. **Revert is simple:** Single function change in one file
2. **Git revert:** `git revert <commit-hash>`
3. **Testing:** Same test suite validates rollback

## Code Review Checklist

- [ ] Code follows MicroPython style (see `CODECONVENTIONS.md`)
- [ ] Formatted with `tools/codeformat.py`
- [ ] No new compiler warnings
- [ ] `#if MICROPY_KBD_EXCEPTION` guards correct
- [ ] Memory safety verified (no buffer overruns)
- [ ] Interrupt character behavior matches original exactly
- [ ] `cdc_itf_pending` logic matches original
- [ ] Comments explain bulk operation logic
- [ ] Tested on RP2 Pico
- [ ] Throughput improvement measured and documented

## Success Criteria

1. **Correctness:** All test cases pass
2. **Performance:** Mount FS throughput >200 KB/s (2.5x improvement)
3. **Compatibility:** No regressions on any TinyUSB port
4. **Interrupt handling:** Ctrl-C works identically to before

## References

- **Current code:** `shared/tinyusb/mp_usbd_cdc.c:71-95`
- **Ringbuf API:** `py/ringbuf.h`
- **TinyUSB CDC API:** `lib/tinyusb/src/class/cdc/cdc_device.h`
- **Performance analysis:** `docs/stdin_serial_analysis.md`
- **Benchmark results:** `docs/stdin_serial_analysis.md` (Benchmark Results section)

## Implementation Timeline

1. **Implement function:** 30 minutes
2. **Build and test on RP2:** 1 hour
3. **Benchmark and validate:** 30 minutes
4. **Code review and refinement:** 30 minutes
5. **Documentation update:** 15 minutes

**Total estimated time:** ~3 hours
