# Makefile directives for Zephyr BLE component

ifeq ($(MICROPY_BLUETOOTH_ZEPHYR),1)

EXTMOD_DIR = extmod
ZEPHYR_BLE_EXTMOD_DIR = $(EXTMOD_DIR)/zephyr_ble

# Debug output for Zephyr BLE (disabled by default)
# Set ZEPHYR_BLE_DEBUG=1 on make command line to enable debug output
ZEPHYR_BLE_DEBUG ?= 0
CFLAGS_EXTMOD += -DZEPHYR_BLE_DEBUG=$(ZEPHYR_BLE_DEBUG)

SRC_EXTMOD_C += $(ZEPHYR_BLE_EXTMOD_DIR)/modbluetooth_zephyr.c
SRC_EXTMOD_C += $(ZEPHYR_BLE_EXTMOD_DIR)/net_buf_pool_registry.c
# hci_driver_stub.c not needed when port provides its own HCI driver (e.g., mpzephyrport.c)
# SRC_EXTMOD_C += $(ZEPHYR_BLE_EXTMOD_DIR)/hci_driver_stub.c

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

# Note: No force-include needed - the stub toolchain/gcc.h includes autoconf.h
# before forwarding to the real gcc.h, ensuring CONFIG_ARM is defined in time.

ZEPHYR_LIB_DIR = lib/zephyr

# HAL abstraction layer sources
SRC_THIRDPARTY_C += $(addprefix $(ZEPHYR_BLE_EXTMOD_DIR)/hal/, \
	zephyr_ble_timer.c \
	zephyr_ble_work.c \
	zephyr_ble_sem.c \
	zephyr_ble_mutex.c \
	zephyr_ble_fifo.c \
	zephyr_ble_mem_slab.c \
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
# -Wno-format: Zephyr uses %u for uint32_t which varies by platform
$(BUILD)/$(ZEPHYR_LIB_DIR)/subsys/bluetooth/host/%.o: CFLAGS += -Wno-error -Wno-format

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
	bt_str.c \
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
	)

# TODO Phase 1.5: Add PSA Crypto or TinyCrypt support
# These files require PSA Crypto API headers which are not yet available
# Temporarily excluded - secure pairing won't work without these
# ecc.c
# crypto_psa.c

# TODO Phase 2: Add ISO audio support
# iso.c

# TODO Phase 3: Add Channel Sounding support
# cs.c

# TODO Phase 4: Add BR/EDR Classic Bluetooth support
# classic/br.c classic/conn_br.c classic/ssp.c classic/l2cap_br.c
# classic/keys_br.c classic/sdp.c classic/a2dp.c classic/rfcomm.c classic/avdtp.c

# Include paths
# Order matters: stub headers shadow real Zephyr headers when needed
# 1. zephyr_ble_extmod_dir - zephyr_ble_config.h and component headers
# 2. hal - HAL abstraction layer headers
# 3. zephyr_headers_stub - minimal stubs (autoconf.h, syscall_list.h, devicetree_generated.h)
# 4. lib/zephyr/include - real Zephyr headers (used for most includes)
# 5. subsys/bluetooth - Zephyr BLE host internal headers
INC += -I$(TOP)/$(ZEPHYR_BLE_EXTMOD_DIR)
INC += -I$(TOP)/$(ZEPHYR_BLE_EXTMOD_DIR)/hal
INC += -I$(TOP)/$(ZEPHYR_BLE_EXTMOD_DIR)/zephyr_headers_stub
INC += -I$(TOP)/$(ZEPHYR_LIB_DIR)/include
INC += -I$(TOP)/$(ZEPHYR_LIB_DIR)/subsys/bluetooth

endif

endif
