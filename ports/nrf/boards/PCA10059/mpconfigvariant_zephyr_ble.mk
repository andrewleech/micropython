# PCA10059 Zephyr BLE variant
# Uses Zephyr BLE host + on-core controller (no SoftDevice)

MICROPY_BLUETOOTH_ZEPHYR = 1
MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER = 1

# Include variant config header
CFLAGS += -DMICROPY_BOARD_VARIANT_ZEPHYR_BLE=1
CFLAGS += -include boards/$(BOARD)/mpconfigvariant_zephyr_ble.h

# PCA10059 uses USB for both CDC and DFU bootloader — keep USB enabled
