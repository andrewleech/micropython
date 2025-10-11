# Build System Update - Random Number Generation

## Date: 2025-10-10

## Changes Made

### Files Modified

1. **`zephyr_ble.mk`** - Added `zephyr_ble_random.c` to HAL sources (line 38)
2. **`zephyr_ble.cmake`** - Added `zephyr_ble_random.c` to target sources (line 23)

### New HAL Source

**`hal/zephyr_ble_random.c`** - Random number generation implementation
- STM32 with hardware RNG: HAL_RNG_GenerateRandomNumber()
- Unix/macOS: /dev/urandom
- WebAssembly: crypto.getRandomValues
- Generic fallback: stdlib rand()

### Build System Status

Both build systems now complete:

#### Makefile (zephyr_ble.mk) ✅
```makefile
# HAL sources (7 files)
SRC_THIRDPARTY_C += $(addprefix $(ZEPHYR_BLE_EXTMOD_DIR)/hal/, \
    zephyr_ble_timer.c \
    zephyr_ble_work.c \
    zephyr_ble_sem.c \
    zephyr_ble_mutex.c \
    zephyr_ble_kernel.c \
    zephyr_ble_poll.c \
    zephyr_ble_random.c \      # ← NEW
    )
```

#### CMake (zephyr_ble.cmake) ✅
```cmake
target_sources(micropy_extmod_zephyr_ble INTERFACE
    # ... other sources ...
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_random.c  # ← NEW
    # ... BLE sources ...
)
```

### Include Paths

Both build systems correctly include:
- `extmod/zephyr_ble/` - Base directory (finds zephyr/ subdirectory)
- `extmod/zephyr_ble/hal/` - HAL headers
- `lib/zephyr/include` - Zephyr API headers

When BLE code does `#include <zephyr/sys/util.h>`:
1. Looks in `extmod/zephyr_ble/zephyr/sys/util.h` (wrapper) ✓
2. Falls back to `lib/zephyr/include/zephyr/sys/util.h` if not found

### Verification

Both build systems are now complete and symmetric:
- ✅ Same HAL sources (7 files including random.c)
- ✅ Same BLE sources (18 files)
- ✅ Same include paths
- ✅ Same compile definitions

## Testing Recommendations

### Makefile Ports
```bash
cd ports/unix
make MICROPY_BLUETOOTH_ZEPHYR=1
```

### CMake Ports
```bash
cd ports/rp2
mkdir build && cd build
cmake .. -DMICROPY_BLUETOOTH_ZEPHYR=1
make
```

## Files Summary

### HAL Sources (7)
1. zephyr_ble_timer.c - k_timer implementation
2. zephyr_ble_work.c - k_work implementation
3. zephyr_ble_sem.c - k_sem implementation
4. zephyr_ble_mutex.c - k_mutex implementation
5. zephyr_ble_kernel.c - Kernel functions (k_sleep, k_uptime_get)
6. zephyr_ble_poll.c - Polling mechanism
7. **zephyr_ble_random.c** - Random number generation (NEW)

### Wrapper Headers (18)
All located in `zephyr/` subdirectory with appropriate paths

### Configuration (2)
1. zephyr/autoconf.h - Delegates to config
2. zephyr_ble_config.h - 123 CONFIG_* values

## Build System Complete ✅

Both Makefile and CMake build systems now include all necessary components for Zephyr BLE integration:
- ✅ All HAL implementations
- ✅ All wrapper headers
- ✅ Configuration files
- ✅ Include paths
- ✅ Compile definitions

Ready for port integration and testing.
