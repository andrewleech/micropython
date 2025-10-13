# Zephyr BLE variant for NUCLEO_WB55
# Override the default NimBLE stack with Zephyr BLE

# Enable Bluetooth with Zephyr stack
MICROPY_PY_BLUETOOTH = 1
MICROPY_BLUETOOTH_NIMBLE = 0
MICROPY_BLUETOOTH_ZEPHYR = 1

# Use ROM vector table for probe-rs compatibility
# The RAM variant requires debugger preload and doesn't work with direct flash reset
MICROPY_HW_ENABLE_ISR_UART_FLASH_FUNCS_IN_RAM = 0

# Force linker to keep HCI device symbols (prevent --gc-sections from removing them)
LDFLAGS += -Wl,--undefined=__device_dts_ord_0 -Wl,--undefined=mp_bluetooth_zephyr_hci_dev
