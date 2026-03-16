# PCA10059 Zephyr BLE variant
# Uses Zephyr BLE host + on-core controller (no SoftDevice)

MICROPY_BLUETOOTH_ZEPHYR = 1
MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER = 1

# Include variant config header
CFLAGS += -DMICROPY_BOARD_VARIANT_ZEPHYR_BLE=1
CFLAGS += -include boards/$(BOARD)/mpconfigvariant_zephyr_ble.h

# PCA10059 uses USB CDC for REPL (no UART debug probe like DK)
# USB is already enabled by default in mpconfigboard.h

# No DFU bootloader when flashing via SWD.
# Clear BOOTLOADER too — the board mk sets it before variant is included.
DFU = 0
BOOTLOADER =

# LFS2 filesystem
CFLAGS += -DMICROPY_VFS_LFS2=1

# GATT pool allocator — required because nRF port uses -nostdlib (no libc malloc)
CFLAGS += -DMICROPY_BLUETOOTH_ZEPHYR_GATT_POOL=1

# Zephyr BLE iterable sections — reuse PCA10056 linker script
LD_FILES += boards/PCA10056/zephyr_ble_sections.ld
