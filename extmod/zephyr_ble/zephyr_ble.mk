# Makefile directives for Zephyr BLE component

ifeq ($(MICROPY_BLUETOOTH_ZEPHYR),1)

EXTMOD_DIR = extmod
ZEPHYR_BLE_EXTMOD_DIR = $(EXTMOD_DIR)/zephyr_ble

SRC_EXTMOD_C += $(ZEPHYR_BLE_EXTMOD_DIR)/modbluetooth_zephyr.c
SRC_EXTMOD_C += $(ZEPHYR_BLE_EXTMOD_DIR)/hci_driver_stub.c

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
	zephyr_ble_settings.c \
	zephyr_ble_psa_crypto.c \
	zephyr_ble_util.c \
	zephyr_ble_arch.c \
	zephyr_ble_crypto_stubs.c \
	zephyr_ble_monitor_stubs.c \
	zephyr_ble_feature_stubs.c \
	zephyr_ble_array_size_stub.c \
	)

# Zephyr net_buf library (required by BLE stack)
SRC_THIRDPARTY_C += $(addprefix $(ZEPHYR_LIB_DIR)/lib/net_buf/, \
	buf.c \
	buf_simple.c \
	)

# Suppress warnings in net_buf (third-party Zephyr code)
$(BUILD)/$(ZEPHYR_LIB_DIR)/lib/net_buf/buf.o: CFLAGS += -Wno-unused-value -Wno-array-bounds -Wno-type-limits
$(BUILD)/$(ZEPHYR_LIB_DIR)/lib/net_buf/buf_simple.o: CFLAGS += -Wno-attributes

# Suppress warnings in BLE host (third-party Zephyr code)
# Use broad suppression for all BLE host files to avoid warning churn
$(BUILD)/$(ZEPHYR_LIB_DIR)/subsys/bluetooth/host/%.o: CFLAGS += -Wno-error

# gatt.c has "initializer element is not constant" error with ARRAY_SIZE
# This is a GCC limitation: sizeof(external_array) not recognized as compile-time constant
# Workaround: Use -std=gnu11 (C11 may be more lenient) or disable error for this specific case
# Note: -fpermissive is C++ only, so we use -Wno-error to downgrade the error to a warning
$(BUILD)/$(ZEPHYR_LIB_DIR)/subsys/bluetooth/host/gatt.o: ../../$(ZEPHYR_LIB_DIR)/subsys/bluetooth/host/gatt.c
	$(ECHO) "CC $<"
	$(Q)$(CC) $(filter-out -Werror,$(CFLAGS)) -Wno-error=pedantic -c -MD -MF $(@:.o=.d) -o $@ $<



# Zephyr BLE common sources (shared between host and controller)
SRC_THIRDPARTY_C += $(addprefix $(ZEPHYR_LIB_DIR)/subsys/bluetooth/common/, \
	addr.c \
	rpa.c \
	)

# Suppress warnings in common sources (third-party Zephyr code)
$(BUILD)/$(ZEPHYR_LIB_DIR)/subsys/bluetooth/common/rpa.o: CFLAGS += -Wno-error=implicit-function-declaration

# Zephyr BLE host core sources (Phase 1: Core + SMP/Keys/ECC/Crypto)
SRC_THIRDPARTY_C += $(addprefix $(ZEPHYR_LIB_DIR)/subsys/bluetooth/host/, \
	hci_core.c \
	hci_common.c \
	id.c \
	addr.c \
	buf.c \
	uuid.c \
	conn.c \
	l2cap.c \
	att.c \
	gatt.c \
	adv.c \
	scan.c \
	data.c \
	keys.c \
	smp.c \
	ecc.c \
	crypto_psa.c \
	)

# TODO Phase 2: Add ISO audio support
# iso.c

# TODO Phase 3: Add Channel Sounding support
# cs.c

# TODO Phase 4: Add BR/EDR Classic Bluetooth support
# classic/br.c classic/conn_br.c classic/ssp.c classic/l2cap_br.c
# classic/keys_br.c classic/sdp.c classic/a2dp.c classic/rfcomm.c classic/avdtp.c

# Include paths
# Note: extmod/zephyr_ble/zephyr/ contains our wrapper headers (autoconf.h, kernel.h, etc.)
# which will be found before lib/zephyr/include/zephyr/ due to include order
INC += -I$(TOP)/$(ZEPHYR_BLE_EXTMOD_DIR)
INC += -I$(TOP)/$(ZEPHYR_BLE_EXTMOD_DIR)/hal
INC += -I$(TOP)/$(ZEPHYR_LIB_DIR)/include
INC += -I$(TOP)/$(ZEPHYR_LIB_DIR)/subsys/bluetooth

endif

endif
