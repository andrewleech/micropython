# PCA10056 Zephyr BLE variant
# Uses Zephyr BLE host + on-core controller (no SoftDevice)

MICROPY_BLUETOOTH_ZEPHYR = 1
MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER = 1

# Include variant config header
CFLAGS += -DMICROPY_BOARD_VARIANT_ZEPHYR_BLE=1
CFLAGS += -include boards/$(BOARD)/mpconfigvariant_zephyr_ble.h

# No SoftDevice — use full flash/RAM layout
# The base mpconfigboard.mk already includes nrf52840_1M_256k.ld
# SoftDevice LD files are only added when SD is set, which we don't

# UART REPL (no USB CDC for initial bring-up — matches Zephyr port approach)
# Can re-enable USB later once basic BLE works
CFLAGS += -DMICROPY_HW_USB_CDC=0
CFLAGS += -DMICROPY_HW_ENABLE_USBDEV=0
CFLAGS += -DMICROPY_HW_ENABLE_UART_REPL=1

# LFS2 filesystem
CFLAGS += -DMICROPY_VFS_LFS2=1

# GATT pool allocator — provides malloc/free for BLE GATT structures
# Required because nRF port uses -nostdlib (no libc malloc)
CFLAGS += -DMICROPY_BLUETOOTH_ZEPHYR_GATT_POOL=1

# Zephyr BLE iterable sections (net_buf pools, GATT services, conn callbacks, etc.)
LD_FILES += boards/$(BOARD)/zephyr_ble_sections.ld
