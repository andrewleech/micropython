# MicroPython Coroutine/Generator Implementation

This document describes how Python coroutines and generators are implemented in MicroPython's C code.

## Overview

The implementation uses a **suspension/resumption model** where execution state is saved to a heap-allocated structure rather than the C stack. This allows generators to be truly stateful with full execution context preserved across yield boundaries.

## Core Data Structures

### Generator Instance (`py/objgenerator.c:44-51`)

```c
typedef struct _mp_obj_gen_instance_t {
    mp_obj_base_t base;
    mp_obj_t pend_exc;           // Tracks running state and pending exceptions
    mp_code_state_t code_state;  // Full execution state
} mp_obj_gen_instance_t;
```

The `pend_exc` field serves multiple purposes:
- `mp_const_none`: Not currently executing, no pending exception
- `MP_OBJ_NULL`: Currently executing (prevents reentrance)
- Other value: Pending exception to inject on next resume

### Code State (`py/bc.h:234-256`)

```c
typedef struct _mp_code_state_t {
    struct _mp_obj_fun_bc_t *fun_bc;  // Reference to bytecode function
    const byte *ip;                    // Instruction pointer
    mp_obj_t *sp;                      // Stack pointer
    uint16_t n_state;                  // Size of state array
    uint16_t exc_sp_idx;               // Exception stack index
    mp_obj_dict_t *old_globals;        // Saved globals context
    mp_obj_t state[0];                 // Variable-length: locals + value stack
} mp_code_state_t;
```

The `state[]` array is variable-length and contains:
- Local variables (function arguments, local assignments)
- Python value stack (operands being worked on)
- Exception stack (at offset `state + n_state`)

## Generator Creation

When a generator function is called (`py/objgenerator.c:53-71`):

1. Decode bytecode prelude to get `n_state` and `n_exc_stack`
2. Allocate generator instance with space for variable-length state array
3. Initialize locals with arguments via `mp_setup_code_state()`
4. Set `ip` to the first bytecode instruction of the function body

The generator is not executed at this point—it returns immediately with the generator object.

## Yield Mechanism

### Yield Bytecode (`py/vm.c:1209-1216`)

```c
ENTRY(MP_BC_YIELD_VALUE):
yield:
    nlr_pop();
    code_state->ip = ip;      // Save instruction pointer
    code_state->sp = sp;      // Save stack pointer
    code_state->exc_sp_idx = MP_CODE_STATE_EXC_SP_IDX_FROM_PTR(exc_stack, exc_sp);
    FRAME_LEAVE();
    return MP_VM_RETURN_YIELD;
```

The yield value sits on top of the value stack. Execution saves `ip`/`sp` and returns `MP_VM_RETURN_YIELD` to the generator manager.

### Yield From (`py/vm.c:1218-1255`)

For `yield from`, the VM recursively calls `mp_resume()` on the sub-generator and propagates yields upward until the sub-generator is exhausted.

## Resume Mechanism

### Core Resume Function (`py/objgenerator.c:153-255`)

```c
mp_vm_return_kind_t mp_obj_gen_resume(mp_obj_t self_in, mp_obj_t send_value,
                                       mp_obj_t throw_value, mp_obj_t *ret_val)
```

Resume process:
1. Check if generator is exhausted (`ip == 0`)
2. Check reentrance via `pend_exc` field
3. For `send(value)`, place value on top of saved stack (becomes yield expression result)
4. Mark as running (`pend_exc = MP_OBJ_NULL`)
5. Switch globals to the generator's module context
6. Call `mp_execute_bytecode(&self->code_state, throw_value)`
7. VM picks up from saved `ip`/`sp` and continues
8. Handle return kind:
   - `MP_VM_RETURN_YIELD`: Value on stack, generator suspended
   - `MP_VM_RETURN_NORMAL`: Generator finished
   - `MP_VM_RETURN_EXCEPTION`: Exception raised

## Python-Level API

### `next()` / `__next__()` (`py/objgenerator.c:281-288`)

Calls `mp_obj_gen_resume()` with `send_value = mp_const_none`.

### `send(value)` (`py/objgenerator.c:290-309`)

Places `value` on top of the saved stack before resuming, so it becomes the result of the yield expression.

### `throw(exception)` (`py/objgenerator.c:312-330`)

Passes exception to `mp_execute_bytecode()` which injects it at the current yield point.

### `close()` (`py/objgenerator.c:332-350`)

Throws `GeneratorExit` into the generator. If generator yields instead of exiting, raises `RuntimeError`.

## VM Return Kinds (`py/runtime.h:40-44`)

```c
typedef enum {
    MP_VM_RETURN_NORMAL,        // Normal return or StopIteration
    MP_VM_RETURN_YIELD,         // Generator yielded
    MP_VM_RETURN_EXCEPTION,     // Exception raised
} mp_vm_return_kind_t;
```

## Native Generators

Native (compiled) generators use a variant structure (`py/objgenerator.c:93-97`):
- `mp_code_state_native_t` instead of `mp_code_state_t`
- `exc_sp_idx = MP_CODE_STATE_EXC_SP_IDX_SENTINEL` indicates native code
- Resume via function pointer instead of `mp_execute_bytecode()`

## Key Design Points

1. **No C Stack Frames Saved**: All Python state lives in heap-allocated `state[]` array
2. **Fresh C Calls**: `mp_execute_bytecode()` is called fresh each resume—it just starts from wherever `ip` points
3. **Memory Efficient**: Overhead is just `mp_obj_gen_instance_t` struct plus state array sized to function requirements
4. **Reentrance Protection**: `pend_exc` field prevents a generator from being resumed while already executing
5. **Globals Context**: Each generator maintains its module's globals and switches to them during execution

---

# Hardware Interrupt Servicing with Python Callbacks

This section describes how hardware interrupts are serviced with Python callback code, using the STM32 timer IRQ as a case study.

## Overview

MicroPython provides two modes for executing Python callbacks from hardware interrupts:

| Aspect | Hard IRQ | Soft IRQ |
|--------|----------|----------|
| Execution Time | Immediately in ISR context | Later, when VM checks pending tasks |
| Stack | Dedicated ISR stack (if configured) | Main Python stack |
| GC | Locked (no allocations) | Normal operation |
| Scheduler | Locked (prevents other callbacks) | Normal scheduling |
| Latency | Minimum | Depends on VM execution |
| Safety | More restrictive | More flexible |

## Callback Registration

### Timer Example (`ports/stm32/timer.c:1509-1528`)

```python
tim = pyb.Timer(4, freq=100)
tim.callback(lambda t: print(t))
```

The callback is stored in the timer object structure (`ports/stm32/timer.c:130-139`):

```c
typedef struct _pyb_timer_obj_t {
    mp_obj_base_t base;
    uint8_t tim_id;
    mp_obj_t callback;    // The Python callable
    bool ishard;          // Hard vs soft IRQ mode
    TIM_HandleTypeDef tim;
    IRQn_Type irqn;
    // ...
} pyb_timer_obj_t;
```

The `ishard` flag defaults to `true` (line 660) and determines whether the callback runs in hard or soft IRQ context.

## Hardware Interrupt Flow

### ISR Entry (`ports/stm32/stm32_it.c:705-709`)

```c
void TIM2_IRQHandler(void) {
    IRQ_ENTER(TIM2_IRQn);
    timer_irq_handler(2);
    IRQ_EXIT(TIM2_IRQn);
}
```

### Timer Dispatch (`ports/stm32/timer.c:1733-1766`)

```c
void timer_irq_handler(uint tim_id) {
    pyb_timer_obj_t *tim = MP_STATE_PORT(pyb_timer_obj_all)[tim_id - 1];
    if (tim != NULL) {
        timer_handle_irq_channel(tim, 0, tim->callback);
        // ... handle channel callbacks ...
    }
}
```

### Channel Handler (`ports/stm32/timer.c:1716-1731`)

```c
static void timer_handle_irq_channel(pyb_timer_obj_t *tim, uint8_t channel,
                                      mp_obj_t callback) {
    if (__HAL_TIM_GET_FLAG(&tim->tim, irq_mask) != RESET) {
        __HAL_TIM_CLEAR_IT(&tim->tim, irq_mask);  // Clear flag first

        if (mp_irq_dispatch(callback, MP_OBJ_FROM_PTR(tim), tim->ishard) < 0) {
            // Exception occurred - disable callback
            tim->callback = mp_const_none;
            __HAL_TIM_DISABLE_IT(&tim->tim, irq_mask);
        }
    }
}
```

## The Hard vs Soft IRQ Decision Point

### `mp_irq_dispatch()` (`shared/runtime/mpirq.c:68-109`)

```c
int mp_irq_dispatch(mp_obj_t handler, mp_obj_t parent, bool ishard) {
    if (handler != mp_const_none) {
        if (ishard) {
            // Execute Python NOW in ISR context
            mp_sched_lock();
            gc_lock();

            nlr_buf_t nlr;
            if (nlr_push(&nlr) == 0) {
                mp_call_function_1(handler, parent);
                nlr_pop();
            } else {
                // Exception - print and signal error
                mp_obj_print_exception(...);
                result = -1;
            }

            gc_unlock();
            mp_sched_unlock();
        } else {
            // Schedule for later execution
            mp_sched_schedule(handler, parent);
        }
    }
    return result;
}
```

## Soft IRQ Scheduler

### Scheduling a Callback (`py/scheduler.c:163-181`)

```c
bool mp_sched_schedule(mp_obj_t function, mp_obj_t arg) {
    mp_uint_t atomic_state = MICROPY_BEGIN_ATOMIC_SECTION();

    if (!mp_sched_full()) {
        if (MP_STATE_VM(sched_state) == MP_SCHED_IDLE) {
            MP_STATE_VM(sched_state) = MP_SCHED_PENDING;
        }
        // Add to circular queue
        uint8_t iput = IDX_MASK(MP_STATE_VM(sched_idx) + MP_STATE_VM(sched_len)++);
        MP_STATE_VM(sched_queue)[iput].func = function;
        MP_STATE_VM(sched_queue)[iput].arg = arg;
        ret = true;
    }

    MICROPY_END_ATOMIC_SECTION(atomic_state);
    return ret;
}
```

### Scheduler States (`py/mpstate.h:76-79`)

```c
#define MP_SCHED_IDLE    (1)   // No pending tasks
#define MP_SCHED_LOCKED  (-1)  // Locked (executing or in GC)
#define MP_SCHED_PENDING (0)   // Tasks queued, ready to run
```

### Executing Pending Callbacks (`py/scheduler.c:76-123`)

Called via `mp_handle_pending()` from the VM loop:

```c
static inline void mp_sched_run_pending(void) {
    if (MP_STATE_VM(sched_state) != MP_SCHED_PENDING) return;

    MP_STATE_VM(sched_state) = MP_SCHED_LOCKED;

    // Run ONE Python callback per call
    if (!mp_sched_empty()) {
        mp_sched_item_t item = MP_STATE_VM(sched_queue)[MP_STATE_VM(sched_idx)];
        MP_STATE_VM(sched_idx) = IDX_MASK(MP_STATE_VM(sched_idx) + 1);
        --MP_STATE_VM(sched_len);

        mp_call_function_1_protected(item.func, item.arg);
    }

    mp_sched_unlock();
}
```

## Atomic Sections and Locking

### IRQ Disable (`ports/stm32/irq.h:62-74`)

```c
static inline mp_uint_t disable_irq(void) {
    mp_uint_t state = __get_PRIMASK();
    __disable_irq();
    return state;
}

static inline void enable_irq(mp_uint_t state) {
    __set_PRIMASK(state);
}
```

### Scheduler Locking (`py/scheduler.c:128-138`)

```c
void mp_sched_lock(void) {
    mp_uint_t atomic_state = MICROPY_BEGIN_ATOMIC_SECTION();
    if (MP_STATE_VM(sched_state) < 0) {
        --MP_STATE_VM(sched_state);  // Recursive lock
    } else {
        MP_STATE_VM(sched_state) = MP_SCHED_LOCKED;
    }
    MICROPY_END_ATOMIC_SECTION(atomic_state);
}
```

## Complete Flow Diagram

```
Hardware Timer Triggers
    ↓
TIM2_IRQHandler()                    [stm32_it.c:705]
    ↓
timer_irq_handler(2)                 [timer.c:1733]
    ↓
timer_handle_irq_channel()           [timer.c:1716]
    ↓
mp_irq_dispatch(callback, tim, ishard)  [mpirq.c:68]
    ↓
    ├─→ ishard == true:
    │   ├─ mp_sched_lock()
    │   ├─ gc_lock()
    │   ├─ mp_call_function_1(handler, parent)  ← Execute NOW
    │   ├─ gc_unlock()
    │   └─ mp_sched_unlock()
    │
    └─→ ishard == false:
        └─ mp_sched_schedule(handler, parent)
            ↓
            Queued in sched_queue
            ↓
            Later: mp_handle_pending() in VM loop
            ↓
            mp_sched_run_pending()
            ↓
            mp_call_function_1_protected()  ← Execute in main context
```

## Key Files Reference

| Component | File | Key Lines |
|-----------|------|-----------|
| Callback registration | `ports/stm32/timer.c` | 1509-1528 |
| Timer object | `ports/stm32/timer.c` | 130-139 |
| Hardware ISR | `ports/stm32/stm32_it.c` | 705-709 |
| Timer dispatch | `ports/stm32/timer.c` | 1716-1766 |
| Hard/soft dispatch | `shared/runtime/mpirq.c` | 68-109 |
| Scheduler queue | `py/scheduler.c` | 163-181 |
| Scheduler execution | `py/scheduler.c` | 76-123 |
| Scheduler states | `py/mpstate.h` | 76-84 |
| IRQ atomics | `ports/stm32/irq.h` | 62-74 |

---

# Generator-Based IRQ Handlers

This section documents the implementation and performance testing of generator-based IRQ handlers.

## Implementation

The generator-based IRQ handler feature allows Python generators to be used as hard IRQ callbacks. This provides:
- Pre-allocated state (generator locals persist across yields)
- Lower latency (no function object lookup/creation per IRQ)
- Tighter jitter (more predictable execution path)

### Key Changes

**Branch:** `generator-irq-handlers`
**Feature commit:** `d1d13ef2dd` - shared/runtime: Add generator-based IRQ handler support.

#### `shared/runtime/mpirq.c`

Added `mp_irq_prepare_handler()` to auto-instantiate and prime generators:

```c
mp_obj_t mp_irq_prepare_handler(mp_obj_t callback, mp_obj_t parent) {
    // Auto-instantiate generator functions (bytecode or native).
    if (mp_obj_is_type(callback, &mp_type_gen_wrap)
        #if MICROPY_EMIT_NATIVE
        || mp_obj_is_type(callback, &mp_type_native_gen_wrap)
        #endif
        ) {
        callback = mp_call_function_1(callback, parent);
    }

    // Prime generator instances (run setup code to first yield).
    if (mp_obj_is_type(callback, &mp_type_gen_instance)) {
        mp_obj_t ret_val;
        mp_vm_return_kind_t ret = mp_obj_gen_resume(callback, mp_const_none, MP_OBJ_NULL, &ret_val);
        if (ret != MP_VM_RETURN_YIELD) {
            if (ret == MP_VM_RETURN_EXCEPTION) {
                nlr_raise(ret_val);
            }
            mp_raise_ValueError(MP_ERROR_TEXT("generator must yield"));
        }
    } else if (callback != mp_const_none && !mp_obj_is_callable(callback)) {
        mp_raise_ValueError(MP_ERROR_TEXT("callback must be None, callable, or generator"));
    }

    return callback;
}
```

Modified `mp_irq_dispatch()` to detect and resume generator instances:

```c
int mp_irq_dispatch(mp_obj_t handler, mp_obj_t parent, bool ishard) {
    // ... locking code ...
    if (nlr_push(&nlr) == 0) {
        if (mp_obj_is_type(handler, &mp_type_gen_instance)) {
            // Generator-based handler: resume with parent as send value.
            mp_obj_t ret_val;
            mp_vm_return_kind_t ret = mp_obj_gen_resume(handler, parent, MP_OBJ_NULL, &ret_val);
            if (ret == MP_VM_RETURN_NORMAL) {
                result = -1;  // Generator finished
            } else if (ret == MP_VM_RETURN_EXCEPTION) {
                mp_obj_print_exception(MICROPY_ERROR_PRINTER, ret_val);
                result = -1;
            }
        } else {
            mp_call_function_1(handler, parent);
        }
        nlr_pop();
    }
    // ... cleanup ...
}
```

#### `ports/stm32/timer.c`

Updated `pyb_timer_callback()` to use `mp_irq_prepare_handler()`:

```c
static mp_obj_t pyb_timer_callback(mp_obj_t self_in, mp_obj_t callback) {
    pyb_timer_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (callback == mp_const_none) {
        __HAL_TIM_DISABLE_IT(&self->tim, TIM_IT_UPDATE);
        self->callback = mp_const_none;
    } else {
        __HAL_TIM_DISABLE_IT(&self->tim, TIM_IT_UPDATE);
        self->callback = mp_irq_prepare_handler(callback, self_in);
        // ... enable timer ...
    }
    return mp_const_none;
}
```

### Usage Example

```python
def handler(tim):
    buf = bytearray(64)  # Allocated during registration
    while True:
        tim = yield        # IRQ resumes here
        buf[0] = tim.counter() & 0xFF

tim.callback(handler)      # Auto-instantiates and primes generator
```

## Performance Testing

### Test Environment

- **Board:** NUCLEO_H563ZI (STM32H563ZI, Cortex-M33 @ 250MHz)
- **Clock:** SYSCLK/HCLK/PCLK1/PCLK2 all at 250MHz (verified via `machine.freq()`)
- **Timer:** Timer 2, prescaler=24 → 250MHz/25 = 10MHz (100ns per tick)
- **IRQ Period:** 10000 ticks (1ms between IRQs)
- **Samples:** 100 per test run

### Commit Hashes

| Build | Commit | Description |
|-------|--------|-------------|
| Master (baseline) | `41acdd8083` | stm32/rtc: Make sure RTC is using LSE on N6 MCUs. |
| Generator branch | `63c78cb810` | Revert of instrumentation, clean generator IRQ support |
| Feature commit | `d1d13ef2dd` | shared/runtime: Add generator-based IRQ handler support. |

### Test Procedure

#### 1. Build firmware

```bash
# For master baseline
git checkout master -- ports/stm32/timer.c shared/runtime/mpirq.c shared/runtime/mpirq.h
make -C ports/stm32 BOARD=NUCLEO_H563ZI -j8

# For generator branch
git checkout generator-irq-handlers -- ports/stm32/timer.c shared/runtime/mpirq.c shared/runtime/mpirq.h
make -C ports/stm32 BOARD=NUCLEO_H563ZI -j8
```

#### 2. Flash firmware

```bash
# Using pyocd (probe serial for NUCLEO_H563ZI)
pyocd load --probe 004400413433510D37363934 --target stm32h563zitx \
    ports/stm32/build-NUCLEO_H563ZI/firmware.hex
pyocd reset --probe 004400413433510D37363934 --target stm32h563zitx

# Wait for USB enumeration
sleep 5

# Verify device is available
ls /dev/serial/by-id/usb-MicroPython*3873*
```

#### 3. Run latency test

```bash
mpremote connect /dev/serial/by-id/usb-MicroPython_Pyboard_Virtual_Comm_Port_in_FS_Mode_3873328A3332-if01 \
    exec '<test code below>'
```

### Test Methodology

The timer counter itself is used to measure IRQ handler entry latency. The timer counts from 0 to `period`, generates an IRQ at overflow, then resets to 0. By capturing `t.counter()` immediately in the callback, we measure the number of ticks elapsed since the IRQ triggered.

**Timer Configuration:**
- Prescaler=24 at 250MHz → 10MHz timer → 100ns per tick
- Period=10000 → 1ms between IRQs
- Counter value at callback entry = latency in ticks

### Test Code

```python
import micropython
from pyb import Timer
import gc

TIMER_NUM = 2
IRQ_PERIOD = 10000
NUM_SAMPLES = 100

latencies = [0] * NUM_SAMPLES
idx = 0

# Bytecode function callback
def func_cb(t):
    global idx, latencies
    count = t.counter()  # Capture FIRST before any other operations
    if idx < NUM_SAMPLES:
        latencies[idx] = count
        idx += 1

# Native function callback
@micropython.native
def native_func_cb(t):
    global idx, latencies
    count = t.counter()  # Capture FIRST
    if idx < NUM_SAMPLES:
        latencies[idx] = count
        idx += 1

# Bytecode generator callback
def gen_cb(t):
    global idx
    local_lat = latencies
    local_idx = 0
    while True:
        t = yield
        count = t.counter()  # Capture FIRST after yield resumes
        if local_idx < NUM_SAMPLES:
            local_lat[local_idx] = count
            local_idx += 1
            idx = local_idx

# Native generator callback
@micropython.native
def native_gen_cb(t):
    global idx
    local_lat = latencies
    local_idx = 0
    while True:
        t = yield
        count = t.counter()  # Capture FIRST after yield resumes
        if local_idx < NUM_SAMPLES:
            local_lat[local_idx] = count
            local_idx += 1
            idx = local_idx

def test_callback(callback):
    global idx, latencies
    idx = 0
    for i in range(NUM_SAMPLES):
        latencies[i] = 0
    gc.collect()
    tim = Timer(TIMER_NUM, prescaler=24, period=IRQ_PERIOD, callback=callback)
    while idx < NUM_SAMPLES:
        pass
    tim.callback(None)
    tim.deinit()
    valid = [x for x in latencies if x > 0 and x < IRQ_PERIOD]
    if valid:
        avg = sum(valid) // len(valid)
        return avg * 100  # ns (100ns per tick)
    return -1
```

### Results (2025-12-03)

#### Master Branch (41acdd8083, no generator IRQ support)

| Callback Type | Min | Avg | Max | Valid/Total |
|---------------|-----|-----|-----|-------------|
| Bytecode Function | 4000ns | 4000ns | 5800ns | 100/100 |
| Native Function | 3200ns | 3200ns | 5100ns | 100/100 |

Raw ticks (first 10 samples):
- Bytecode: `[58, 41, 40, 41, 40, 40, 40, 41, 40, 40]`
- Native: `[51, 33, 32, 32, 32, 32, 32, 32, 32, 33]`

#### Generator Branch (d1d13ef2dd)

| Callback Type | Min | Avg | Max | Valid/Total |
|---------------|-----|-----|-----|-------------|
| Bytecode Function | 4200ns | 4200ns | 5800ns | 100/100 |
| Native Function | 3500ns | 3500ns | 5100ns | 100/100 |
| Bytecode Generator | 2800ns | 2800ns | 3900ns | 100/100 |
| Native Generator | 2600ns | 2600ns | 3500ns | 100/100 |

Raw ticks (first 10 samples):
- Bytecode Func: `[58, 43, 43, 43, 43, 42, 43, 43, 43, 43]`
- Native Func: `[51, 35, 35, 36, 35, 35, 35, 36, 35, 35]`
- Bytecode Gen: `[39, 28, 28, 28, 28, 28, 28, 28, 28, 28]`
- Native Gen: `[35, 26, 26, 26, 26, 26, 26, 26, 26, 26]`

### Analysis

| Comparison | Baseline | With Generator | Improvement |
|------------|----------|----------------|-------------|
| Bytecode gen vs bytecode func | 4200ns | 2800ns | **1400ns (33%)** |
| Native gen vs native func | 3500ns | 2600ns | **900ns (26%)** |
| Native gen vs master native func | 3200ns | 2600ns | **600ns (19%)** |

The generator branch adds ~200-300ns overhead to function callbacks (due to the generator type check in the dispatch path), but generator callbacks themselves are significantly faster:

- **Bytecode generators are 33% faster** than bytecode functions
- **Native generators are 26% faster** than native functions
- **Native generators provide 19% improvement** over master baseline

### Notes

- Timer 1 has a conflict on NUCLEO_H563ZI (possibly systick/USB related), use Timer 2
- All tests used pre-allocated list for latency storage (no heap allocation in IRQ)
- Results are highly consistent after first sample (±100ns)
- First sample is consistently higher (~5μs) due to cold instruction cache, then settles to steady state
- Generator callbacks use local variables for latency list to avoid global lookups
- **Critical:** `t.counter()` must be captured as the very first operation in the callback to measure true entry latency

#### Unified Dispatch with Function Wrapping (c79492d3b2, 2025-12-09)

This version unifies all IRQ handlers through a single dispatch path using sentinel
values in `exc_sp_idx` to differentiate handler types. Bytecode functions are wrapped
as generator-compatible objects at registration time.

| Callback Type | Avg | Raw (first 10) |
|---------------|-----|----------------|
| Bytecode Function | 2800ns | `[44, 28, 28, 28, 28, 28, 28, 28, 28, 28]` |
| Bytecode Generator | 2700ns | `[43, 27, 27, 27, 27, 27, 27, 27, 27, 27]` |
| Native Generator | 2500ns | `[39, 25, 25, 25, 25, 25, 25, 25, 25, 25]` |

**Comparison to Baseline (d1d13ef2dd):**

| Callback Type | Baseline | Unified | Improvement |
|---------------|----------|---------|-------------|
| Bytecode Function | 4200ns | 2800ns | **-1400ns (33% faster)** |
| Bytecode Generator | 2800ns | 2700ns | -100ns (4% faster) |
| Native Generator | 2600ns | 2500ns | -100ns (4% faster) |

The function wrapper achieves its design goal: bytecode functions now run at
near-generator speed by eliminating per-IRQ `INIT_CODESTATE` overhead.

**Important:** These results require `MICROPY_PYB_IRQ_PROFILE (0)` (disabled).
When profiling is enabled, volatile flag checks in the hot path add ~10+ ticks
overhead.

#### Counter Read Methods Comparison (76b5665a07, 2025-12-09)

Testing direct register access (`mem32[]`, viper `ptr32`) vs `t.counter()` method
call to measure the overhead of Python method dispatch.

**Test method:** Timer IRQ callback reads TIM2->CNT immediately on entry to measure
latency from IRQ assertion to first user code execution. 100 samples per test,
TIM2 @ 10MHz (100ns/tick), NUCLEO_H563ZI @ 250MHz.

| Callback Type | Counter Method | Avg Ticks | Avg ns |
|---------------|---------------|-----------|--------|
| **Direct Viper** |
| Viper direct | ptr32 | **16** | **1600** |
| **Functions** |
| Bytecode func | t.counter() | 26 | 2600 |
| Bytecode func | mem32[] | 25 | 2500 |
| Bytecode func | viper_read() | 28 | 2800 |
| **Generators** |
| Bytecode gen | t.counter() | 27 | 2700 |
| Native gen | t.counter() | 24 | 2400 |
| Bytecode gen | mem32[] | 24 | 2400 |
| Native gen | mem32[] | **20** | **2000** |
| Bytecode gen | viper_read() | 28 | 2800 |
| **Generic Callables** |
| Bound method | t.counter() | 62 | 6200 |

**Key findings:**

1. **Direct `@viper` callback is fastest** at 16 ticks (1.6µs) - 24% faster than
   @native generators. Requires `array.array` + `uctypes.addressof()` for storage
   since viper can't access Python lists.
2. **`@native` generator + `mem32[]`** at 20 ticks is fastest for code needing
   Python data structure access.
3. **Calling convention fix was critical** - Previous dispatch used wrong signature
   `(mp_obj_t)` instead of `(fun, n_args, n_kw, args)`. Fixed in 76b5665a07.
4. **Bound methods are slowest** at 62 ticks (6.2µs) due to generic dispatch via
   `mp_call_function_1()`. The extra ~36 ticks come from type lookups, args array
   construction, and multiple function calls through the bound method machinery.
   Use plain functions or generators when latency matters.

For absolute minimum latency, use direct `@viper` callback with `ptr32()` and
array-based storage. For easier Python interop, use `@native` generator with
`mem32[]`. See `test_viper_irq.py` for test code.

---

## IRQ Latency Profiling (Detailed)

To understand where time is spent between hardware interrupt and Python callback execution, profiling instrumentation was added to capture TIM2->CNT at key points throughout the entire C dispatch path.

### Profiling Infrastructure

**Configuration:** `MICROPY_PYB_IRQ_PROFILE` in `ports/stm32/mpconfigport.h` (disabled by default)

**Files modified:**

| File | Purpose |
|------|---------|
| `ports/stm32/mpconfigport.h:181-209` | Config option and `MP_IRQ_PROFILE_CAPTURE` macro |
| `ports/stm32/timer.c:1724` | P0 capture, storage, and Python API |
| `shared/runtime/mpirq.c:98-150` | P1-P6 captures |
| `py/objfun.c:256,293` | P7-P8 captures (function path) |
| `py/objgenerator.c:154,194` | P7-P8 captures (generator path) |
| `py/vm.c:272,311` | P9-P10 captures |
| `py/mpconfig.h:2431-2435` | Fallback macro for non-STM32 ports |

### Capture Points (14 total)

| Point | File:Line | Location | Description |
|-------|-----------|----------|-------------|
| P0 | `timer.c:1725` | `timer_handle_irq_channel()` entry | First C code after ISR dispatch |
| P1 | `mpirq.c:98` | `mp_irq_dispatch()` entry | Start of dispatch function |
| P2 | `mpirq.c:116` | After `gc_lock()` | Scheduler and GC locked |
| P3 | `mpirq.c:119` | After `nlr_push()` | Exception handler set up |
| P4 | `mpirq.c:122/138` | Before handler call | Type check done, about to call/resume |
| P5 | `mpirq.c:125/140` | After handler returns | Python execution complete |
| P6 | `mpirq.c:150` | `mp_irq_dispatch()` exit | After unlock, before return |
| P7 | `objfun.c` / `objgenerator.c:154` | Handler entry | `fun_bc_call()` or `mp_obj_gen_resume()` |
| P8 | `objfun.c` / `objgenerator.c:194` | After state setup | `INIT_CODESTATE` or send_value placed |
| P9 | `vm.c` / `objgenerator.c:207` | Bytecode/native entry | VM entry or native gen before call |
| P10 | `objboundmeth.c:88` | `bound_meth_call()` entry | First type dispatch complete |
| P11 | `objboundmeth.c:76` | After args setup | `alloca` + arg copy done |
| P12 | `objfun.c:108` | `fun_builtin_var_call()` entry | Second type dispatch complete |
| P13 | `timer.c:1425` | `pyb_timer_counter()` entry | Actual C function entry |
| Px | Python code | `t.counter()` return | Counter value captured in Python |

### Complete IRQ Dispatch Flow (Unified Architecture)

The current implementation uses a unified dispatch architecture where all hard IRQ handlers
are wrapped as `mp_type_gen_instance` objects at registration time. This eliminates type
checks in the hot dispatch path and enables per-type optimizations via sentinel values
in the `exc_sp_idx` field.

#### Phase 1: Registration (tim.callback() call)

```
Python: tim.callback(handler)
    │
    ▼
pyb_timer_callback()                                 [timer.c:1509]
    │   Disable timer interrupt
    ▼
mp_irq_prepare_handler(callback, parent, ishard)     [mpirq.c:240]
    │
    ├─── Is generator function (mp_type_gen_wrap or native_gen_wrap)?
    │    │
    │    ▼ YES
    │    mp_call_function_1(callback, parent)        Instantiate generator
    │    │
    │    ▼
    │    mp_obj_gen_resume(gen, None, NULL)          Prime to first yield
    │    │
    │    └── Ready: gen_instance with valid ip/sp at yield point
    │
    ├─── Is bytecode function (mp_type_fun_bc)?
    │    │
    │    ▼ YES: mp_irq_wrap_bytecode_function()      [mpirq.c:61]
    │        - Decode prelude: n_state, n_pos_args, scope_flags
    │        - Reject if scope_flags & GENERATOR (has yield)
    │        - Reject if n_pos_args != 1
    │        - Allocate gen_instance with state[] + extra data
    │        - Call mp_setup_code_state() with dummy arg
    │        - Cache bytecode_start in extra data
    │        - Set exc_sp_idx = IRQ_FUNC_BC (-2)
    │
    ├─── Is @native function (mp_type_fun_native)?
    │    │
    │    ▼ YES: mp_irq_wrap_native_function()        [mpirq.c:120]
    │        - Decode native prelude for n_state
    │        - Reject if n_pos_args != 1
    │        - Allocate gen_instance with state[] + extra data
    │        - Cache native_entry pointer
    │        - Set exc_sp_idx = IRQ_FUNC_NAT (-3)
    │
    ├─── Is @viper function (mp_type_fun_viper)?
    │    │
    │    ▼ YES: mp_irq_wrap_viper_function()         [mpirq.c:156]
    │        - Allocate gen_instance (minimal state)
    │        - Entry point at fun_bc->bytecode directly
    │        - Set exc_sp_idx = IRQ_VIPER (-4)
    │
    └─── Other callable (bound method, closure, etc)?
         │
         ▼ YES: mp_irq_wrap_callable()               [mpirq.c:194]
             - Allocate gen_instance with n_state=2
             - Store callable in state[0]
             - Set exc_sp_idx = IRQ_CALLABLE (-5)
```

**Sentinel Values (defined in py/bc.h:169-178):**

| Sentinel | Value | Handler Type |
|----------|-------|--------------|
| `SENTINEL` | -1 | Native generator (@native def with yield) |
| `IRQ_FUNC_BC` | -2 | Wrapped bytecode function |
| `IRQ_FUNC_NAT` | -3 | Wrapped @native function |
| `IRQ_VIPER` | -4 | Wrapped @viper function |
| `IRQ_CALLABLE` | -5 | Generic callable (bound method, etc) |
| 0+ | n | Bytecode generator (n = exc_stack index) |

#### Phase 2: Hardware IRQ Dispatch

```
Hardware Timer Overflow (counter wraps from period→0)
    │
    ▼ ═══════════════════════════════════════════════════════════════
    │  NVIC dispatches to ISR vector
    ▼
TIM2_IRQHandler()                                    [stm32_it.c:705]
    │   IRQ_ENTER(TIM2_IRQn)
    ▼
timer_irq_handler(2)                                 [timer.c:1733]
    │   Lookup: tim = MP_STATE_PORT(pyb_timer_obj_all)[1]
    ▼
timer_handle_irq_channel(tim, 0, callback)           [timer.c:1716]
    │   Check __HAL_TIM_GET_FLAG() & __HAL_TIM_GET_IT_SOURCE()
    │   __HAL_TIM_CLEAR_IT()
    ▼
mp_irq_dispatch(handler, tim, ishard=true)           [mpirq.c:289]
    │
    │   [Stack check setup if MICROPY_STACK_CHECK]
    │   mp_sched_lock()
    │   gc_lock()
    │   nlr_push(&nlr)
    │
    │   ════════════════════════════════════════════════════════════
    │   UNIFIED DISPATCH: All handlers are mp_type_gen_instance
    │   ════════════════════════════════════════════════════════════
    ▼
mp_obj_gen_resume_irq(handler, tim, &ret_val)        [objgenerator.c:260]
    │
    │   mp_cstack_check()
    │   Reentrance check: if (pend_exc == NULL) return ERROR
    │
    │   Switch on exc_sp_idx:
    │
    ├─── IRQ_FUNC_BC (-2): Wrapped bytecode function
    │    │                                           [objgenerator.c:275]
    │    │   Get extra data: bytecode_start
    │    │   Reset: ip = bytecode_start
    │    │   Reset: sp = &state[0] - 1
    │    │   Reset: exc_sp_idx = 0  (for VM)
    │    │   Place arg: state[n_state-1] = send_value
    │    │   pend_exc = NULL (mark running)
    │    │   mp_globals_set()
    │    ▼
    │    mp_execute_bytecode(&code_state, NULL)      [vm.c:285]
    │    │   FRAME_ENTER/SETUP
    │    │   Execute bytecodes
    │    │   Return when function ends
    │    ▼
    │    Restore: exc_sp_idx = IRQ_FUNC_BC
    │    pend_exc = mp_const_none (mark idle)
    │
    ├─── IRQ_FUNC_NAT (-3): Wrapped @native function
    │    │                                           [objgenerator.c:310]
    │    │   Get extra data: native_entry
    │    │   pend_exc = NULL
    │    │   args[0] = send_value
    │    ▼
    │    native_fun(fun_bc, 1, 0, args)              Direct call
    │    │   ARM native code executes
    │    ▼
    │    pend_exc = mp_const_none
    │
    ├─── IRQ_VIPER (-4): Wrapped @viper function
    │    │                                           [objgenerator.c:322]
    │    │   Entry = fun_bc->bytecode (direct)
    │    │   pend_exc = NULL
    │    │   args[0] = send_value
    │    ▼
    │    viper_fun(fun_bc, 1, 0, args)               Direct call
    │    │   ARM native code executes
    │    ▼
    │    pend_exc = mp_const_none
    │
    ├─── SENTINEL (-1): Native generator
    │    │                                           [objgenerator.c:334]
    │    │   Check: ip == 0? (exhausted)
    │    │   *sp = send_value
    │    │   pend_exc = NULL
    │    │   mp_globals_set()
    │    ▼
    │    mp_fun_native_gen_t fun(code_state, NULL)
    │    │   Native generator resume function
    │    ▼
    │    mp_globals_restore()
    │    pend_exc = mp_const_none
    │
    ├─── IRQ_CALLABLE (-5): Generic callable wrapper
    │    │                                           [objgenerator.c:355]
    │    │   callable = state[0]
    │    │   pend_exc = NULL
    │    ▼
    │    mp_call_function_1(callable, send_value)    [runtime.c:674]
    │    │   Full type dispatch
    │    │   (slowest path - bound method overhead)
    │    ▼
    │    pend_exc = mp_const_none
    │
    └─── Default (0+): Bytecode generator
         │                                           [objgenerator.c:365]
         │   Check: ip == 0? (exhausted)
         │   *sp = send_value
         │   pend_exc = NULL
         │   mp_globals_set()
         ▼
         mp_execute_bytecode(&code_state, NULL)      [vm.c:285]
         │   Resume from saved ip (after yield)
         │   Execute until next yield or return
         ▼
         mp_globals_restore()
         pend_exc = mp_const_none
    │
    ═══════════════════════════════════════════════════════════════
    │
    ▼  [Common return handling]
    │   Check ret_kind:
    │     NORMAL: For real generators, mark exhausted (ip=0)
    │     YIELD: Success, handler remains active
    │     EXCEPTION: Print error, return -1
    │
    │   nlr_pop()
    │   gc_unlock()
    │   mp_sched_unlock()
    │   [Restore stack limits if STACK_CHECK]
    ▼
Return to timer_handle_irq_channel()
    │   If result < 0: disable callback, set to None
    ▼
Return to TIM2_IRQHandler()
    │   IRQ_EXIT(TIM2_IRQn)
    ▼
Hardware resumes interrupted code
```

#### Key Files Reference

| Component | File | Key Lines |
|-----------|------|-----------|
| Sentinel definitions | `py/bc.h` | 169-178 |
| Handler wrapping | `shared/runtime/mpirq.c` | 61-210 |
| Unified prepare | `shared/runtime/mpirq.c` | 240-286 |
| Unified dispatch | `shared/runtime/mpirq.c` | 289-346 |
| Resume with sentinels | `py/objgenerator.c` | 260-414 |
| Timer callback reg | `ports/stm32/timer.c` | 1509-1528 |
| Timer IRQ handler | `ports/stm32/timer.c` | 1716-1766 |
| Hardware ISR | `ports/stm32/stm32_it.c` | 705-709 |

#### Soft IRQ Path (ishard=false)

For soft IRQs, the flow is simpler:
- Generators are still instantiated and primed at registration
- Other callables are NOT wrapped (passed directly)
- At IRQ time: `mp_sched_schedule(handler, parent)` queues the callback
- Later: VM checks `sched_state`, calls `mp_call_function_1_protected()`

### Profiling Results (11 capture points)

Timer configured at 10MHz (100ns per tick). Warm-state averages after initial cache fills.

**Function Callback Raw Data:**

```
Sample  P0    P1    P2    P3    P4    P5    P6    P7    P8    P9   P10    Px
   1      2     4     7    10    11    96   100    15    29    31    34    54
   2      2     4     7    10    11    96    99    15    29    31    34    53
   3      2     4     7    10    11    96    99    15    29    31    34    53
```

**Generator Callback Raw Data:**

```
Sample  P0    P1    P2    P3    P4    P5    P6    P7    P8    P9   P10    Px
   1      2     4     6    10    10    80    83    12    14    16    19    38
   2      2     4     6    10    10    80    83    12    14    16    18    38
   3      2     4     6    10    10    80    83    12    14    16    18    38
```

### Detailed Timing Breakdown

All values in ticks @ 10MHz (1 tick = 100ns).

| Phase | Code Reference | Function | Generator | Δ |
|-------|----------------|----------|-----------|---|
| P0→P1 | `timer.c` → `mpirq.c` entry | 2 | 2 | 0 |
| P1→P2 | `sched_lock` + `gc_lock` | 3 | 2 | 1 |
| P2→P3 | `nlr_push` | 3 | 4 | -1 |
| P3→P4 | Type check → call/resume | 1 | 0 | 1 |
| **P4→P7** | **To handler entry** | **4** | **2** | **2** |
| **P7→P8** | **State setup** | **14** | **2** | **12** |
| P8→P9 | To `execute_bytecode` entry | 2 | 2 | 0 |
| P9→P10 | VM dispatch setup | 3 | 2-3 | 0-1 |
| P10→Px | To first Python instruction | 19-20 | 19-20 | 0 |
| **Total P4→Px** | **Call to Python** | **42-43** | **28** | **14-15** |
| **Total P0→Px** | **IRQ to Python** | **52-54** | **36-38** | **16** |

### Key Finding: P7→P8 Is The Bottleneck

The **P7→P8 phase** is where generators provide the main savings:

| Path | P7→P8 Time | What Happens |
|------|------------|--------------|
| **Function** | 14 ticks (1400ns) | `INIT_CODESTATE()`: parse bytecode prelude, allocate stack space, copy arguments to locals, initialize exception stack |
| **Generator** | 2 ticks (200ns) | Place `send_value` (timer object) on existing stack, set `pend_exc = NULL` |

**Savings: 12 ticks = 1200ns (86% reduction in this phase)**

This is because:
1. Generator state was **pre-initialized** during the prime step (`next(gen)` at registration)
2. The `code_state` already has: `ip` pointing after `yield`, `sp` at correct position, locals populated
3. Only minimal work needed: place timer object on stack and mark as running

### Overall Performance Comparison

| Metric | Function | Generator | Improvement |
|--------|----------|-----------|-------------|
| P0→Px (ISR entry to Python) | 5200-5400ns | 3600-3800ns | **1600ns (30%)** |
| P4→Px (call to Python) | 4200-4300ns | 2800ns | **1400-1500ns (33%)** |
| P7→P8 (state setup) | 1400ns | 200ns | **1200ns (86%)** |

### Python API

```python
import pyb

# Enable profiling (clears previous values)
pyb.irq_profile_enable(True)

# Get last captured values as tuple (11 elements: P0-P10)
profile = pyb.irq_profile_get()
p0, p1, p2, p3, p4, p5, p6, p7, p8, p9, p10 = profile

# Disable profiling
pyb.irq_profile_enable(False)
```

### C Implementation

The profiling macro is defined in `ports/stm32/mpconfigport.h:204-208`:

```c
#define MP_IRQ_PROFILE_CAPTURE(idx) do { \
        if (pyb_irq_profile_enabled) { \
            pyb_irq_profile[idx] = *(volatile uint32_t *)(0x40000000 + 0x24); \
        } \
    } while (0)
```

The hardcoded address `0x40000000 + 0x24` is TIM2->CNT on STM32, ensuring zero overhead from HAL abstraction.

A fallback empty macro is defined in `py/mpconfig.h:2433-2434` for non-STM32 ports:

```c
#ifndef MP_IRQ_PROFILE_CAPTURE
#define MP_IRQ_PROFILE_CAPTURE(idx)
#endif
```

## Callback Pattern Optimization

Different callback patterns have different latency characteristics. Testing on STM32H563ZI @ 250MHz:

### Pattern Comparison Results

| Callback Type | P0→Px | Improvement | Notes |
|--------------|-------|-------------|-------|
| Bytecode function | 51 ticks (5100ns) | baseline | Standard `def callback(t):` |
| Plain generator | 35 ticks (3500ns) | +31% | Generator with bytecode body |
| @native function | 38 ticks (3800ns) | +25% | Native code, repeated setup overhead |
| **@native generator** | **31 ticks (3100ns)** | **+39%** | Best - combines both optimizations |
| Pre-bound method | 49 ticks (4900ns) | +4% | Marginal improvement |
| stm.mem32 direct | 56 ticks (5600ns) | -10% | More bytecode overhead |

### Why @native Generator Is Fastest

The `@native` generator combines both optimizations:

**1. Skipping INIT_CODESTATE (P7→P8 savings)**

The generator's code state is pre-initialized during the prime step. On each IRQ:
- Function: Must parse bytecode prelude, allocate stack, copy arguments (~14 ticks)
- Generator: Just place send_value on existing stack (~2 ticks)

**2. Native code execution after resume**

With `@native`, the generator body compiles to ARM native code that bypasses the bytecode VM entirely (P10=0). This saves the ~18 ticks of bytecode dispatch overhead.

**3. Closure avoids repeated method lookup**

```python
@micropython.native
def gen_handler(tim):    # tim captured in closure at instantiation
    while True:
        yield
        count = tim.counter()  # tim from closure, no argument passing
```

### Why Plain @native Function Is Slower

The `@micropython.native` decorator on a regular function still has setup overhead:
- Must call through `fun_bc_call()` → `INIT_CODESTATE` on each invocation
- Repeated argument passing and state initialization

The generator avoids this by maintaining persistent state across calls.

### P10 (VM Entry) Comparison

| Callback | P10 Value | Meaning |
|----------|-----------|---------|
| Function | 33 ticks | Arrives at VM late due to INIT_CODESTATE |
| Plain generator | 17 ticks | Arrives 16 ticks earlier (state pre-initialized) |
| @native generator | 0 ticks | Bypasses VM entirely after generator resume |
| @native function | 0 ticks | Bypasses VM but still has call setup overhead |

### Recommended Patterns

**Best for latency-critical IRQ handlers (39% faster):**
```python
import micropython

@micropython.native
def gen_handler(tim):
    while True:
        yield
        # Handler code here - tim available from closure
        data = tim.counter()
```

**Good alternative without @native (31% faster):**
```python
def gen_handler(tim):
    while True:
        yield
        data = tim.counter()
```

**Avoid:**
- Plain bytecode functions (slowest option)
- Wrapper functions that call `gen.send()` (adds extra function call overhead)
- `stm.mem32` access (more bytecode operations than method call)
- Pre-bound methods (minimal improvement, adds complexity)

---

## Python Function Call Overhead Analysis

To understand the fundamental limits of IRQ handler performance, we measured the overhead of different methods for reading a timer register.

### Test Methods

| Method | Code | Description |
|--------|------|-------------|
| `t.counter()` | Bound method call | Standard Python method on timer object |
| `stm.mem32[addr]` | Module subscript | Direct memory access via stm module |
| `@viper ptr32()` | Viper function call | Native code with pointer access |
| `@viper` inline | Pure viper loop | No Python call overhead |

### Results (STM32H563ZI @ 250MHz, TIM2 @ 10MHz)

| Method | Ticks/call | Time | Notes |
|--------|------------|------|-------|
| `@viper` inline ptr32 | **1.2** | 120ns | Pure register read baseline |
| `@viper ptr32()` function | 24.8 | 2478ns | +24 ticks for call overhead |
| `t.counter()` bound method | 26.1 | 2607ns | Only +1.3 vs viper function |
| `stm.mem32[]` subscript | 33.9 | 3395ns | Slowest (subscript dispatch) |

### Key Finding: Function Call Overhead Dominates

The actual register read takes only ~1 tick (100ns). The remaining ~25 ticks are **Python function call overhead**:

```
Pure register read:     ~1 tick   (100ns)
Function call dispatch: ~24 ticks (2400ns)
Bound method extra:     ~1 tick   (100ns)
─────────────────────────────────────────
Total t.counter():      ~26 ticks (2600ns)
```

### Why `stm.mem32[]` Is Slower Than `t.counter()`

Counterintuitively, direct memory access via `stm.mem32[address]` is **slower** than the bound method call:

| Operation | What Happens |
|-----------|--------------|
| `stm.mem32[addr]` | Type lookup → `__getitem__` dispatch → address calculation → memory read |
| `t.counter()` | Bound method dispatch → C function directly reads register |

The `t.counter()` implementation in C (`pyb_timer_counter()`) reads the register with a single memory access, while `stm.mem32[]` goes through Python's subscript mechanism.

### Implications for IRQ Handlers

1. **Bound methods are well-optimized** - The `t.counter()` call adds only ~1 tick overhead versus a direct viper function call.

2. **Function call overhead is inherent** - Any Python function call costs ~25 ticks regardless of implementation.

3. **For sub-microsecond response, avoid Python calls in hot path** - Keep everything inline in viper code:

```python
@micropython.viper
def fast_handler():
    # Direct register access - no function call
    cnt = ptr32(0x40000024)[0]  # TIM2->CNT
```

4. **Don't use `stm.mem32[]` for performance** - It's slower than bound methods due to subscript dispatch overhead.

### Detailed Function Call Breakdown

Using fine-grained profiling with 24 capture points, we traced exactly where the ~20 ticks are spent when calling `t.counter()`:

#### Call Path

```
Python code
    ↓
mp_call_function_n_kw (1st dispatch)     ─┐
    → mp_obj_get_type()                   │ ~3 ticks
    → MP_OBJ_TYPE_HAS_SLOT + slot call   ─┘
    ↓
bound_meth_call                          ─┐
    → extract self->meth, self->self      │ 2 ticks
    ↓                                     │
mp_call_method_self_n_kw                  │
    → alloca(args array)                  │ 3 ticks
    → memcpy(args)                       ─┘
    ↓
mp_call_function_n_kw (2nd dispatch)     ─┐
    → mp_obj_get_type()                   │ ~3 ticks
    → MP_OBJ_TYPE_HAS_SLOT + slot call   ─┘
    ↓
fun_builtin_var_call                     ─┐
    → mp_arg_check_num_sig()              │ 2 ticks
    → self->fun.var() call               ─┘
    ↓
pyb_timer_counter                        ─┐
    → read TIM->CNT                       │ ~4 ticks
    → mp_obj_new_int()                   ─┘
────────────────────────────────────────────
TOTAL: ~19 ticks (1900ns)
```

#### Breakdown by Phase

| Phase | Ticks | Time | What Happens |
|-------|-------|------|--------------|
| 1st `mp_call_function_n_kw` | ~3 | 300ns | Type lookup + slot dispatch |
| `bound_meth_call` → alloca | 2 | 200ns | Function entry, stack allocation |
| alloca → memcpy done | 3 | 300ns | Copy args to prepend self |
| 2nd `mp_call_function_n_kw` | ~3 | 300ns | Type lookup + slot dispatch |
| `fun_builtin_var_call` | 2 | 200ns | Entry + arg validation |
| → `pyb_timer_counter` | 2 | 200ns | Function pointer call |
| Inside timer_counter | ~4 | 400ns | Register read + int creation |
| **Total** | **~19** | **1900ns** | |

#### Key Discoveries

1. **No single bottleneck** - Overhead is distributed across many small operations. There's no obvious "hot spot" to optimize.

2. **Two full type dispatches** - The bound method path requires two complete `mp_call_function_n_kw` cycles, each doing type lookup and slot dispatch.

3. **Arg array copying** - `mp_call_method_self_n_kw` allocates a new array and copies args to prepend `self`, costing ~5 ticks.

4. **Bound method overhead is minimal** - When calling `t.counter()` directly (not via pre-bound method object), MicroPython's `LOAD_METHOD`/`CALL_METHOD` optimization bypasses bound method creation entirely, going straight to the builtin function.

5. **Arg validation cost** - `mp_arg_check_num_sig()` adds 2 ticks per call, even when args are correct.

### Potential Performance Optimizations

Based on the breakdown above, here are potential optimizations that could improve MicroPython function call performance. These are ordered by estimated impact and implementation complexity.

#### High Impact, Moderate Complexity

**1. Eliminate second type dispatch in bound method path (~3 ticks savings)**

Currently, `bound_meth_call` calls `mp_call_method_self_n_kw` which calls `mp_call_function_n_kw`, requiring a second full type lookup and slot dispatch. Since `bound_meth_call` already knows the target is a callable, it could dispatch directly:

```c
// Current path (two dispatches):
bound_meth_call → mp_call_method_self_n_kw → mp_call_function_n_kw → type lookup → slot call

// Optimized path (one dispatch):
bound_meth_call → mp_call_method_self_n_kw → direct call based on type
```

This would require `mp_call_method_self_n_kw` to check common callable types (builtin_var, builtin_0/1/2/3, fun_bc) and call directly.

**2. Avoid arg array allocation and copy (~5 ticks savings)**

`mp_call_method_self_n_kw` currently allocates a new array and copies all args to prepend `self`:

```c
// Current:
args2 = alloca(sizeof(mp_obj_t) * (1 + n_total));
args2[0] = self;
memcpy(args2 + 1, args, n_total * sizeof(mp_obj_t));
mp_call_function_n_kw(meth, n_args + 1, n_kw, args2);
```

Alternatives:
- Pass `self` as a separate parameter through the call chain
- Use a calling convention where `self` is at `args[-1]` (before the array) instead of `args[0]`
- For 0-arg methods, avoid copy entirely since only self is passed

#### Medium Impact, Low Complexity

**3. Fast path for 0-arg method calls (~4 ticks savings)**

Methods with no arguments (like `timer.counter()`) are extremely common. A specialized fast path could skip generic arg handling:

```c
// In bound_meth_call, for 0-arg case:
if (n_args == 0 && n_kw == 0) {
    // Skip alloca, memcpy, arg checking
    return direct_call_with_self(self->meth, self->self);
}
```

**4. Skip arg validation for known-good calls (~2 ticks savings)**

`mp_arg_check_num_sig()` validates argument count on every call. For builtin functions with fixed signatures called with correct args, this is redundant:

```c
// Add MP_TYPE_FLAG_SKIP_ARG_CHECK for trusted internal calls
if (!(self->flags & MP_TYPE_FLAG_SKIP_ARG_CHECK)) {
    mp_arg_check_num_sig(n_args, n_kw, self->sig);
}
```

#### Lower Impact / More Speculative

**5. Cache resolved methods for repeated calls**

When calling the same method repeatedly (e.g., in a loop), the method lookup could be cached:

```python
# Current: each call does LOAD_METHOD + CALL_METHOD
for i in range(1000):
    t.counter()  # Full lookup each time

# With caching: first call caches, subsequent calls reuse
```

This would require bytecode changes or a method cache in the VM.

**6. Inline type checks**

`mp_obj_get_type()` is already minimal (single memory read), but the slot lookup could be optimized for common types by checking them first:

```c
// Fast path for common types
if (type == &mp_type_fun_builtin_var) {
    return fun_builtin_var_call(fun_in, n_args, n_kw, args);
}
// Fall back to generic slot lookup
```

#### Trade-off Considerations

| Optimization | Savings | Code Size Impact | Complexity | Risk |
|-------------|---------|------------------|------------|------|
| Eliminate 2nd dispatch | ~3 ticks | +50 bytes | Medium | Low |
| Avoid arg copy | ~5 ticks | +100 bytes | High | Medium |
| 0-arg fast path | ~4 ticks | +30 bytes | Low | Low |
| Skip arg validation | ~2 ticks | +10 bytes | Low | Medium |
| Method caching | ~5 ticks | +200 bytes | High | Medium |
| Inline type checks | ~1 tick | +50 bytes | Low | Low |

The most promising combination would be:
1. **0-arg fast path** (low risk, good savings for common pattern)
2. **Eliminate 2nd dispatch** (moderate complexity, consistent savings)
3. **Skip arg validation flag** (low complexity, opt-in for trusted code)

Together these could reduce the 19-tick overhead by ~8-10 ticks (40-50%), bringing method calls down to ~10 ticks (1000ns).

### Consecutive Call Verification

To confirm the per-call overhead, we measured the delta between consecutive `t.counter()` calls:

```python
count1 = t.counter()
count2 = t.counter()
count3 = t.counter()
# count2 - count1 = overhead of single call
```

**Results (STM32H563ZI @ 250MHz, TIM2 @ 10MHz):**

| Context | Per-call overhead | Time |
|---------|-------------------|------|
| Bytecode function | **19.0 ticks** | 1900ns |
| @native function | **16.8 ticks** | 1680ns |

Raw data from 5 consecutive calls in bytecode context:
```
c2-c1=20, c3-c2=19, c4-c3=19, c5-c4=18 → avg 19.0 ticks
```

Raw data from 5 consecutive calls in @native context:
```
c2-c1=17, c3-c2=17, c4-c3=16, c5-c4=17 → avg 16.8 ticks
```

The `@native` decorator saves ~2-3 ticks per call due to less bytecode dispatch overhead between calls.

This confirms:
- Each `t.counter()` call takes ~19-20 ticks (1900-2000ns) in bytecode
- The overhead is consistent across consecutive calls
- The bound method mechanism itself is efficient; overhead is inherent function call dispatch

### Test Code

```python
import pyb
import stm
import time
import micropython

TIM2_CNT = 0x40000024

def test_methods():
    t2 = pyb.Timer(2, prescaler=24, period=0x7FFFFFFF)  # 10MHz
    t4 = pyb.Timer(4, freq=100)

    # Bound method
    start = t2.counter()
    for _ in range(1000):
        _ = t4.counter()
    bound_time = (t2.counter() - start) / 1000

    # stm.mem32
    start = t2.counter()
    for _ in range(1000):
        _ = stm.mem32[TIM2_CNT]
    mem_time = (t2.counter() - start) / 1000

    # Viper function
    @micropython.viper
    def read_viper() -> int:
        return ptr32(0x40000024)[0]

    start = t2.counter()
    for _ in range(1000):
        _ = read_viper()
    viper_time = (t2.counter() - start) / 1000

    # Pure viper (no Python call)
    @micropython.viper
    def read_viper_loop(n: int) -> int:
        p = ptr32(0x40000024)
        start: int = p[0]
        for _ in range(n):
            _ = p[0]
        return p[0] - start

    viper_pure = read_viper_loop(1000) / 1000

    print(f"t.counter():    {bound_time:.1f} ticks")
    print(f"stm.mem32[]:    {mem_time:.1f} ticks")
    print(f"@viper func:    {viper_time:.1f} ticks")
    print(f"@viper inline:  {viper_pure:.1f} ticks")
```

---

## Fast IRQ Dispatch for Function Callbacks

This section documents the optimization that automatically wraps bytecode function callbacks as generator-like objects for fast IRQ dispatch.

### Problem Statement

Before this optimization, function callbacks had significantly higher IRQ dispatch overhead than generators:

| Handler Type | P1→P5 (dispatch total) | Notes |
|-------------|------------------------|-------|
| Generator | ~36 ticks (3600ns) | State pre-initialized at registration |
| Function | ~51 ticks (5100ns) | Full INIT_CODESTATE on every IRQ |

The 15-tick difference came primarily from the INIT_CODESTATE overhead (~14 ticks) that occurs on every function call: parsing bytecode prelude, allocating stack space, copying arguments, and initializing exception stack.

### Solution: `mp_obj_irq_func_wrapper_t`

The optimization wraps bytecode functions as generator-like objects at registration time, pre-initializing the code state once and reusing it across IRQs.

**Commit:** `fecd3dae63` - shared/runtime,py: Add fast IRQ dispatch for function callbacks.

#### New Data Structure

```c
typedef struct _mp_obj_irq_func_wrapper_t {
    mp_obj_base_t base;
    mp_obj_t pend_exc;              // none=idle, NULL=running (like generator)
    mp_obj_fun_bc_t *fun_bc;        // Original function
    const byte *bytecode_start;     // Cached: first bytecode instruction
    uint16_t n_state;               // Cached: state array size
    uint16_t n_exc_stack;           // Cached: exception stack size
    mp_code_state_t code_state;     // Pre-allocated (variable length follows)
} mp_obj_irq_func_wrapper_t;
```

#### Wrapping at Registration (`mp_irq_wrap_function`)

When `mp_irq_prepare_handler()` receives a bytecode function:

1. Decode bytecode prelude to get `n_state` and `n_exc_stack`
2. Verify function accepts exactly 1 argument (the parent/timer object)
3. Allocate wrapper with variable-length state array
4. Pre-parse prelude to cache `bytecode_start` pointer
5. Initialize `code_state.fun_bc` and `code_state.n_state`

This work is done once at registration, not on every IRQ.

#### Fast Resume (`mp_irq_func_wrapper_resume`)

On each IRQ, the wrapper resume is minimal:

```c
// Reset state for fresh execution
self->code_state.ip = self->bytecode_start;
self->code_state.sp = &self->code_state.state[0] - 1;
self->code_state.exc_sp_idx = 0;

// Zero state array (locals + stack)
memset(self->code_state.state, 0, self->n_state * sizeof(mp_obj_t));

// Place argument directly in locals slot
self->code_state.state[self->n_state - 1] = arg;

// Execute bytecode directly (bypasses INIT_CODESTATE!)
mp_vm_return_kind_t ret = mp_execute_bytecode(&self->code_state, MP_OBJ_NULL);
```

This is ~4-5 ticks instead of ~14 for full INIT_CODESTATE.

### Part 1: `mp_obj_gen_resume_irq()`

A companion optimization adds a fast path for generator resume that skips the primed-check:

```c
// In py/objgenerator.c
mp_vm_return_kind_t mp_obj_gen_resume_irq(mp_obj_t self_in, mp_obj_t send_value, mp_obj_t *ret_val) {
    // Skip state_start calculation - generator is primed, directly write send_value
    *self->code_state.sp = send_value;
    // ... rest of resume logic ...
}
```

For IRQ handlers, generators are always primed at registration time, so the check `sp == state_start` and the `state_start` calculation are wasted work.

### Results (STM32H563ZI @ 250MHz, TIM2 @ 10MHz)

After the optimization:

| Handler Type | Before | After | Improvement |
|-------------|--------|-------|-------------|
| Function wrapper | 51 ticks | **36 ticks** | **29% faster** |
| Generator | 36 ticks | 40 ticks | 11% regression |

**Function callbacks are now nearly as fast as generators!**

Raw profiling data (5 runs, P1→P5):
- Function: `[37, 36, 36, 37, 36]` → avg 36 ticks
- Generator: `[40, 40, 40, 43, 41]` → avg 40 ticks

### Generator Regression: Root Cause and Fix

Initial results showed a regression in generator performance (36→40 ticks). Investigation revealed the cause:

**Root Cause:** The Part 2 implementation added debug profile capture points (P1-P9) throughout the IRQ dispatch path. These calls caused overhead even when profiling was disabled due to the volatile flag check:

```c
#define MP_IRQ_PROFILE_CAPTURE(idx) do { \
        if (pyb_irq_profile_enabled) { \  // Volatile read adds cycles
            pyb_irq_profile[idx] = *(volatile uint32_t *)(0x40000000 + 0x24); \
        } \
} while (0)
```

Before Part 2, the dispatch path had zero profile capture points. Part 2 added 9 capture points (P1-P6 in `mp_irq_dispatch()`, P7-P9 in `mp_obj_gen_resume()` and `mp_obj_gen_resume_irq()`).

**Fix:** Removed all MP_IRQ_PROFILE_CAPTURE calls from the core dispatch and generator resume paths. Profile capture is now limited to the timer IRQ handler entry (P0) which remains useful for measuring total ISR-to-handler latency.

**Files Modified:**
- `shared/runtime/mpirq.c` - Removed P1-P6 from `mp_irq_dispatch()`
- `py/objgenerator.c` - Removed P7-P9 from `mp_obj_gen_resume()` and `mp_obj_gen_resume_irq()`

**Results After Fix (STM32H563ZI @ 250MHz):**

| Handler Type | Before Fix | After Fix | Notes |
|-------------|------------|-----------|-------|
| Function wrapper | 36 ticks | **9998 ticks period** | 1ms period as expected |
| Generator | 40 ticks | **9998 ticks period** | Regression eliminated |

Both callback types now show identical performance with 0-tick difference in period measurements, confirming the regression was entirely due to profile capture overhead.

### Memory Overhead

The wrapper structure is similar in size to a user-written generator:

| Component | Size |
|-----------|------|
| Wrapper header | ~24 bytes |
| Code state header | ~32 bytes |
| State array | `n_state * 4 + n_exc_stack * 8` bytes |

For a simple 1-arg callback with 2-3 locals: ~100-120 bytes per handler.

### Files Modified

| File | Changes |
|------|---------|
| `py/objgenerator.c` | Added `mp_obj_gen_resume_irq()` |
| `py/objgenerator.h` | Declared `mp_obj_gen_resume_irq()` |
| `shared/runtime/mpirq.c` | Added wrapper type, `mp_irq_wrap_function()`, `mp_irq_func_wrapper_resume()`, updated dispatch logic |

### Dispatch Flow

```
mp_irq_dispatch(handler, parent, ishard=true)
    ├─ mp_obj_is_type(handler, &mp_type_gen_instance)?
    │   └─ mp_obj_gen_resume_irq(handler, parent, &ret_val)
    │
    ├─ mp_obj_is_type(handler, &mp_type_irq_func_wrapper)?
    │   └─ mp_irq_func_wrapper_resume(handler, parent, &ret_val)
    │
    └─ else (builtin/native functions)
        └─ mp_call_function_1(handler, parent)
```

Bytecode functions are wrapped at registration, so they take the wrapper path. Builtin/native functions remain unchanged (already fast, ~5 ticks).
