# Zephyr BLE variant for Unix port.
# Provides soft timer, machine.Timer, and (future) Zephyr BLE stack.

FROZEN_MANIFEST ?= $(VARIANT_DIR)/manifest.py

# Soft timer backend (shared softtimer.c framework) and IRQ dispatch.
SHARED_SRC_C_EXTRA += runtime/softtimer.c
SHARED_SRC_C_EXTRA += runtime/mpirq.c

# Link with pthread for the timer thread.
LDFLAGS += $(LIBPTHREAD)
