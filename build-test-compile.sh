#!/bin/bash
# Test compilation script for BLE sources
# Creates minimal stub environment

set -e

echo "Setting up minimal compile environment..."

# Create minimal stubs
cat > /tmp/mpconfigport_stub.h << 'STUB_EOF'
#ifndef MICROPY_INCLUDED_MPCONFIGPORT_H
#define MICROPY_INCLUDED_MPCONFIGPORT_H
#define MICROPY_PY_SYS_PLATFORM "test"
#define MICROPY_MALLOC_USES_ALLOCATED_SIZE (1)
#define MICROPY_MEM_STATS (0)
#define MICROPY_ENABLE_GC (1)
#define MICROPY_LONGINT_IMPL (2)
#define MICROPY_FLOAT_IMPL (1)
#define MICROPY_USE_INTERNAL_PRINTF (1)
#define MICROPY_PY_BLUETOOTH (1)
#define MICROPY_PY_BLUETOOTH_ENABLE_CENTRAL_MODE (1)
#define MICROPY_PY_BLUETOOTH_ENTER uint32_t atomic_state = 0; (void)atomic_state;
#define MICROPY_PY_BLUETOOTH_EXIT (void)atomic_state;
typedef long mp_int_t;
typedef unsigned long mp_uint_t;
typedef long mp_off_t;
#define MP_PLAT_PRINT_STRN(str, len) (void)0
#endif
STUB_EOF

cat > /tmp/mphalport_stub.h << 'STUB_EOF'
#ifndef MICROPY_INCLUDED_MPHALPORT_H
#define MICROPY_INCLUDED_MPHALPORT_H
#include <stdint.h>
static inline uint32_t mp_hal_ticks_ms(void) { return 0; }
static inline void mp_hal_delay_ms(uint32_t ms) { (void)ms; }
static inline void mp_hal_delay_us(uint32_t us) { (void)us; }
#endif
STUB_EOF

# Compile with stubs
arm-none-eabi-gcc -c lib/zephyr/subsys/bluetooth/host/l2cap.c \
    -o build-RPI_PICO_W/l2cap.o \
    -Iextmod/zephyr_ble \
    -Iextmod/zephyr_ble/hal \
    -Iextmod/zephyr_ble/zephyr \
    -Ilib/zephyr/include \
    -I/tmp \
    -include /tmp/mpconfigport_stub.h \
    -include /tmp/mphalport_stub.h \
    -Wall -Wno-cpp -Wno-error=incompatible-pointer-types \
    2>&1 | head -40

echo ""
echo "Checking if object was created..."
ls -lh build-RPI_PICO_W/l2cap.o 2>/dev/null || echo "Compilation had errors"
