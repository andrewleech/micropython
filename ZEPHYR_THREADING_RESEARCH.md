# Zephyr Kernel Threading Integration Research

## Executive Summary

This document explores integrating the Zephyr RTOS kernel into MicroPython ports to provide unified threading support. The goal is to use only the core Zephyr kernel (threads, mutexes, semaphores, scheduling) while keeping existing port build systems and HAL layers unchanged.

## Current State Analysis

### Existing Threading Implementations

MicroPython currently has 7 different threading implementations across ports:

1. **Unix port** (`ports/unix/mpthreadport.c`): Uses pthreads (410 lines)
   - Thread creation via pthread_create
   - Mutex via pthread_mutex
   - GC coordination using signals (SIGRTMIN+5)
   - TLS via pthread_key

2. **Zephyr port** (`ports/zephyr/mpthreadport.c`): Uses Zephyr kernel (309 lines)
   - Thread creation via k_thread_create
   - Mutex implemented as k_sem (binary semaphore)
   - GC via k_thread_foreach iteration
   - Thread pool with fixed stack slots

3. **STM32 port** (`ports/stm32/mpthreadport.c`): Custom implementation (100 lines)
   - Delegates to pybthread.c custom scheduler
   - Cooperative multitasking with timeslicing
   - Manual context switching

4. **ESP32 port** (`ports/esp32/mpthreadport.c`): Uses FreeRTOS
   - Thread creation via xTaskCreate
   - Mutex via FreeRTOS mutex
   - TLS via vTaskSetThreadLocalStoragePointer

5. **RP2 port** (`ports/rp2/mpthreadport.h`): Pico SDK mutexes
   - Dual-core architecture (not traditional threading)
   - Uses hardware multicore support

6. **Renesas-RA port**: Custom implementation
7. **CC3200 port**: Custom implementation

### MicroPython Threading API

All ports must implement this interface (defined in `py/mpthread.h`):

```c
// Thread management
mp_uint_t mp_thread_create(void *(*entry)(void *), void *arg, size_t *stack_size);
mp_uint_t mp_thread_get_id(void);
void mp_thread_start(void);
void mp_thread_finish(void);

// Thread state (TLS)
mp_state_thread_t *mp_thread_get_state(void);
void mp_thread_set_state(mp_state_thread_t *state);

// Synchronization
void mp_thread_mutex_init(mp_thread_mutex_t *mutex);
int mp_thread_mutex_lock(mp_thread_mutex_t *mutex, int wait);
void mp_thread_mutex_unlock(mp_thread_mutex_t *mutex);

// Optional: recursive mutex (MICROPY_PY_THREAD_RECURSIVE_MUTEX)
void mp_thread_recursive_mutex_init(mp_thread_recursive_mutex_t *mutex);
int mp_thread_recursive_mutex_lock(mp_thread_recursive_mutex_t *mutex, int wait);
void mp_thread_recursive_mutex_unlock(mp_thread_recursive_mutex_t *mutex);

// GC coordination
void mp_thread_gc_others(void);
```

## Zephyr Kernel Analysis

### Structure

The Zephyr RTOS (in `lib/zephyr/`) contains:
- **kernel/** (788KB): Core scheduling, threads, synchronization
- **arch/** (3.7MB): Architecture-specific implementations (ARM, x86, RISC-V, etc.)
- **include/** (22MB): Headers and API definitions

### Key Components

From `lib/zephyr/kernel/CMakeLists.txt`, the kernel requires:

**Core (always compiled):**
- thread.c, sched.c - Thread and scheduler
- mutex.c, sem.c, condvar.c - Synchronization primitives
- init.c, device.c - Initialization
- mem_slab.c, kheap.c - Memory management
- timeout.c, timer.c - Timing (if CONFIG_SYS_CLOCK_EXISTS)

**Architecture-specific requirements:**
- Context switching (in arch/*/core/)
- Atomic operations
- Interrupt handling
- Timer/clock integration

### Configuration Complexity

Zephyr uses Kconfig extensively. Analysis of `kernel/thread.c` shows:
- 174 CONFIG_ option references
- Dependencies on: CONFIG_MULTITHREADING, CONFIG_THREAD_CUSTOM_DATA, CONFIG_THREAD_NAME, CONFIG_USERSPACE, etc.

### Current Zephyr Port Integration

The existing Zephyr port (`ports/zephyr/`) integrates via:
- Full Zephyr build system (west + CMake)
- `CMakeLists.txt` using `find_package(Zephyr)`
- All Zephyr features available (drivers, networking, etc.)
- MicroPython runs as a Zephyr application thread

## Proposed Approach

### Design Principles

1. **Minimal extraction**: Only include kernel threading components
2. **Build system independence**: No requirement for west or Zephyr CMake
3. **Fixed configuration**: Pre-defined CONFIG_ options, no dynamic Kconfig
4. **Architecture abstraction**: Minimal arch layer for each target
5. **Opt-in**: Existing implementations remain available during transition

### Architecture

```
extmod/zephyr_kernel/           # Shared Zephyr kernel integration
├── zephyr_config.h             # Fixed CONFIG_ definitions
├── zephyr_kernel.h             # MicroPython-facing API
├── arch/                       # Architecture-specific code
│   ├── arm/
│   │   └── cortex_m/
│   │       ├── switch.c        # Context switching
│   │       └── atomic.c        # Atomic ops
│   └── x86/
│       └── x86_64/
│           ├── switch.c
│           └── atomic.c
└── kernel/                     # Wrapper/glue code
    └── mpthread_zephyr.c       # mp_thread_* implementations

ports/unix/
├── zephyr_arch_unix.c          # Unix-specific arch layer (pthread backing)
└── Makefile                    # Conditionally include extmod/zephyr_kernel

ports/stm32/
├── zephyr_arch_stm32.c         # STM32-specific arch layer
└── Makefile                    # Conditionally include extmod/zephyr_kernel
```

### Implementation Phases

#### Phase 1: Infrastructure Setup

1. Create `extmod/zephyr_kernel/` directory structure
2. Create `zephyr_config.h` with minimal fixed CONFIG_ definitions:
   ```c
   #define CONFIG_MULTITHREADING 1
   #define CONFIG_NUM_PREEMPT_PRIORITIES 15
   #define CONFIG_NUM_COOP_PRIORITIES 16
   #define CONFIG_THREAD_CUSTOM_DATA 1
   #define CONFIG_THREAD_NAME 1
   #define CONFIG_THREAD_MAX_NAME_LEN 32
   #define CONFIG_SYS_CLOCK_EXISTS 1
   // Disable complex features
   #define CONFIG_USERSPACE 0
   #define CONFIG_MMU 0
   #define CONFIG_DEMAND_PAGING 0
   ```

3. Identify minimal kernel file list:
   - thread.c
   - sched.c
   - mutex.c
   - sem.c
   - timeout.c
   - timer.c (if needed)
   - queue.c (for ready queues)
   - priority_queues.c

4. Create `extmod/zephyr_kernel/kernel/mpthread_zephyr.c` implementing the mp_thread_* API

#### Phase 2: Unix Port (Proof of Concept)

**Goal**: Validate approach with easiest-to-test platform

1. **Architecture layer** (`ports/unix/zephyr_arch_unix.c`):
   - Implement context switching using setjmp/longjmp or assembly
   - Or: Use pthread as backing with Zephyr API wrapper
   - Implement atomic operations using GCC builtins
   - Provide timer tick using SIGALRM

2. **Build integration** (`ports/unix/Makefile`):
   ```makefile
   ifeq ($(MICROPY_ZEPHYR_THREADING),1)
   SRC_C += $(addprefix extmod/zephyr_kernel/kernel/,\
       thread.c \
       sched.c \
       mutex.c \
       sem.c \
   )
   SRC_C += zephyr_arch_unix.c
   INC += -I$(TOP)/lib/zephyr/include
   INC += -I$(TOP)/extmod/zephyr_kernel
   CFLAGS += -DMICROPY_ZEPHYR_THREADING=1
   endif
   ```

3. **Testing**:
   - Run tests/thread/thread_*.py tests
   - Compare behavior with pthread implementation
   - Measure performance impact

#### Phase 3: STM32 Port Integration

**Goal**: Real embedded target with custom threading

1. **Architecture layer** (`ports/stm32/zephyr_arch_stm32.c`):
   - Context switching via PendSV handler
   - Use existing STM32 HAL timer for tick
   - ARM Cortex-M atomic operations (LDREX/STREX)
   - SysTick integration

2. **Replace pybthread** implementation:
   - Remove ports/stm32/pybthread.[ch]
   - Update mpthreadport.[ch] to use Zephyr kernel

3. **Build integration** (`ports/stm32/Makefile`):
   - Similar to Unix port
   - Add ARM arch-specific files
   - Ensure linking order correct (kernel before app)

4. **Testing**:
   - Test on actual STM32 hardware
   - Verify GC coordination works
   - Check stack usage

#### Phase 4: Additional Ports (Future)

Consider integrating other ports based on success:
- Renesas-RA
- CC3200
- MIMXRT
- SAMD

**Explicitly excluded**:
- ESP32 (already uses FreeRTOS)
- RP2 (multicore, not traditional threading)
- Zephyr (already uses full Zephyr)

### Technical Challenges

#### Challenge 1: CONFIG_ Dependencies

**Issue**: Zephyr kernel expects ~174 CONFIG_ options defined

**Solution**: Create comprehensive `zephyr_config.h` with all required options set to sensible defaults:
```c
// Thread configuration
#define CONFIG_MULTITHREADING 1
#define CONFIG_NUM_PREEMPT_PRIORITIES 15
#define CONFIG_NUM_COOP_PRIORITIES 16
#define CONFIG_MAIN_STACK_SIZE 2048
#define CONFIG_IDLE_STACK_SIZE 512
#define CONFIG_ISR_STACK_SIZE 2048

// Feature enables
#define CONFIG_THREAD_CUSTOM_DATA 1
#define CONFIG_THREAD_NAME 1
#define CONFIG_THREAD_MONITOR 1
#define CONFIG_SYS_CLOCK_EXISTS 1

// Feature disables
#define CONFIG_USERSPACE 0
#define CONFIG_MMU 0
#define CONFIG_DEMAND_PAGING 0
#define CONFIG_THREAD_STACK_INFO 0
#define CONFIG_OBJ_CORE_THREAD 0
// ... etc
```

#### Challenge 2: Architecture Dependencies

**Issue**: Kernel requires arch-specific implementations (context switch, atomics)

**Solution**: Provide minimal arch layer for each supported architecture:
- ARM Cortex-M: Use CMSIS + standard Cortex-M context switch
- x86/x86_64: Either native implementation or pthread backing
- RISC-V: Standard RISC-V context switch (if needed later)

For Unix POC, could use pthread as backing layer initially:
```c
// zephyr_arch_unix.c - Uses pthread underneath
void arch_switch(struct k_thread *new_thread, struct k_thread *old_thread) {
    // Use pthread context for now
    pthread_setspecific(current_thread_key, new_thread);
}
```

#### Challenge 3: Timer/Clock Integration

**Issue**: Zephyr kernel expects timer ticks for scheduling

**Solution**:
- Unix: Use SIGALRM or timer_create()
- STM32: Use SysTick (already configured in most ports)
- Provide `sys_clock_announce()` wrapper

#### Challenge 4: Build System Integration

**Issue**: Need to compile Zephyr kernel without full Zephyr build system

**Solution**: Direct compilation in port Makefile:
```makefile
ZEPHYR_KERNEL = $(TOP)/lib/zephyr/kernel
ZEPHYR_INC = $(TOP)/lib/zephyr/include

INC += -I$(ZEPHYR_INC)
INC += -I$(ZEPHYR_INC)/zephyr
INC += -I$(TOP)/extmod/zephyr_kernel

# Compile kernel sources directly
SRC_C += $(ZEPHYR_KERNEL)/thread.c
SRC_C += $(ZEPHYR_KERNEL)/sched.c
# etc...
```

#### Challenge 5: Header Dependencies

**Issue**: 22MB of headers in lib/zephyr/include

**Solution**: This is acceptable - just add to include path, don't copy. The headers are mostly inline functions and macros.

#### Challenge 6: Memory Overhead

**Issue**: Zephyr kernel adds binary size

**Mitigation**:
- Only compile essential kernel files (~20-30KB code)
- Use -Os optimization
- Strip unused features via CONFIG_ options
- Acceptable tradeoff for unified API

#### Challenge 7: GC Integration

**Issue**: MicroPython GC needs to scan all thread stacks

**Current approaches**:
- Unix: Signal threads to scan themselves
- Zephyr port: Use k_thread_foreach() to iterate
- STM32: Iterate linked list

**Solution**: Implement mp_thread_gc_others() using Zephyr's thread iteration:
```c
void mp_thread_gc_others(void) {
    // Lock scheduler
    k_sched_lock();

    // Iterate all threads
    K_THREAD_FOREACH(thread) {
        if (thread != k_current_get()) {
            gc_collect_root(thread->stack, thread->stack_size);
        }
    }

    k_sched_unlock();
}
```

### Benefits

1. **Unified API**: Same threading behavior across all ports
2. **Reduced maintenance**: Single implementation to maintain and test
3. **Feature parity**: All ports get same threading capabilities
4. **Better tested**: Zephyr kernel is extensively tested
5. **Advanced features**: Easy to add work queues, events, etc. later

### Drawbacks

1. **Code size increase**: ~20-50KB per port (kernel + arch layer)
2. **Complexity**: Additional layer of abstraction
3. **Migration effort**: Need to update each port individually
4. **Learning curve**: Developers need to understand Zephyr kernel
5. **Compatibility risk**: Need to ensure exact API compatibility

### Alternative Approaches Considered

#### Alternative 1: Full Zephyr Build Integration
Use full Zephyr build system (west + CMake) for all ports.

**Rejected because**:
- Too invasive, breaks existing build systems
- Requires west tooling
- Much more complex than needed

#### Alternative 2: Extract and Fork Kernel
Copy Zephyr kernel to extmod/ and maintain separately.

**Rejected because**:
- Creates maintenance burden
- Lose upstream improvements
- Divergence over time

#### Alternative 3: Common Threading API Library
Create new threading abstraction that all ports implement.

**Rejected because**:
- Doesn't reduce port-specific code
- Still need architecture-specific implementations
- Doesn't leverage Zephyr's maturity

### Success Criteria

The integration is successful if:

- [ ] Unix port builds with `MICROPY_ZEPHYR_THREADING=1`
- [ ] All threading tests pass on Unix with Zephyr kernel
- [ ] STM32 port builds and runs with Zephyr threading
- [ ] Threading tests pass on STM32 hardware
- [ ] Binary size increase < 50KB on STM32
- [ ] No Python API changes required
- [ ] Performance within 10% of native implementation
- [ ] GC coordination works correctly

### Open Questions

1. **Fallback strategy**: Keep native implementations as fallback, or fully replace?
   - Recommendation: Keep as compile option during transition

2. **Feature exposure**: Which Zephyr features to expose beyond basic threading?
   - Work queues (k_work_*)?
   - Events (k_event_*)?
   - Timers (k_timer_*)?
   - Recommendation: Start minimal, expand based on need

3. **Port coverage**: Which ports can/should adopt this?
   - Unix: Yes (for testing)
   - STM32: Yes (custom implementation)
   - ESP32: No (FreeRTOS conflict)
   - RP2: No (multicore model)
   - Others: Evaluate case-by-case

4. **Version pinning**: Pin to specific Zephyr version or track latest?
   - Recommendation: Pin to stable release, update periodically

5. **Upstream contribution**: Should this be contributed to MicroPython upstream?
   - Recommendation: Prototype first, then propose to upstream

## Proof of Concept Plan

### POC Goal
Demonstrate Zephyr kernel integration working on Unix port within 1-2 days of development.

### POC Steps

1. **Setup** (2 hours)
   - Create extmod/zephyr_kernel/ structure
   - Create minimal zephyr_config.h
   - Identify minimal file list

2. **Unix integration** (4 hours)
   - Implement basic arch layer using pthread backing
   - Create mpthread_zephyr.c with mp_thread_* wrappers
   - Update Unix Makefile

3. **Testing** (2 hours)
   - Run basic threading tests
   - Debug issues
   - Compare with pthread implementation

### POC Success Criteria

- Unix build succeeds with MICROPY_ZEPHYR_THREADING=1
- Can create and join threads
- Mutex operations work
- At least basic tests pass (thread_create1.py, thread_exc2.py)

### Next Steps After POC

If POC succeeds:
1. Complete Unix port integration
2. Begin STM32 port work
3. Document integration guide
4. Consider upstream proposal

If POC reveals blockers:
1. Document findings
2. Evaluate alternative approaches
3. Decide whether to continue or pivot

## File Structure Summary

```
micropython/
├── extmod/
│   └── zephyr_kernel/              # NEW: Zephyr integration
│       ├── zephyr_config.h         # Fixed CONFIG_ definitions
│       ├── zephyr_kernel.h         # Integration API
│       ├── arch/                   # Arch-specific code
│       │   ├── arm/
│       │   └── x86/
│       └── kernel/
│           └── mpthread_zephyr.c   # mp_thread_* implementation
├── lib/
│   └── zephyr/                     # Git submodule (unchanged)
│       ├── kernel/                 # Source (788KB)
│       ├── arch/                   # Arch code (3.7MB)
│       └── include/                # Headers (22MB)
└── ports/
    ├── unix/
    │   ├── zephyr_arch_unix.c      # NEW: Unix arch layer
    │   ├── mpthreadport.c          # KEEP: Existing pthread impl
    │   └── Makefile                # MODIFY: Add Zephyr option
    └── stm32/
        ├── zephyr_arch_stm32.c     # NEW: STM32 arch layer
        ├── pybthread.[ch]          # REMOVE: Custom threading
        ├── mpthreadport.[ch]       # MODIFY: Use Zephyr
        └── Makefile                # MODIFY: Add Zephyr option
```

## Conclusion

Integrating Zephyr kernel for threading is technically feasible with the "minimal extraction" approach. Key success factors:

1. Start with Unix port as POC
2. Create fixed configuration (no dynamic Kconfig)
3. Provide minimal arch layer for each platform
4. Keep existing implementations during transition
5. Measure and optimize code size impact

The main risk is complexity of Zephyr kernel dependencies, but this can be managed through careful configuration and testing.

**Recommendation**: Proceed with POC on Unix port to validate approach before committing to full implementation.
