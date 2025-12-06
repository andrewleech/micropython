# FreeRTOS Threading Backend Development

## Active Work

Implementing universal FreeRTOS threading backend for MicroPython in `extmod/freertos/`.

**Specification Documents:**
- `FREERTOS_THREADING_REQUIREMENTS.md` - Technical requirements (v1.5)
- `FREERTOS_IMPLEMENTATION_PLAN.md` - Phased implementation plan

## Target Ports (Development Order)

1. **QEMU ARM (Cortex-M3)** - Primary development/testing target (no hardware needed)
2. **STM32 (NUCLEO-F429ZI)** - Primary hardware validation using `flash-nucleo-f429` skill
3. Secondary ports (mimxrt, SAMD51, nRF52840, RP2) follow after core is stable

## Development Workflow

### Step Implementation

1. Pick next task from implementation plan (check git log for last completed step)
2. Implement the step - use `[HAIKU]` agent for simple tasks, regular agent for `[REGULAR]` tasks
3. Run `principal-code-reviewer` agent on significant changes
4. Address review feedback
5. Test where possible:
   - Build: `make -C ports/stm32 BOARD=NUCLEO_F429ZI MICROPY_PY_THREAD=1`
   - Flash: Use `flash-nucleo-f429` skill
   - Run tests: `mpremote run tests/thread/<test>.py`
6. Commit with plan reference

### Commit Message Format

```
extmod/freertos: <brief description>.

Implements Phase X.Y: <task name from plan>

Signed-off-by: ...
```

Example:
```
extmod/freertos: Add core thread data structures.

Implements Phase 1.3: Define Core Data Structures

Signed-off-by: ...
```

### Git Log as Progress Tracker

The git history serves as the running development log. To check progress:
```bash
git log --oneline --grep="Implements Phase"
```

## Build Commands

```bash
# QEMU build (for CI/development)
make -C ports/qemu-arm BOARD=mps2-an385 MICROPY_PY_THREAD=1

# STM32 build
make -C ports/stm32 BOARD=NUCLEO_F429ZI MICROPY_PY_THREAD=1

# Non-threaded build (must not break)
make -C ports/stm32 BOARD=NUCLEO_F429ZI
```

## Testing

```bash
# Run single thread test
mpremote run tests/thread/thread_start1.py

# Run thread test suite
cd tests && ./run-tests.py --target pyboard thread/

# Stress test (extended run)
mpremote run tests/thread/stress_freertos_gc.py
```

## Key Risk Areas (from plan)

- **Phase 6.4** (mp_thread_create) - Memory management, error handling
- **Phase 7.1** (mp_thread_gc_others) - GC stack scanning correctness
- **Phase 10.4** (STM32 main.c) - Must preserve non-threaded builds
