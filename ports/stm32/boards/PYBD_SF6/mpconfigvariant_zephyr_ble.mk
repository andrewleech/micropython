# Zephyr BLE variant for PYBD_SF6
# Swaps NimBLE host stack for Zephyr BLE host, using same CYW43 UART HCI transport.

# Enable Bluetooth with Zephyr stack (disable NimBLE default)
MICROPY_PY_BLUETOOTH = 1
MICROPY_BLUETOOTH_NIMBLE = 0
MICROPY_BLUETOOTH_ZEPHYR = 1

# Use bump allocator for GATT structures (STM32 port uses -nostdlib, no libc malloc)
CFLAGS += -DMICROPY_BLUETOOTH_ZEPHYR_GATT_POOL=1

# Force linker to keep HCI device symbols (prevent --gc-sections from removing them)
LDFLAGS += -Wl,--undefined=__device_dts_ord_0 -Wl,--undefined=mp_bluetooth_zephyr_hci_dev
