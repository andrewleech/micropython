# Unix variant with Zephyr BLE stack using HCI_CHANNEL_USER sockets.

FROZEN_MANIFEST ?= $(VARIANT_DIR)/manifest.py

# Soft timer backend (pthread-based) and IRQ dispatch (needed by soft timer callbacks)
SHARED_SRC_C_EXTRA += runtime/softtimer.c
SHARED_SRC_C_EXTRA += runtime/mpirq.c

# machine.Timer wrapping soft timer
SRC_EXTMOD_C += extmod/machine_timer.c

# Port identifier for Zephyr BLE stub headers (cmsis_core.h, etc.)
CFLAGS_EXTRA += -D__ZEPHYR_BLE_UNIX_PORT__

# Zephyr BLE
MICROPY_PY_BLUETOOTH = 1
MICROPY_BLUETOOTH_ZEPHYR = 1

# Linker script fragment to collect Zephyr iterable sections (net_buf_pool etc.)
LDFLAGS_EXTRA += -T$(VARIANT_DIR)/zephyr_sections.ld
