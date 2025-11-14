# ports/zephyr Baseline Testing - INVESTIGATION IN PROGRESS

**Date**: 2025-11-11
**Status**: 🟢 Phase 2 Complete (Flash & Verify)
**Last Updated**: 2025-11-11 19:50 UTC
**Related**: GC_TIMING_INVESTIGATION.md (commit 104d69544c), commit 303bd7c82f (test instrumentation)

## Purpose

Establish baseline by testing MicroPython's official Zephyr port (`ports/zephyr`) against the same thread test suite that fails on our custom `extmod/zephyr_kernel` implementation.

**Goal**: Determine if threading corruption issues are:
- Specific to `extmod/zephyr_kernel` implementation → Fix our code
- Fundamental to Zephyr threading on STM32 → Report upstream or investigate Zephyr kernel

## Quick Reference Commands

### Build
```bash
source ~/zephyr/zephyr-env.sh
cd /home/corona/micropython/ports/zephyr
west build -b nucleo_f429zi . -DOVERLAY_CONFIG=thread.conf
```

### Flash
```bash
pyocd load --probe 066CFF495177514867213407 \
  --target stm32f429xi build/zephyr/zephyr.hex
pyocd reset --probe 066CFF495177514867213407
```

### Verify
```bash
mpremote connect ./STLK_F429 resume exec "import os; print(os.uname())"
```

### Test
```bash
cd /home/corona/micropython/tests
env RESET='pyocd reset --probe 066CFF495177514867213407' \
  ./run-tests.py -t port:./STLK_F429 thread/*.py
```

## Investigation Plan

- [x] **Phase 1**: Build official ports/zephyr with threading ✅
- [x] **Phase 2**: Flash and verify firmware on NUCLEO_F429ZI ✅
- [ ] **Phase 3**: Run complete thread test suite
- [ ] **Phase 4**: Compare results to extmod/zephyr_kernel
- [ ] **Phase 5**: Analyze differences and document conclusions

## Background Research

### Board Support ✅
- **Board**: NUCLEO_F429ZI officially supported by Zephyr
- **Board ID**: `nucleo_f429zi` (for west build)
- **Zephyr board def**: `~/zephyr/boards/st/nucleo_f429zi/`
- **MicroPython config**: Uses default (no custom board.conf needed)

### Build System
- **Tool**: West (Zephyr's meta-tool) + CMake + Kconfig
- **Environment**: Requires `source ~/zephyr/zephyr-env.sh` (sets ZEPHYR_BASE)
- **Output**: `build/zephyr/zephyr.{elf,hex,bin}`

### Threading Configuration
- **Overlay**: `thread.conf` enables threading support
  - `CONFIG_THREAD_CUSTOM_DATA=y`
  - `CONFIG_THREAD_MONITOR=y`
  - `CONFIG_THREAD_STACK_INFO=y`

### Key Architectural Differences

| Aspect | ports/zephyr | extmod/zephyr_kernel |
|--------|--------------|----------------------|
| **Integration** | Full Zephyr RTOS | Minimal kernel only |
| **Build** | West + CMake | Make |
| **Thread File** | mpthreadport.c (308 lines) | mpthread_zephyr.c (667 lines) |
| **Mutex** | k_sem (binary semaphore) | k_mutex (recently switched) |
| **Stack Alloc** | Static pool (4×5KB) | Static/heap hybrid |
| **Known Bugs** | ⚠️ Dangling pointer (line 242) | ✅ Fixed (commit 6c81a76b5f) |

### Critical Finding: Same Dangling Pointer Bug

**ports/zephyr line 242-244**:
```c
mp_uint_t mp_thread_create(void *(*entry)(void *), void *arg, size_t *stack_size) {
    char _name[16];  // ⚠️ DANGLING POINTER BUG
    snprintf(_name, sizeof(_name), "mp_thread_%d", mp_thread_counter);
    k_thread_name_set(id, _name);  // BUG: _name goes out of scope!
```

This is **identical** to the bug in `extmod/zephyr_kernel` that was fixed by changing to `static char name[16]`.

## Execution Log

### 2025-11-11 01:50 UTC - Investigation Started
- Created investigation document
- Research complete: Board supported, build system understood
- Identified dangling pointer bug in upstream code
- Commit: 171b7d4287 (initial investigation plan)

### 2025-11-11 19:45 UTC - Phase 1 Complete: Build SUCCESS ✅
**Agent 1 (general-purpose)** - Build official ports/zephyr

**Build Configuration**:
- Board: nucleo_f429zi (STM32F429ZI)
- Zephyr: v4.2.99 (development/main)
- Toolchain: GNU ARM Embedded 14.3.1
- Overlay: thread.conf (THREAD_CUSTOM_DATA, THREAD_MONITOR, THREAD_STACK_INFO)

**Build Results**:
- Status: ✅ SUCCESS
- Duration: ~3 minutes 23 seconds
- Warnings: 1 (lfs2.c non-critical)
- Errors: 0

**Artifacts Generated**:
- `/tmp/zephyr_workspace/build/zephyr/zephyr.elf` - 5.4 MB (with debug symbols)
- `/tmp/zephyr_workspace/build/zephyr/zephyr.hex` - 856 KB
- `/tmp/zephyr_workspace/build/zephyr/zephyr.bin` - 304 KB

**Memory Usage**:
- Flash: 311,284 bytes / 2 MB (14.84%)
- RAM: 131,848 bytes / 192 KB (67.06%)

**Next**: Flash to NUCLEO_F429ZI and verify

### 2025-11-11 19:50 UTC - Phase 2 Complete: Flash & Verify SUCCESS ✅
**Agent 2 (general-purpose)** - Flash and verify firmware

**Flash Results**:
- Status: ✅ SUCCESS
- Erased: 393,216 bytes (7 sectors)
- Programmed: 311,296 bytes (76 pages)
- Speed: 17.22 kB/s

**Firmware Verification**:
```
MicroPython v1.27.0-preview.388.g27544a2d81.dirty on 2025-11-14
Platform: zephyr
Machine: zephyr-nucleo_f429zi with stm32f429xx
Build: ZEPHYR_NUCLEO_F429ZI
Thread mode: _thread='GIL'
```

**Threading Test**: ✅ PASS
- Module `_thread` available
- Basic thread spawning functional
- GIL-based concurrency confirmed

**Next**: Run thread test suite

---

## Test Results

### extmod/zephyr_kernel Baseline (Current Implementation)
**Commit**: 303bd7c82f (with GC timing test instrumentation)
**Results**: 26/40 PASS (65%), 7 FAIL (17.5%), 7 TIMEOUT (17.5%)

**Key Failures**:
- `mutate_bytearray.py` - **NotImplementedError: opcode** (memory corruption)
- `thread_qstr1.py` - NameError
- `thread_lock3.py`, `thread_lock4.py` - Lock edge cases
- `stress_heap.py`, `stress_recurse.py`, `stress_schedule.py` - Resource limits

### ports/zephyr Results
**Status**: ⏳ Not yet tested

**Expected completion**: [Will be filled by Agent 3]

---

## Comparison Analysis

**Status**: ⏳ Pending test completion

[Will be filled by Agent 4 after test results available]

---

## Conclusions

**Status**: ⏳ Pending analysis

[Will be filled after comparison complete]

---

## AutoMem References

**Memory IDs**:
- `5979fbb5-e7b4-416d-9e7d-979493f91613` - Test 4 (GC delay) failure
- [Additional entries will be added during investigation]

---

## Related Documentation

- **GC_TIMING_INVESTIGATION.md** (commit 104d69544c) - Four GC theories tested and refuted
- **CLAUDE.local.md** - Hardware setup and testing procedures
- **GDB_WATCHPOINT_GUIDE.md** - Hardware watchpoint debugging guide
