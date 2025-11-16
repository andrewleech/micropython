# Zephyr Threading - Fix and Testing Plan

**Date**: 2025-11-16
**Branch**: zephy_thread_2
**Reference**: ZEPHYR_THREADING_CODE_REVIEW.md

## Overview

This plan addresses:
1. **Critical bug fixes** (4 must-fix issues blocking 85-95% test pass rate)
2. **Generated header cleanup** (move to build-time generation)
3. **Code duplication analysis** (no duplication found - compiles directly from lib/zephyr)

---

## Part 1: Critical Bug Fixes

### Phase 1A: Fix arch_irq_lock() - PRIMARY CORRUPTION VECTOR 🔥

**Issue**: Uses BASEPRI register (masks priority >= 0x10 only), allowing high-priority interrupts during critical sections.

**Files**: `ports/stm32/irq.h:62-78`

**Implementation**:
```c
// Current (BROKEN):
static inline mp_uint_t disable_irq(void) {
    return arch_irq_lock();  // Uses BASEPRI - incomplete masking
}

// Fixed:
#if MICROPY_ZEPHYR_THREADING
static inline mp_uint_t disable_irq(void) {
    mp_uint_t state = __get_PRIMASK();
    __disable_irq();  // PRIMASK - masks ALL interrupts
    return state;
}

static inline void enable_irq(mp_uint_t state) {
    __set_PRIMASK(state);
}
#else
// Original BASEPRI implementation for non-Zephyr builds
static inline mp_uint_t disable_irq(void) {
    mp_uint_t state = raise_irq_pri(IRQ_PRI_PENDSV);
    ARM_DMB();
    return state;
}

static inline void enable_irq(mp_uint_t state) {
    ARM_DMB();
    set_irq_pri(state);
}
#endif
```

**Testing**:
1. Build with fix
2. Run mutate_bytearray.py (previously NotImplementedError: opcode)
3. Run thread_qstr1.py (previously NameError)
4. Run full thread test suite
5. Expected: +10-15% pass rate improvement

**Success Criteria**: No "NotImplementedError: opcode" corruption, thread operations complete without crashes

---

### Phase 1B: Fix Thread Initialization Race - SECONDARY CORRUPTION VECTOR 🔥

**Issue**: Thread added to list and started before fields initialized. GC can scan garbage pointers.

**File**: `extmod/zephyr_kernel/kernel/mpthread_zephyr.c:459-571`

**Current Flow** (BROKEN):
```
Line 498: Add to list (th->next = ...; mp_thread_list_head = th)
Line 521: Start thread with K_NO_WAIT (runs immediately!)
Lines 545-552: Initialize fields (th->arg, th->status, etc.)
```

**Proposed Fix - Option A** (Use K_FOREVER):
```c
mp_uint_t mp_thread_create_ex(void *(*entry)(void *), void *arg, size_t *stack_size,
    int priority, char *name) {

    mp_thread_mutex_lock(&thread_mutex, 1);

    // Allocate thread structure
    mp_thread_protected_t *prot = m_new_obj(mp_thread_protected_t);
    mp_thread_t *th = &prot->thread;

    // Initialize canaries FIRST
    prot->canary_before = THREAD_CANARY_BEFORE;
    prot->canary_after = THREAD_CANARY_AFTER;

    // Initialize ALL fields BEFORE adding to list
    memset(th, 0, sizeof(mp_thread_t));
    th->arg = arg;  // Initialize arg FIRST (critical for GC)
    th->status = MP_THREAD_STATUS_CREATED;
    th->stack_size = *stack_size;
    th->priority = priority;

    // Generate thread name
    static char thread_name[16];  // FIXME: Still has race, defer to Phase 2
    snprintf(thread_name, sizeof(thread_name), "mp_thread_%d", mp_thread_counter);

    // Allocate Zephyr stack
    // ... stack allocation code ...

    // Create thread in SUSPENDED state (K_FOREVER)
    th->id = k_thread_create(&th->z_thread,
                             th->z_stack,
                             th->stack_size,
                             zephyr_entry,
                             th,  // All fields now initialized!
                             NULL,
                             NULL,
                             th->priority,
                             0,  // No flags
                             K_FOREVER);  // DON'T START YET

    if (th->id == NULL) {
        mp_thread_mutex_unlock(&thread_mutex);
        mp_raise_OSError(MP_ENOMEM);
    }

    k_thread_name_set(th->id, thread_name);

    // NOW add to list (after full initialization)
    th->next = MP_STATE_VM(mp_thread_list_head);
    MP_STATE_VM(mp_thread_list_head) = th;

    mp_thread_counter++;

    // Memory barrier before starting thread
    __sync_synchronize();

    // NOW start the thread
    k_thread_start(th->id);

    mp_thread_mutex_unlock(&thread_mutex);

    return (mp_uint_t)th->id;
}
```

**Testing**:
1. Build with fix
2. Run thread_gc1.py (previously timeout - GC deadlock)
3. Run mutate_list.py (previously timeout)
4. Run full thread test suite
5. Expected: +5-10% pass rate improvement

**Success Criteria**: No GC scanning of partially-initialized threads, no corruption from garbage pointers

---

### Phase 1C: Remove Test Instrumentation from Production 🔥

**Issue**: gc_recently_run, gc_last_run_ms cause MemoryErrors with artificial 100ms GC delay.

**Files**:
- `py/gc.c:766-771, 862-871, 945`
- `py/gc.h:87-91`
- `ports/stm32/gccollect.c:52-56`

**Implementation**:
```bash
# Remove from py/gc.h:87-91
git diff py/gc.h  # Should remove these lines:
# extern bool gc_recently_run;
# extern uint32_t gc_last_run_ms;

# Remove from py/gc.c:766-771
# Remove initialization:
# bool gc_recently_run = false;
# uint32_t gc_last_run_ms = 0;

# Remove from py/gc.c:803-814 (threshold GC check)
# Remove entire timing check block

# Remove from py/gc.c:860-872 (emergency GC check)
# Remove entire timing check block

# Remove from py/gc.c:945
# Remove gc_last_run_ms update

# Remove from ports/stm32/gccollect.c:52-56
# Remove both gc_recently_run and gc_last_run_ms updates
```

**Testing**:
1. Build with removals
2. Run stress_heap.py (previously MemoryError from blocked allocations)
3. Run thread tests that allocate heavily
4. Expected: +5% pass rate improvement

**Success Criteria**: No MemoryErrors from artificially blocked allocations

---

### Phase 1D: Fix z_arm_pendsv Implementation 🔥

**Issue**: Referenced in pendsv.c but not implemented, causing context switch failures.

**File**: `ports/stm32/pendsv.c:96-97`

**Current** (BROKEN):
```asm
#if MICROPY_ZEPHYR_THREADING
"b z_arm_pendsv\n"  // Branch to non-existent function!
#endif
```

**Option 1**: Use Zephyr's existing swap mechanism
```c
// ports/stm32/pendsv.c
#if MICROPY_ZEPHYR_THREADING
// Zephyr handles context switching via its own PendSV handler
// We shouldn't reach here - Zephyr's swap_helper.S provides __pendsv
// Remove the branch and let Zephyr's handler take over
void PendSV_Handler(void) __attribute__((alias("z_arm_pendsv")));
#else
// Original MicroPython pendsv implementation
__attribute__((naked)) void PendSV_Handler(void) {
    // ... existing code ...
}
#endif
```

**Option 2**: Delegate to Zephyr's swap
```c
// ports/stm32/pendsv.c
#if MICROPY_ZEPHYR_THREADING
// Import Zephyr's PendSV handler (defined in swap_helper.S)
extern void z_arm_pendsv(void);

void PendSV_Handler(void) {
    z_arm_pendsv();
}
#else
// Original implementation
#endif
```

**Investigation Required**:
- Check if Zephyr's swap_helper.S already provides PendSV handler
- Verify Zephyr's context switch mechanism with k_thread_create()
- Test if removing branch allows Zephyr's handler to work

**Testing**:
1. Build with fix
2. Run thread_coop.py (cooperative threading test)
3. Run thread_sleep1.py (thread yielding test)
4. Expected: +5% pass rate improvement

**Success Criteria**: Context switches work without crashes, threads can yield properly

---

## Part 2: Generated Header Cleanup

### Phase 2A: Identify Generated Headers

**Currently checked into git**:
```
extmod/zephyr_kernel/generated/
├── cmsis_core.h
├── zephyr/
│   ├── arch/posix/
│   │   ├── board_irq.h
│   │   ├── posix_board_if.h
│   │   └── soc_irq.h
│   ├── devicetree_fixup.h
│   ├── devicetree_generated.h
│   ├── driver-validation.h
│   ├── kobj-types-enum.h
│   ├── syscall_list.h
│   └── syscalls/
│       ├── atomic_c.h
│       ├── clock.h
│       ├── device.h
│       ├── entropy.h
│       ├── kernel.h (706 lines!)
│       ├── kobject.h
│       ├── log_ctrl.h
│       ├── log_msg.h
│       ├── random.h
│       └── time_units.h
```

**Total**: ~1000+ lines of generated code checked in

---

### Phase 2B: Move to Build-Time Generation

**zephyr_kernel.mk already generates some headers**:
```makefile
# Lines 41-48: Already generates at build time:
$(BUILD)/zephyr_gen_root/zephyr/version.h
$(BUILD)/zephyr_gen_root/zephyr/syscalls/log_msg.h
$(BUILD)/zephyr_gen_root/zephyr/offsets.h
```

**Strategy**: Extend build-time generation for all headers

**Implementation**:
```makefile
# extmod/zephyr_kernel/zephyr_kernel.mk

# Generated headers (all build-time)
ZEPHYR_GEN_HEADERS := \
	$(BUILD)/zephyr_gen_root/zephyr/version.h \
	$(BUILD)/zephyr_gen_root/zephyr/offsets.h \
	$(BUILD)/zephyr_gen_root/zephyr/syscalls/log_msg.h \
	$(BUILD)/zephyr_gen_root/zephyr/syscalls/kernel.h \
	$(BUILD)/zephyr_gen_root/zephyr/devicetree_generated.h \
	$(BUILD)/zephyr_gen_root/zephyr/kobj-types-enum.h \
	$(BUILD)/zephyr_gen_root/cmsis_core.h

# Rule to generate syscalls/kernel.h from Zephyr's syscall definitions
$(BUILD)/zephyr_gen_root/zephyr/syscalls/kernel.h: $(ZEPHYR_BASE)/include/zephyr/kernel.h
	@echo "GEN (Zephyr syscalls) $@"
	$(Q)mkdir -p $(dir $@)
	$(Q)python3 $(ZEPHYR_BASE)/scripts/build/gen_syscalls.py \
		--json-file $(ZEPHYR_BASE)/misc/generated/syscalls.json \
		--base-output $(BUILD)/zephyr_gen_root/zephyr/syscalls \
		--syscall-dispatch $(BUILD)/zephyr_gen_root/zephyr/syscall_dispatch.c

# Rule to generate minimal devicetree stubs (no real devicetree in use)
$(BUILD)/zephyr_gen_root/zephyr/devicetree_generated.h:
	@echo "GEN (Zephyr devicetree stub) $@"
	$(Q)mkdir -p $(dir $@)
	$(Q)echo '/* Auto-generated devicetree stub - no devicetree in use */' > $@
	$(Q)echo '#ifndef DEVICETREE_GENERATED_H' >> $@
	$(Q)echo '#define DEVICETREE_GENERATED_H' >> $@
	$(Q)echo '/* Empty - devicetree disabled */' >> $@
	$(Q)echo '#endif' >> $@

# Rule to generate cmsis_core.h wrapper
$(BUILD)/zephyr_gen_root/cmsis_core.h:
	@echo "GEN (CMSIS wrapper) $@"
	$(Q)mkdir -p $(dir $@)
	$(Q)echo '/* CMSIS Core wrapper for MicroPython build */' > $@
	$(Q)echo '#ifndef CMSIS_CORE_H' >> $@
	$(Q)echo '#define CMSIS_CORE_H' >> $@
	$(Q)echo '#include "core_cm4.h"  /* Use MicroPython CMSIS headers */' >> $@
	$(Q)echo '#endif' >> $@
```

**Fallback Strategy**: If full generation is complex, keep **minimal stubs** that are build-time generated

---

### Phase 2C: Remove Generated Files from Git

```bash
# Add to .gitignore
echo "extmod/zephyr_kernel/generated/" >> .gitignore

# Remove from git
git rm -r extmod/zephyr_kernel/generated/

# Commit
git add .gitignore
git commit -s -m "extmod/zephyr_kernel: Remove generated headers, add to .gitignore.

Generated headers now created at build time in \$(BUILD)/zephyr_gen_root/.
Reduces repo size and prevents stale header issues."
```

**Testing**:
1. Clean build: `make BOARD=NUCLEO_F429ZI clean`
2. Full rebuild: `make BOARD=NUCLEO_F429ZI`
3. Verify generated headers exist in `build-NUCLEO_F429ZI/zephyr_gen_root/`
4. Run tests to confirm no regressions

**Success Criteria**: Build succeeds with generated headers in build directory, not in git

---

## Part 3: Code Duplication Analysis

### Finding: NO CODE DUPLICATION ✅

**Analysis**:
1. **zephyr_kernel.mk:94-108** compiles directly from `lib/zephyr/kernel/*.c`
2. **zephyr_kernel.mk:145-154** compiles directly from `lib/zephyr/arch/arm/core/`
3. **No Zephyr kernel code duplicated in extmod/zephyr_kernel/**

**What IS in extmod/zephyr_kernel**:
- `kernel/mpthread_zephyr.c` (667 lines) - MicroPython threading interface (NEW)
- `zephyr_cstart.c` (253 lines) - Initialization glue (NEW)
- `zephyr_config*.h` - Zephyr configuration (NEW)
- `arch/cortex_m/cortex_m_arch.c` (408 lines) - No longer used per line 159 comment
- `posix_minimal_board.c` (384 lines) - POSIX board emulation layer (NEW)
- `generated/` - Should be removed (Phase 2)

**80K+ lines breakdown**:
- ~30K lines: Build logs (should be removed)
- ~40K lines: Actual Zephyr kernel compiled from lib/zephyr (NOT duplication)
- ~8K lines: Generated headers (should be build-time)
- ~2K lines: Integration code (legitimate)

**Recommendation**: No changes needed. The architecture is correct - it compiles Zephyr sources directly.

---

## Testing Strategy

### Test Suite Execution

**Pre-fix baseline** (already established):
- 26/40 tests pass (65%)
- Known failures: mutate_bytearray, thread_gc1, thread_qstr1, stress tests, etc.

**After each phase**:
```bash
# Build
cd ports/stm32
make BOARD=NUCLEO_F429ZI clean
make BOARD=NUCLEO_F429ZI

# Flash
pyocd load --probe 066CFF495177514867213407 --target stm32f429xi \
  build-NUCLEO_F429ZI/firmware.hex
pyocd reset --probe 066CFF495177514867213407

# Run full test suite
cd ../../tests
env RESET='pyocd reset --probe 066CFF495177514867213407' \
  ./run-tests.py -t port:../STLK_F429 thread/*.py
```

**Metrics to track**:
- Pass rate (target: 85-95%)
- Memory corruption tests (mutate_bytearray, mutate_list)
- GC tests (thread_gc1, thread_gc1_debug)
- QSTR tests (thread_qstr1)
- Stress tests (stress_heap, stress_recurse, stress_schedule)

---

## Implementation Order

### Sprint 1: Critical Fixes (Estimated: 1-2 days)
1. **Phase 1A**: Fix arch_irq_lock() (30 min, test 1 hour)
2. **Phase 1B**: Fix thread initialization race (2 hours, test 1 hour)
3. **Phase 1C**: Remove test instrumentation (30 min, test 30 min)
4. **Phase 1D**: Fix z_arm_pendsv (investigation 1 hour, fix 30 min, test 1 hour)
5. **Full regression test**: Run complete suite (30 min)

**Expected outcome**: 85-95% test pass rate

### Sprint 2: Cleanup (Estimated: 4-6 hours)
1. **Phase 2A**: Audit generated headers
2. **Phase 2B**: Extend build-time generation
3. **Phase 2C**: Remove from git, update .gitignore
4. **Testing**: Clean build verification

**Expected outcome**: No generated code in git, faster clean builds

### Sprint 3: Code Quality (Deferred)
1. Fix static name buffer race (Phase 5 from review)
2. Make canaries debug-only (Phase 6 from review)
3. Fix stack scanning fallback (Phase 7 from review)
4. Consider simplification (Phase 10-12 from review)

---

## Success Criteria

### Phase 1 (Critical Fixes):
- ✅ Test pass rate: 85-95% (up from 65%)
- ✅ No "NotImplementedError: opcode" corruption
- ✅ No MemoryErrors from blocked allocations
- ✅ thread_gc1.py passes (no timeout)
- ✅ thread_qstr1.py passes (no NameError)
- ✅ mutate_bytearray.py passes (no corruption)
- ✅ Context switches work (thread_coop.py, thread_sleep1.py)

### Phase 2 (Generated Headers):
- ✅ No files in extmod/zephyr_kernel/generated/ in git
- ✅ Build generates headers in $(BUILD)/zephyr_gen_root/
- ✅ Clean build succeeds
- ✅ No test regressions

### Overall:
- ✅ Match or exceed ports/zephyr stability (80-90% pass rate)
- ✅ No memory corruption in thread tests
- ✅ Clean repository (no build artifacts, no generated code)
- ✅ Maintainable build system (build-time generation)

---

## Risk Mitigation

### Risk 1: arch_irq_lock() change breaks non-Zephyr code
**Mitigation**: Use #if MICROPY_ZEPHYR_THREADING guard, preserve original implementation

### Risk 2: z_arm_pendsv fix causes regressions
**Mitigation**: Test incremental changes, verify Zephyr's swap mechanism works

### Risk 3: Generated header removal breaks build
**Mitigation**: Implement build-time generation BEFORE removing files, test clean builds

### Risk 4: Thread initialization fix causes new race conditions
**Mitigation**: Add memory barriers, test extensively with thread_gc1.py

---

## Next Steps

1. **Review this plan** with stakeholders
2. **Create feature branch**: `zephy_thread_3_fixes` from `zephy_thread_2`
3. **Execute Sprint 1**: Apply all 4 critical fixes
4. **Run full test suite**: Measure improvement
5. **Document results**: Update ZEPHYR_THREADING_CODE_REVIEW.md with test results
6. **Execute Sprint 2**: Clean up generated headers
7. **Prepare for merge**: If 85-95% achieved, ready for upstream consideration
