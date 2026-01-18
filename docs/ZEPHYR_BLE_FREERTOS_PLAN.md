# Zephyr BLE FreeRTOS Integration Plan

## Overview

Zephyr BLE should standardize on FreeRTOS for task-based integration across all MicroPython ports. This eliminates polling-based workarounds and provides proper threading separation between Python execution and BLE stack processing.

## Current State

### RP2 Pico W (RP2040) - FreeRTOS Implementation
**Status**: Working implementation using FreeRTOS tasks

**Architecture**:
```
FreeRTOS Scheduler
├── Priority MAX-1: HCI RX Task (CYW43 polling)
├── Priority MAX-2: BLE Work Queue Thread
└── Priority 1: Python Main Thread
```

**Benefits**:
- BLE stack processing independent of Python execution
- No polling required from machine.idle()
- Work queue processes automatically via dedicated task
- Clean separation of concerns

### STM32WB55 - Current Implementation
**Status**: Scheduler-based workaround (no FreeRTOS)

**Current Approach**:
- `machine.idle()` calls `mp_handle_pending()` to process scheduler
- BLE polling scheduled via `mp_sched_schedule_node()`
- Python execution must yield control for BLE events to process

**Limitations**:
- BLE processing blocked when Python code doesn't call idle/sleep
- Couples BLE functionality to scheduler implementation
- Not truly asynchronous - Python must cooperate

## Migration Path

### Phase 1: Enable FreeRTOS on STM32 Ports
**Target**: STM32WB55, STM32H5, other STM32 variants

**Changes Required**:
1. Enable `MICROPY_PY_THREAD` in board config
2. Configure FreeRTOS integration (already supported in STM32 port)
3. Allocate FreeRTOS heap and stack space
4. Test threading stability with existing code

**Files**:
- `ports/stm32/boards/NUCLEO_WB55/mpconfigboard.h`
- `ports/stm32/boards/NUCLEO_WB55/mpconfigboard.mk`

### Phase 2: Implement Task-Based Zephyr BLE HAL
**Target**: All ports with FreeRTOS support

**Architecture Pattern** (based on RP2 implementation):
```c
// HCI RX Task (highest priority)
// - Polls HCI transport (UART/IPCC/SPI)
// - Queues received packets for processing
// - Runs continuously, yields on no data

// BLE Work Queue Task (high priority)
// - Processes Zephyr work queue items
// - Handles HCI packet processing callbacks
// - Runs continuously, blocks on work queue

// Python Main Task (normal priority)
// - Executes Python bytecode
// - Handles user script execution
// - BLE events delivered via mp_sched when safe
```

**Implementation Steps**:
1. Create `extmod/zephyr_ble/hal/zephyr_ble_freertos.c`
2. Implement task creation/management functions
3. Port HCI polling to dedicated task (from current polling implementation)
4. Port work queue processing to dedicated task
5. Add inter-task communication (queues, semaphores)

**Key Functions**:
```c
// Initialize FreeRTOS tasks for Zephyr BLE
void mp_bluetooth_zephyr_freertos_init(void);

// Shutdown FreeRTOS tasks (called from deinit)
void mp_bluetooth_zephyr_freertos_deinit(void);

// HCI RX task entry point
void mp_bluetooth_zephyr_hci_task(void *arg);

// Work queue task entry point
void mp_bluetooth_zephyr_work_task(void *arg);
```

### Phase 3: Remove Polling-Based HAL
**Target**: Deprecate `zephyr_ble_poll.c` and scheduler-based integration

**Changes**:
1. Make FreeRTOS mandatory for Zephyr BLE (`#error` if not enabled)
2. Remove `mp_bluetooth_zephyr_poll()` and related polling infrastructure
3. Remove scheduler-based workaround from `machine.idle()`
4. Update documentation to reflect FreeRTOS requirement

### Phase 4: Port-Specific Adaptations
**Target**: Each port using Zephyr BLE

**Per-Port Tasks**:
1. **STM32**: Adapt IPCC HCI transport for task-based polling
2. **ESP32**: Integrate with ESP-IDF FreeRTOS (different API)
3. **NRF**: Adapt UART HCI transport for task-based polling
4. Adjust task priorities for each port's constraints

## Benefits of FreeRTOS Integration

1. **Architectural Correctness**: BLE stack processing independent of Python execution
2. **Responsiveness**: BLE events processed immediately without Python cooperation
3. **Consistency**: Same architecture across all Zephyr BLE ports
4. **Simplicity**: Removes scheduler workarounds and polling complexity
5. **Performance**: Dedicated tasks with appropriate priorities

## Compatibility Considerations

### Breaking Changes
- FreeRTOS becomes mandatory dependency for Zephyr BLE
- Increased memory footprint (FreeRTOS overhead + task stacks)
- May affect boards with limited RAM

### Migration Strategy
- Keep NimBLE and BTstack as alternatives for resource-constrained boards
- Document FreeRTOS requirement clearly in Zephyr BLE documentation
- Provide memory usage estimates for each port

## Timeline

**Short-term** (Current):
- ✓ Scheduler-based workaround functional on STM32WB55
- ✓ FreeRTOS implementation working on RP2

**Medium-term** (Next release cycle):
- Enable FreeRTOS on STM32 Zephyr BLE variant
- Implement task-based HAL for STM32
- Test and verify stability

**Long-term** (Future releases):
- Make FreeRTOS mandatory for Zephyr BLE
- Remove polling-based fallback
- Extend to other ports (ESP32, NRF)

## Testing Requirements

1. **Functional Tests**: All BLE multitests must pass
2. **Stress Tests**: Long-running BLE operations (hours)
3. **Concurrency Tests**: Python threads + BLE tasks
4. **Memory Tests**: Monitor heap/stack usage under load
5. **Priority Tests**: Verify BLE responsiveness under Python load

## References

- RP2 FreeRTOS implementation: `ports/rp2/mpzephyrport_freertos.c`
- STM32 FreeRTOS integration: `ports/stm32/mpthreadport.c`
- Zephyr work queue: `extmod/zephyr_ble/hal/zephyr_ble_work.c`
