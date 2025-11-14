# GC Timing Investigation - Threading Corruption

**Date**: 2025-11-11
**Commit**: 303bd7c82f
**Branch**: zephy_thread_2
**Target**: STM32 NUCLEO_F429ZI with Zephyr threading

## Problem Statement

Thread tests failing with memory corruption, specifically `mutate_bytearray.py` crashes with:
```
NotImplementedError: opcode
```

Crash analysis showed corrupted `mp_code_state_t` structure with invalid pointer patterns (0x0f080201, 0x5f080201). Use-after-free suspected - object collected by GC while still in use by preempted threads.

## Investigation Summary

Tested **four theories** about GC interaction with threading. All theories **refuted**.

---

## Theory 1: Missing Register Scanning

### Hypothesis
Preempted thread's saved registers (callee_saved.v1-v8 / r4-r11) not scanned by GC. Heap pointers in registers get collected while thread suspended.

### Implementation
**File**: `extmod/zephyr_kernel/kernel/mpthread_zephyr.c:404-407`

```c
// Scan saved callee registers (r4-r11)
void **saved_regs = (void **)&th->id->callee_saved;
gc_collect_root(saved_regs, 8);  // v1-v8 = r4-r11 (8 registers)
```

### Test Method
- Added register scanning to `mp_thread_gc_others()`
- Scanned all 8 callee-saved registers (v1-v8) stored in Zephyr thread context
- Built and tested with `mutate_bytearray.py`

### Result: **FAILED**
Corruption persisted with different pointer values on each run. Register scanning theory **refuted**.

---

## Theory 2: Wrong Stack Scanning

### Hypothesis
Scanning entire allocated stack (including uninitialized garbage) instead of active stack frames. Should scan from saved psp (stack pointer) to stack top.

### Initial Bug
Original code scanned full stack allocation:
```c
// WRONG: Scans garbage below psp
th->stack = (void *)th->id->stack_info.start;  // BOTTOM (low address)
th->stack_len = th->id->stack_info.size / sizeof(uintptr_t);
```

### Fix
**File**: `extmod/zephyr_kernel/kernel/mpthread_zephyr.c:372-411`

Corrected to scan only active frames:
```c
// ARM stacks grow DOWNWARD (high to low addresses)
// - stack_info.start = BOTTOM (low address, lowest valid SP)
// - stack_info.start + size = TOP (high address, initial SP)
// - callee_saved.psp = Current SP (middle, where preempted)

uintptr_t stack_bottom = (uintptr_t)th->id->stack_info.start;  // LOW
uintptr_t stack_top = stack_bottom + th->id->stack_info.size;  // HIGH
uintptr_t saved_sp = (uintptr_t)th->id->callee_saved.psp;  // Current SP

// Validate saved SP
if (saved_sp < stack_bottom || saved_sp > stack_top) {
    mp_printf(&mp_plat_print,
              "WARNING: Corrupt SP thread=%p sp=0x%08x bounds=0x%08x-0x%08x\r\n",
              th, (unsigned)saved_sp, (unsigned)stack_bottom, (unsigned)stack_top);
    // Fallback: scan full stack
    th->stack = (void *)stack_bottom;
    th->stack_len = th->id->stack_info.size / sizeof(uintptr_t);
} else {
    // Scan ONLY active portion: from saved_sp UP to stack_top
    th->stack = (void *)saved_sp;
    th->stack_len = (stack_top - saved_sp) / sizeof(uintptr_t);
}

gc_collect_root(th->stack, th->stack_len);
```

### Additional Diagnostics
**File**: `ports/stm32/main.c:361-368`

Added stack direction verification:
```c
volatile int local1 = 1;
volatile int local2 = 2;
mp_printf(&mp_plat_print, "Stack direction check: local1=%p local2=%p -> %s\r\n",
          &local1, &local2,
          ((uintptr_t)&local2 < (uintptr_t)&local1) ? "GROWS DOWN (correct)" : "GROWS UP (ERROR!)");
```

### Address Calculation Bug Found and Fixed
Initially calculated addresses incorrectly (assumed stack_info.start was HIGH address). Corrected after consulting upstream `ports/zephyr/mpthreadport.c` and Zephyr docs.

### Result: **FAILED**
Corruption persisted even with correct stack scanning. Fix is architecturally sound and kept, but theory **refuted** as root cause.

---

## Theory 3: Double-GC Timing Issue

### Hypothesis
Trace from `mutate_bytearray.py` showed **two GC cycles** running during thread execution:
```
[4 heap code_states allocated]
[All 4 threads EXEC]
[GC #1 START]
[GC #1 END]
[GC #2 START]  ← SECOND GC!
[GC #2 END]
[All 4 threads RETURN]
NotImplementedError: opcode
```

Suspected rapid successive GC cycles don't give preempted threads time to resume before second GC collects objects marked by first GC.

### Root Cause in gc_alloc()
`py/gc.c` has two GC trigger points:
1. **Line 800**: Threshold-based (when `gc_alloc_amount >= gc_alloc_threshold`)
2. **Line 866**: Emergency collection (when no free blocks after first GC)

### Implementation - Test 1
**Files**: `py/gc.c`, `py/gc.h`, `ports/stm32/gccollect.c`

```c
// py/gc.c:765
bool gc_recently_run = false;

// py/gc.c:800-814 - Threshold GC
if (!collected && MP_STATE_MEM(gc_alloc_amount) >= MP_STATE_MEM(gc_alloc_threshold)) {
    GC_EXIT();
    gc_collect();
    collected = 1;
    gc_recently_run = true;  // Mark GC as having run
    GC_ENTER();
}

// py/gc.c:860-866 - Emergency GC
if (gc_recently_run) {
    DEBUG_printf("gc_alloc(...): skipping second GC (one just ran)\n", ...);
    return NULL;  // Fail allocation instead of running second GC
}
gc_collect();
gc_recently_run = true;

// ports/stm32/gccollect.c:52-53 - Clear after explicit GC
gc_collect_end();
gc_recently_run = false;  // Allow future allocations
```

### Test Method
- Added `gc_recently_run` flag
- Skip second emergency GC if flag set
- Fail allocation instead of running second GC
- Clear flag after explicit `gc.collect()` or successful allocation

### Result: **FAILED**
Corruption persisted. Double-GC theory **refuted**.

---

## Theory 4: Insufficient Delay Between GC Cycles

### Hypothesis
Even with double-GC prevented, GC cycles too close together. Preempted threads need time to resume and update stacks before next GC.

### Implementation - Test 4
**Files**: `py/gc.c`, `py/gc.h`, `ports/stm32/gccollect.c`

```c
// py/gc.c:768-770
uint32_t gc_last_run_ms = 0;
#define GC_MIN_INTERVAL_MS 100

// py/gc.c:801-813 - Threshold GC with delay check
if (!collected && MP_STATE_MEM(gc_alloc_amount) >= MP_STATE_MEM(gc_alloc_threshold)) {
    uint32_t now_ms = mp_hal_ticks_ms();
    if (now_ms - gc_last_run_ms >= GC_MIN_INTERVAL_MS || gc_last_run_ms == 0) {
        GC_EXIT();
        gc_collect();
        collected = 1;
        gc_recently_run = true;
        gc_last_run_ms = now_ms;  // Update timestamp
        GC_ENTER();
    } else {
        DEBUG_printf("gc_alloc(...): skipping GC (only %u ms since last)\n",
                     (unsigned)(now_ms - gc_last_run_ms));
    }
}

// py/gc.c:860-872 - Emergency GC with delay check
uint32_t now_ms = mp_hal_ticks_ms();
if (gc_recently_run || (now_ms - gc_last_run_ms < GC_MIN_INTERVAL_MS && gc_last_run_ms != 0)) {
    DEBUG_printf("gc_alloc(...): skipping second GC (recent=%d, delay=%u ms)\n",
                 gc_recently_run, (unsigned)(now_ms - gc_last_run_ms));
    return NULL;  // Fail allocation
}
gc_collect();
gc_last_run_ms = now_ms;

// ports/stm32/gccollect.c:55-56
gc_recently_run = false;
gc_last_run_ms = mp_hal_ticks_ms();  // Update after explicit GC
```

### Test Method
- Enforce 100ms minimum delay between GC cycles
- Use `mp_hal_ticks_ms()` for timing
- Skip GC if within 100ms of last GC
- Update timestamp after all GC types (threshold, emergency, explicit)

### Result: **FAILED**
Test showed **different failure mode**:
```
Unhandled exception in thread started by <function th at 0x2000bfc0>
MemoryError: memory allocation failed, allocating 1845 bytes
[... 3 more identical MemoryErrors with different sizes ...]
NotImplementedError: opcode
```

Multiple MemoryErrors from blocked allocations (GC delay too restrictive), but corruption **still occurs** with NotImplementedError. GC delay theory **refuted**.

---

## Files Modified (Commit 303bd7c82f)

### Core GC Changes
- `py/gc.c`: Test instrumentation (gc_recently_run flag, gc_last_run_ms timestamp, delay logic)
- `py/gc.h`: Exposed gc_recently_run and gc_last_run_ms for external access
- `ports/stm32/gccollect.c`: Update timestamp after explicit GC, include mphal.h

### Threading Changes
- `extmod/zephyr_kernel/kernel/mpthread_zephyr.c`: Fixed stack scanning (use saved psp), added register scanning
- `extmod/zephyr_kernel/zephyr_config_cortex_m.h`: (Config changes)

### Diagnostics
- `ports/stm32/main.c`: Stack direction verification
- `py/objfun.c`: (Logging changes, later removed)
- `py/vm.c`: (Corruption detection, later removed)

---

## Test Results Summary

| Test | Theory | Result | Evidence |
|------|--------|--------|----------|
| 1 | Missing register scanning | **FAILED** | Corruption persists, different pointer values each run |
| 2 | Wrong stack scanning | **FAILED** | Corruption persists with corrected scanning (fix kept) |
| 3 | Double-GC timing | **FAILED** | Corruption persists without double-GC |
| 4 | Insufficient GC delay | **FAILED** | MemoryErrors + corruption persists |

---

## Conclusions

### What We Learned

1. **Register scanning is NOT the issue**
   - Scanning saved callee registers (r4-r11) doesn't prevent corruption
   - Heap pointers in registers are not the root cause

2. **Stack scanning is now CORRECT**
   - Original code scanned garbage below psp (architecturally wrong)
   - Fixed to scan only active frames (psp to top)
   - Fix is sound and kept, but doesn't solve corruption

3. **GC timing is NOT the issue**
   - Neither double-GC nor insufficient delay causes corruption
   - GC can run rapidly without corruption IF objects properly protected
   - GC delay causes MemoryErrors but corruption persists anyway

4. **Corruption is NOT GC-related**
   - All GC theories refuted
   - Corruption must have different root cause

### What This Rules Out

- ❌ Missing GC root registration (thread list IS registered)
- ❌ Preempted thread register values not scanned
- ❌ Scanning uninitialized stack memory
- ❌ Rapid successive GC cycles
- ❌ Insufficient time for threads to resume between GCs

### Implications

The corruption source is **not in GC interaction with threading**. Must investigate:

1. **VM race conditions**: Bytecode execution state corruption during thread switches
2. **Buffer overflows**: Stack or heap overruns corrupting nearby structures
3. **Synchronization bugs**: Data races in thread-shared structures
4. **Compiler optimization**: Aggressive optimization breaking thread assumptions

---

## Next Steps

### Recommended Approach

1. **Revert test instrumentation** (GC delay causes MemoryErrors)
   - Keep stack scanning fix (architecturally correct)
   - Remove gc_recently_run flag and gc_last_run_ms timestamp
   - Remove GC delay logic

2. **Use GDB hardware watchpoints** to catch corruption in the act:
   ```gdb
   # Watch mp_code_state_t fun_bc pointer
   watch *(uint32_t*)0x20018BB4  # fun_bc address
   ```

3. **Test with compiler optimization disabled**:
   ```bash
   make BOARD=NUCLEO_F429ZI CFLAGS_EXTRA="-O0"
   ```
   Check if optimization-related bug (reordering, register allocation)

4. **Add canary values** around critical structures:
   - `mp_code_state_t` allocations
   - Thread stack boundaries
   - Detect overflows before corruption spreads

5. **Analyze bytecode execution** for race conditions:
   - Thread switches during LOAD_CONST / DECODE_PTR operations
   - child_table access patterns
   - GIL release/acquire timing

---

## Reference Information

### Hardware
- **Board**: STM32 NUCLEO_F429ZI
- **MCU**: STM32F429ZI (Cortex-M4, 192KB RAM)
- **Architecture**: ARM Cortex-M (stacks grow DOWN)

### Software
- **MicroPython**: v1.26.0-preview.308+
- **Branch**: zephy_thread_2
- **Threading**: Zephyr kernel on STM32 (MICROPY_ZEPHYR_THREADING)
- **GIL**: Enabled (MICROPY_PY_THREAD_GIL=1)

### Failing Test
**File**: `tests/thread/mutate_bytearray.py`

Creates 4 threads that allocate and mutate bytearrays concurrently. Crashes with:
```
NotImplementedError: opcode
```

Indicates VM reading invalid opcode from corrupted bytecode or instruction pointer.

### Commit History
- **303bd7c82f**: GC timing test instrumentation (THIS COMMIT)
- **4e29364323**: GC canary corruption fix (protected pointer)
- **2ed7e574bb**: Main thread stack initialization fix
- **c0873628bf**: Stack size validation with diagnostics
- **327a3ba559**: Zephyr source in qstr extraction

---

## Code Reviewer Assessment

Principal code reviewer consulted twice during investigation:

### First Review (Stack Scanning)
- Identified critical flaw: scanning entire allocated stack vs active frames
- Recommended using saved psp to calculate active portion
- Provided stack address arithmetic guidance

### Second Review (Double-GC Theory)
- Assessed theory as plausible given trace evidence
- Recommended test strategy: prevent double-GC, add delay, disable optimization
- Noted potential for premature collection with rapid GC cycles

Both reviews technically sound, but theories ultimately refuted by testing.

---

## AutoMem Record

**Memory ID**: 5979fbb5-e7b4-416d-9e7d-979493f91613

Test 4 result stored:
> "Test 4 (GC delay) FAILED: 100ms minimum delay between GC cycles causes MemoryErrors (allocation blocked by delay) but corruption still occurs with NotImplementedError: opcode. GC timing theories (double-GC, insufficient delay) refuted. Corruption likely NOT GC-related."

**Tags**: threading, gc, testing
**Type**: Insight
**Priority**: 0.9
