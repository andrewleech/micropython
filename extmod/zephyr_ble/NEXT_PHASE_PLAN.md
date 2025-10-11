# Next Phase Plan - BLE Source Compilation

## Current Status

**Phase 1 Complete**: All 16 wrapper headers created, reviewed, and fixed
- ✅ System headers (devicetree, toolchain, slist, util, etc.)
- ✅ Logging stubs
- ✅ Threading stubs
- ✅ All critical issues resolved (IS_ENABLED, __ASSERT_NO_MSG, etc.)

## Analysis: What's Needed for Full Compilation

### Headers Already Available in Zephyr

These headers exist in `lib/zephyr/include/zephyr/` and will be used directly:
- `bluetooth/*.h` - BLE API headers (hci.h, conn.h, gatt.h, etc.)
- `net_buf.h` - Network buffer API
- `sys/byteorder.h` - Byte order conversions
- `init.h` - System initialization

### Headers Status

#### 1. `zephyr/autoconf.h` ✅ EXISTS
**Status**: Already created and delegates to `zephyr_ble_config.h`

**Recent update**: Added 60+ missing CONFIG_* values that are actually used by BLE sources:
- Logging levels (CONFIG_BT_*_LOG_LEVEL)
- Connection parameters and timeouts
- Optional feature flags (all disabled initially)
- Array size definitions for buffers

**Approach**: Only define CONFIG values that are actually used, not all Zephyr configs
```c
#ifndef ZEPHYR_AUTOCONF_H_
#define ZEPHYR_AUTOCONF_H_

// BLE Core Configuration
#define CONFIG_BT 1
#define CONFIG_BT_HCI 1
#define CONFIG_BT_PERIPHERAL 1
#define CONFIG_BT_CENTRAL 1

// BLE Features (initially disabled, enable as needed)
#define CONFIG_BT_CONN 1
#define CONFIG_BT_GATT_CLIENT 1
#define CONFIG_BT_GATT_DYNAMIC_DB 1
#define CONFIG_BT_MAX_CONN 4
#define CONFIG_BT_MAX_PAIRED 4

// Features to disable initially
#define CONFIG_BT_SETTINGS 0
#define CONFIG_BT_PRIVACY 0
#define CONFIG_BT_SMP 0
#define CONFIG_BT_OBSERVER 0
#define CONFIG_BT_BROADCASTER 0

// Work queue configuration
#define CONFIG_BT_LONG_WQ_STACK_SIZE 1024
#define CONFIG_BT_LONG_WQ_PRIO 10
#define CONFIG_BT_LONG_WQ_INIT_PRIO 50

// Buffer sizes
#define CONFIG_BT_L2CAP_TX_BUF_COUNT 3
#define CONFIG_BT_L2CAP_TX_MTU 23
#define CONFIG_BT_BUF_ACL_RX_SIZE 251
#define CONFIG_BT_BUF_ACL_TX_SIZE 251
#define CONFIG_BT_RECV_BLOCKING 1

// Logging (all disabled)
#define CONFIG_LOG 0
#define CONFIG_PRINTK 0

// Testing support
#define CONFIG_ZTEST 1  // Bypass device tree

// ... (many more CONFIG_* values needed)
#endif
```

**How to determine values**:
1. Look at MicroPython's current Zephyr port BLE config
2. Check `lib/zephyr/subsys/bluetooth/Kconfig` for defaults
3. Start minimal, enable features incrementally as needed

#### 2. `zephyr/random/random.h` (MEDIUM)
**Purpose**: Random number generation for security

**Used by**: Address generation, security keys, etc.

**Implementation approach**:
```c
#include <stdint.h>
#include <stddef.h>

// Map to MicroPython's random source (STM32 RNG, software fallback, etc.)
void sys_rand_get(void *buf, size_t len);
uint32_t sys_rand32_get(void);
```

**Port integration**:
```c
// In hal/zephyr_ble_random.c
void sys_rand_get(void *buf, size_t len) {
    #ifdef STM32_HAS_RNG
    // Use hardware RNG
    HAL_RNG_GenerateRandomNumber(&RngHandle, (uint32_t*)buf, len/4);
    #else
    // Use software RNG
    for (size_t i = 0; i < len; i++) {
        ((uint8_t*)buf)[i] = (uint8_t)rand();
    }
    #endif
}
```

### Headers Provided by Zephyr (Use Directly)

These complex headers should be used from Zephyr as-is:
- `bluetooth/bluetooth.h` - Main BLE API
- `bluetooth/hci.h` - HCI definitions
- `bluetooth/conn.h` - Connection management
- `bluetooth/gatt.h` - GATT API
- `bluetooth/l2cap.h` - L2CAP API
- `net_buf.h` - Network buffer implementation
- `sys/byteorder.h` - Byte order macros

**Why**: These contain actual BLE protocol definitions and complex structures

## Compilation Strategy

### Phase 2A: Create Remaining Headers

1. ✅ `zephyr/autoconf.h` - Already exists, updated with 60+ additional CONFIG values
2. Create `zephyr/random/random.h` wrapper (only remaining missing header)
3. Implement `hal/zephyr_ble_random.c`

### Phase 2B: Identify BLE Source Dependencies

Compile order (simplest to most complex):
1. `addr.c` - Address utilities (DONE - already compiled)
2. `uuid.c` - UUID utilities (DONE - already compiled)
3. `hci_common.c` - HCI common code
4. `buf.c` - Buffer management
5. `keys.c` - Security keys
6. `hci_core.c` - HCI core (large, many dependencies)
7. `conn.c` - Connection management
8. `gatt.c` - GATT implementation
9. ... (other sources as needed)

### Phase 2C: Build System Integration

**Option 1: Unix Port (Recommended for testing)**
```bash
cd ports/unix
# Modify Makefile to include extmod/zephyr_ble
make CFLAGS_EXTRA="-I../../extmod/zephyr_ble -I../../extmod/zephyr_ble/zephyr"
```

**Option 2: STM32 Port (For actual BLE hardware)**
```bash
cd ports/stm32
# Modify mpconfigboard.mk for PYBD_SF6
make BOARD=PYBD_SF6
```

**Option 3: Standalone Test**
Create a test harness that provides minimal MicroPython stubs:
```c
// test_harness/mp_stubs.c
#include "py/mphal.h"

uint32_t mp_hal_ticks_ms(void) { return 0; }
void mp_hal_delay_us(uint32_t us) { (void)us; }
// ... other minimal stubs
```

## Expected Issues and Solutions

### Issue 1: Missing CONFIG_* Values
**Symptom**: Undefined CONFIG_BT_* macros
**Solution**: Add to autoconf.h, use IS_ENABLED() default behavior (returns 0)

### Issue 2: k_work Implementation
**Symptom**: Undefined k_work_* functions
**Solution**: Implement work queue in hal/zephyr_ble_work.c (already started)

### Issue 3: k_sem/k_mutex Implementation
**Symptom**: Undefined synchronization primitives
**Solution**: Implement in HAL using MicroPython scheduler

### Issue 4: net_buf Memory Allocation
**Symptom**: Zephyr's net_buf uses k_malloc
**Solution**: Map k_malloc to MicroPython's m_malloc

### Issue 5: SYS_INIT Macro
**Symptom**: SYS_INIT() calls in BLE sources
**Solution**: Create wrapper that registers init functions for manual calling

## Success Criteria for Phase 2

1. All BLE host sources compile without errors
2. Object files link without undefined references
3. Basic BLE init can be called from test program
4. No critical warnings (info/style warnings OK)

## Recommended Next Steps

1. **Create autoconf.h** with minimal BLE configuration
2. **Create random.h** wrapper
3. **Test compile hci_common.c** (smallest dependency)
4. **Document any new missing dependencies**
5. **Iterate** until all core BLE sources compile

## Files to Create

```
extmod/zephyr_ble/
├── zephyr/
│   ├── autoconf.h               # NEW - Kconfig values
│   └── random/
│       └── random.h             # NEW - RNG wrapper
└── hal/
    └── zephyr_ble_random.c      # NEW - RNG implementation
```

## Time Estimate

- autoconf.h creation: 30-60 minutes (researching correct CONFIG values)
- random.h + implementation: 15 minutes
- First BLE source compilation: 1-2 hours (finding/fixing issues)
- Full BLE host compilation: 4-8 hours (iterative debugging)

**Total for Phase 2**: 6-12 hours of development time
