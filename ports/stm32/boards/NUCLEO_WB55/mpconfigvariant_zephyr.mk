# Zephyr BLE variant for NUCLEO_WB55
# Override the default NimBLE stack with Zephyr BLE

# Enable Bluetooth with Zephyr stack
MICROPY_PY_BLUETOOTH = 1
MICROPY_BLUETOOTH_NIMBLE = 0
MICROPY_BLUETOOTH_ZEPHYR = 1

# Force linker to keep HCI device symbols (prevent --gc-sections from removing them)
LDFLAGS += -Wl,--undefined=__device_dts_ord_0 -Wl,--undefined=mp_bluetooth_zephyr_hci_dev
