# Zephyr BLE Host Integration Plan

## Current Status

**OS Adapter Layer: ✅ COMPLETE**

All kernel abstractions are implemented and tested:
- `k_timer` - Polling-based timers
- `k_work` / `k_work_q` - Event queues with global linked list
- `k_sem` - Busy-wait semaphores
- `k_mutex` - No-op mutexes
- `k_spinlock` - Critical section macros
- Atomic operations - Header-only inline functions
- Kernel misc - sleep, yield, timing functions
- Main polling function - `mp_bluetooth_zephyr_poll()`

**Git Commits:**
1. `6dec1b7a79` - Directory structure, k_timer, k_work, docs
2. `e2d6a9eeda` - k_sem
3. `ef6a41a066` - k_mutex
4. `e8e864e892` - Atomic ops
5. `fe1b0a51a9` - Kernel misc
6. `0aa74af352` - Polling function
7. `fc8e21b5a2` - Master HAL header

## Integration Challenges

### 1. Zephyr Include System

Zephyr BLE host sources expect Zephyr's full include tree:
```c
#include <zephyr/kernel.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
// ... many more
```

**Options:**
- **A. Wrapper Headers**: Create `zephyr/kernel.h` that includes our HAL
- **B. Source Patching**: Modify includes (violates "no patching" constraint)
- **C. Compiler Flags**: Use `-I` to redirect includes to our HAL

**Recommendation**: Option A (wrapper headers)

### 2. Kconfig Configuration

Zephyr uses Kconfig for feature selection (see `lib/zephyr/subsys/bluetooth/host/CMakeLists.txt`):
- `CONFIG_BT_HCI_HOST` - Enable host stack
- `CONFIG_BT_BROADCASTER` - Advertising support
- `CONFIG_BT_OBSERVER` - Scanning support
- `CONFIG_BT_CONN` - Connection support
- `CONFIG_BT_SMP` - Security Manager Protocol
- `CONFIG_BT_GATT_CLIENT` / `CONFIG_BT_GATT_DYNAMIC_DB` - GATT features
- ... 50+ configuration options

**Options:**
- **A. Static Config**: Create `zephyr_ble_config.h` with hardcoded `#define` values
- **B. MicroPython Config**: Map to `MICROPY_PY_BLUETOOTH_*` defines
- **C. Per-Port Config**: Each port defines config in `mpconfigport.h`

**Recommendation**: Mix of A and B - base config in extmod, port-specific overrides

### 3. HCI Driver Integration

Zephyr BLE expects HCI driver API:
- `bt_hci_driver_register()` - Register HCI transport
- `bt_send()` - Send HCI packet to controller
- HCI driver callbacks for RX data

MicroPython ports have diverse HCI implementations:
- **STM32/RP2**: CYW43 SDIO driver
- **STM32**: STM32WB co-processor
- **ESP32**: Built-in controller
- **nRF**: SoftDevice or Zephyr controller

**Approach:**
- Create HCI abstraction layer in `extmod/zephyr_ble/hci/`
- Port-specific HCI drivers in `ports/*/mpzephyrbleport.c`
- Follow NimBLE/BTstack pattern

### 4. Logging System

Zephyr uses structured logging:
```c
#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(bt_hci_core);
LOG_DBG("...");
```

**Options:**
- **A. Map to printf**: `#define LOG_DBG printf`
- **B. Map to mp_printf**: Use MicroPython's printf
- **C. Disable**: `#define LOG_DBG(...)` (no-op)

**Recommendation**: Option B for debug, C for release

### 5. Settings/Storage System

Zephyr BLE uses settings API for persistent storage:
- Bonding keys
- Identity Resolving Keys (IRK)
- Device name
- GATT database

**Options:**
- **A. RAM-only**: Store in globals, lose on reset
- **B. MicroPython VFS**: Use filesystem for persistence
- **C. Port-specific**: Flash storage per port

**Recommendation**: Start with A, add B later

### 6. Memory Management

Zephyr uses:
- `k_malloc()` / `k_free()` - Heap allocation
- `k_heap` - Kernel heap
- Memory pools

MicroPython uses:
- `m_new()` / `m_del()` - GC-tracked allocation

**Approach:**
- Map `k_malloc` → `m_malloc` (non-GC heap)
- Buffer pools use static allocation

## Integration Phases

### Phase 1: Minimal Core (CHECKPOINT)
**Goal**: Get basic BLE init working with minimal features

**Tasks:**
1. Create Kconfig wrapper with minimal config:
   - `CONFIG_BT_HCI_HOST=1`
   - `CONFIG_BT_BROADCASTER=1`
   - `CONFIG_BT_OBSERVER=1`
   - `CONFIG_BT_PERIPHERAL=1`
   - Disable: SMP, ISO, Direction Finding, etc.

2. Create wrapper headers:
   - `zephyr/kernel.h` → `hal/zephyr_ble_hal.h`
   - `zephyr/sys/*.h` → Compatibility shims
   - `zephyr/logging/log.h` → printf or no-op

3. Add core source files to build:
   - `uuid.c`, `addr.c`, `buf.c`, `data.c`
   - `hci_core.c`, `hci_common.c`, `id.c`
   - `adv.c`, `scan.c`

4. Create minimal HCI stub driver

5. Attempt first build, fix compilation errors

**Success Criteria**: Code compiles, links, basic init doesn't crash

### Phase 2: Connection Support (CHECKPOINT)
**Goal**: Add GAP connection support

**Tasks:**
1. Enable in config:
   - `CONFIG_BT_CONN=1`
   - `CONFIG_BT_SMP=0` (use `smp_null.c`)

2. Add source files:
   - `conn.c`, `l2cap.c`

3. Implement connection state handling

**Success Criteria**: Can establish BLE connections (no security)

### Phase 3: GATT Support (CHECKPOINT)
**Goal**: Add GATT server/client

**Tasks:**
1. Enable in config:
   - `CONFIG_BT_GATT=1`
   - `CONFIG_BT_GATT_DYNAMIC_DB=1`

2. Add source files:
   - `att.c`, `gatt.c`

3. Implement GATT database registration

**Success Criteria**: Can register GATT services, handle read/write

### Phase 4: Security (CHECKPOINT)
**Goal**: Add SMP pairing/bonding

**Tasks:**
1. Enable in config:
   - `CONFIG_BT_SMP=1`
   - Replace `smp_null.c` with `smp.c` + `keys.c`

2. Add crypto support:
   - `crypto_psa.c` or `ecc.c`

3. Implement key storage (RAM-only initially)

**Success Criteria**: Can pair and bond with devices

### Phase 5: Port Integration (CHECKPOINT)
**Goal**: Integrate with real HCI driver on target port

**Tasks:**
1. Choose initial port (recommend STM32 with CYW43)
2. Implement `ports/stm32/mpzephyrbleport.c`:
   - `mp_bluetooth_hci_poll()` → `mp_bluetooth_zephyr_poll()`
   - HCI driver registration
   - Soft timer integration
3. Build and test on hardware

**Success Criteria**: Basic BLE operations work on real hardware

### Phase 6: modbluetooth Integration
**Goal**: Connect to MicroPython's `bluetooth` module

**Tasks:**
1. Update `extmod/zephyr_ble/modbluetooth_zephyr.c`
2. Implement all modbluetooth.h callbacks
3. Map Zephyr events to MicroPython IRQ events

**Success Criteria**: `import bluetooth` works, can run examples

### Phase 7: Testing & Refinement
**Goal**: Validate against multitest suite

**Tasks:**
1. Run `tests/multi_bluetooth/` test suite
2. Fix bugs, memory leaks, edge cases
3. Optimize code size and memory usage

**Success Criteria**: All BLE multitests pass

## File Structure (Post-Integration)

```
extmod/zephyr_ble/
├── modbluetooth_zephyr.c          # MicroPython bindings
├── zephyr_ble.mk                  # Makefile
├── zephyr_ble.cmake               # CMake
├── zephyr_ble_config.h            # Kconfig definitions (NEW)
├── hal/                           # OS abstraction layer
│   ├── zephyr_ble_hal.h           # Master HAL header
│   ├── zephyr_ble_timer.{h,c}    # k_timer
│   ├── zephyr_ble_work.{h,c}     # k_work
│   ├── zephyr_ble_sem.{h,c}      # k_sem
│   ├── zephyr_ble_mutex.{h,c}    # k_mutex
│   ├── zephyr_ble_atomic.h       # Atomic ops
│   ├── zephyr_ble_kernel.{h,c}   # Kernel misc
│   └── zephyr_ble_poll.{h,c}     # Main polling
├── hci/                           # HCI abstraction (NEW)
│   ├── zephyr_ble_hci.h
│   └── zephyr_ble_hci_uart.c     # UART HCI transport
├── zephyr/                        # Zephyr header wrappers (NEW)
│   ├── kernel.h → ../hal/zephyr_ble_hal.h
│   ├── sys/
│   │   ├── byteorder.h
│   │   ├── util.h
│   │   └── ...
│   └── logging/
│       └── log.h
└── host/                          # Zephyr BLE host sources (symlink or copy)
    ├── uuid.c
    ├── addr.c
    ├── hci_core.c
    └── ... (copied from lib/zephyr/subsys/bluetooth/host/)
```

## Next Steps

**CHECKPOINT - User Review Required**

Before proceeding with Phase 1, need user input on:

1. **Source Organization**: Should we:
   - Copy Zephyr host sources to `extmod/zephyr_ble/host/`?
   - Build from `lib/zephyr/subsys/bluetooth/host/` with include redirects?
   - Use git submodule sparse checkout?

2. **Kconfig Approach**: Confirm static config approach or explore alternatives

3. **Initial Target Port**: Which port for Phase 5 integration?
   - STM32 with CYW43 (PYBD_SF6)?
   - RP2 with CYW43 (RPI_PICO_W)?
   - ESP32 with built-in controller?

4. **Feature Scope**: Confirm we want full stack:
   - GAP (advertise, scan, connect)
   - GATT (server, client, dynamic DB)
   - SMP (pairing, bonding)
   - L2CAP (basic + enhanced)

5. **Logging Strategy**: Debug vs release logging approach

This is a substantial undertaking. Each phase represents multiple days of work.
