# RP2 FreeRTOS Integration Status

## Build Status: 100% COMPLETE ✓

The RP2040 (Raspberry Pi Pico) port has been successfully integrated with the universal FreeRTOS threading backend. Firmware builds without errors.

### Build Output
```
text      data    bss     dec     hex   filename
345780    0       24876   370656  5a7e0 firmware.elf
```

## Issues Resolved

### 1. PendSV Handler Conflict (RESOLVED)
**Problem**: Both MicroPython's `pendsv.c` and FreeRTOS's `port.c` defined `PendSV_Handler`, causing multiple definition error.

**Solution**:
- Enabled `configUSE_DYNAMIC_EXCEPTION_HANDLERS (1)` in `FreeRTOSConfig.h`
- Implemented naked assembly wrapper in `pendsv.c:204` that:
  - Checks `pendsv_dispatch_active` flag
  - Calls `pendsv_dispatch_handler()` if dispatch pending
  - Tail-calls `xPortPendSVHandler` for FreeRTOS context switching
- Used Cortex-M0+ compatible assembly (`.syntax unified`, `push {r4, lr}`, `pop {r4, r3}`, `mov lr, r3`)

### 2. Recursive Mutex API (RESOLVED)
**Problem**: RP2's `pendsv.c` required `mp_thread_recursive_mutex_*` functions which weren't enabled in universal backend.

**Solution**: Enabled `MICROPY_PY_THREAD_RECURSIVE_MUTEX (1)` in `mpconfigport.h:151-153`.

### 3. mp_hal_delay_ms Type Mismatch (RESOLVED)
**Problem**: Declaration used `mp_uint_t` which isn't defined early enough in include chain, causing compilation error.

**Solution**: Changed declaration in `mpconfigport.h:146` to use `unsigned int` (matching STM32 approach).

## Implementation Details

### Files Modified
- `mpconfigport.h` - Threading backend configuration, delay redirection, recursive mutex enable
- `FreeRTOSConfig.h` - FreeRTOS SMP configuration, dynamic exception handlers
- `freertos_hooks.c` - FreeRTOS hook implementations (vApplicationStackOverflowHook, etc.)
- `mpthreadport_rp2.c` - RP2-specific atomic sections (FreeRTOS critical + IRQ disable)
- `CMakeLists.txt` - FreeRTOS kernel integration, moved `pendsv.c` to always compile
- `main.c` - FreeRTOS task creation and scheduler startup
- `mphalport.c` - Removed core1_entry references
- `rp2_flash.c` - Simplified multicore lockout for FreeRTOS SMP
- `pendsv.c` - Refactored for FreeRTOS coexistence with naked wrapper

### Key Design Decisions

1. **PendSV Wrapper Pattern**: Follows STM32 approach but adapted for Cortex-M0+ instruction set limitations.

2. **Dual-Core SMP**: Uses FreeRTOS SMP port with hardware spinlocks (0 and 1) for inter-core synchronization.

3. **Atomic Sections**: Combine FreeRTOS critical sections (`taskENTER_CRITICAL`) with hardware IRQ disable for robust synchronization.

4. **Recursive Mutexes**: Required for PendSV on dual-core RP2040 where either core may call `pendsv_suspend()` expecting both mutual exclusion and recursive locking.

## Architecture Notes

- RP2040 is Cortex-M0+ dual-core (different from STM32 F4 single Cortex-M4)
- FreeRTOS SMP uses SysTick for tick generation (clocked from `clk_sys`)
- Hardware spinlocks 0 and 1 reserved for FreeRTOS (`configSMP_SPINLOCK_0/1`)
- PendSV priority set to lowest (`PICO_LOWEST_IRQ_PRIORITY`)
- Main task stack: 8192 bytes
- FreeRTOS heap: 8KB (most memory comes from MicroPython GC heap)

## Testing Status

### Not Yet Tested
- Hardware validation on Pico W
- Thread creation and scheduling
- Dual-core operation
- GC interaction with FreeRTOS tasks
- Soft timer dispatch via PendSV
- WiFi driver (CYW43) with threading

### Next Steps
1. Flash firmware to Pico W hardware
2. Run basic REPL test
3. Run threading tests from `tests/thread/`
4. Test dual-core workload distribution
5. Verify soft timer operation
6. Test WiFi functionality with threading

## Comparison to STM32 Port

| Aspect | STM32 F4 | RP2040 |
|--------|----------|--------|
| Cores | Single | Dual (SMP) |
| Cortex Core | M4 | M0+ |
| PendSV Assembly | Standard ARM | Unified syntax, limited instruction set |
| Threading Init | No args | Requires stack/size for GC scanning |
| Atomic Sections | `disable_irq()` directly | FreeRTOS critical + IRQ disable combo |
| Spinlocks | N/A | Hardware spinlocks 0 & 1 |
| FreeRTOS Config | Standard | SMP-specific (`configNUMBER_OF_CORES`, etc.) |

## References

- FreeRTOS SMP Documentation: https://www.freertos.org/symmetric-multiprocessing-introduction.html
- RP2040 Datasheet: Section 2.3.1 (Hardware Spinlocks)
- Cortex-M0+ Instruction Set: ARMv6-M Architecture Reference Manual
- MicroPython FreeRTOS Threading Spec: `FREERTOS_THREADING_REQUIREMENTS.md`
