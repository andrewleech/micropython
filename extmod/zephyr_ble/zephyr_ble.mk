# Makefile directives for Zephyr BLE component

ifeq ($(MICROPY_BLUETOOTH_ZEPHYR),1)

EXTMOD_DIR = extmod
ZEPHYR_BLE_EXTMOD_DIR = $(EXTMOD_DIR)/zephyr_ble

SRC_EXTMOD_C += $(ZEPHYR_BLE_EXTMOD_DIR)/modbluetooth_zephyr.c

CFLAGS_EXTMOD += -DMICROPY_BLUETOOTH_ZEPHYR=1

# Use Zephyr BLE from lib/zephyr submodule
MICROPY_BLUETOOTH_ZEPHYR_BINDINGS_ONLY ?= 0

CFLAGS_EXTMOD += -DMICROPY_BLUETOOTH_ZEPHYR_BINDINGS_ONLY=$(MICROPY_BLUETOOTH_ZEPHYR_BINDINGS_ONLY)

ifeq ($(MICROPY_BLUETOOTH_ZEPHYR_BINDINGS_ONLY),0)

GIT_SUBMODULES += lib/zephyr

# Zephyr BLE uses synchronous events like NimBLE
CFLAGS_EXTMOD += -DMICROPY_PY_BLUETOOTH_USE_SYNC_EVENTS=1

# Enable pairing and bonding with synchronous events
CFLAGS_EXTMOD += -DMICROPY_PY_BLUETOOTH_ENABLE_PAIRING_BONDING=1

ZEPHYR_LIB_DIR = lib/zephyr

# HAL abstraction layer sources
SRC_THIRDPARTY_C += $(addprefix $(ZEPHYR_BLE_EXTMOD_DIR)/hal/, \
	zephyr_ble_timer.c \
	zephyr_ble_work.c \
	zephyr_ble_sem.c \
	zephyr_ble_mutex.c \
	zephyr_ble_kernel.c \
	zephyr_ble_poll.c \
	)

# Zephyr net_buf library (required by BLE stack)
SRC_THIRDPARTY_C += $(addprefix $(ZEPHYR_LIB_DIR)/lib/net_buf/, \
	buf.c \
	buf_simple.c \
	)

# TODO: Add Zephyr BLE host sources

# Include paths
# Note: extmod/zephyr_ble/zephyr/ contains our wrapper headers (autoconf.h, kernel.h, etc.)
# which will be found before lib/zephyr/include/zephyr/ due to include order
INC += -I$(TOP)/$(ZEPHYR_BLE_EXTMOD_DIR)
INC += -I$(TOP)/$(ZEPHYR_BLE_EXTMOD_DIR)/hal
INC += -I$(TOP)/$(ZEPHYR_LIB_DIR)/include

endif

endif
