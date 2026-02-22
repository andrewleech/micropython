# Makefile directives for Zephyr BLE component

ifeq ($(MICROPY_BLUETOOTH_ZEPHYR),1)

EXTMOD_DIR = extmod
ZEPHYR_BLE_EXTMOD_DIR = $(EXTMOD_DIR)/zephyr_ble

# Debug output for Zephyr BLE (disabled by default)
# Set ZEPHYR_BLE_DEBUG=1 on make command line to enable debug output
ZEPHYR_BLE_DEBUG ?= 0
CFLAGS_EXTMOD += -DZEPHYR_BLE_DEBUG=$(ZEPHYR_BLE_DEBUG)

# Suppress LTO type-mismatch warnings at link time — our stub declarations
# intentionally differ from Zephyr's internal declarations (monitor.h, ecb.h, etc.)
LDFLAGS += -Wno-lto-type-mismatch

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

# Enable L2CAP dynamic channels (COC)
CFLAGS_EXTMOD += -DMICROPY_PY_BLUETOOTH_ENABLE_L2CAP_CHANNELS=1

# Note: No force-include needed - the stub toolchain/gcc.h includes autoconf.h
# before forwarding to the real gcc.h, ensuring CONFIG_ARM is defined in time.

ZEPHYR_LIB_DIR = lib/zephyr

# NimBLE submodule provides TinyCrypt library
NIMBLE_LIB_DIR = lib/mynewt-nimble

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
	zephyr_ble_crypto.c \
	zephyr_ble_monitor_stubs.c \
	zephyr_ble_feature_stubs.c \
	zephyr_ble_array_size_stub.c \
	zephyr_ble_port_stubs.c \
	zephyr_ble_gatt_alloc.c \
	zephyr_ble_h4.c \
	)

# TinyCrypt crypto library for BLE pairing (Legacy + SC)
SRC_THIRDPARTY_C += $(addprefix $(NIMBLE_LIB_DIR)/ext/tinycrypt/src/, \
	aes_encrypt.c \
	cmac_mode.c \
	ecc.c \
	ecc_dh.c \
	utils.c \
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
# -Wno-lto-type-mismatch: stub function declarations intentionally differ from
# real Zephyr implementations (monitor.h, settings.h, ecb.h, etc.)
$(BUILD)/$(ZEPHYR_LIB_DIR)/subsys/bluetooth/host/%.o: CFLAGS += -Wno-error -Wno-format -Wno-lto-type-mismatch
$(BUILD)/$(ZEPHYR_LIB_DIR)/subsys/bluetooth/common/%.o: CFLAGS += -Wno-lto-type-mismatch

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
INC += -I$(TOP)/$(NIMBLE_LIB_DIR)/ext/tinycrypt/include

# Suppress warnings in TinyCrypt (third-party code)
$(BUILD)/$(NIMBLE_LIB_DIR)/ext/tinycrypt/%.o: CFLAGS += -Wno-error

# =============================================================================
# Zephyr BLE Controller (on-core, Nordic nRF5x only)
# =============================================================================
ifeq ($(MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER),1)

CFLAGS_EXTMOD += -DMICROPY_BLUETOOTH_ZEPHYR_CONTROLLER=1

# Controller HCI interface
SRC_THIRDPARTY_C += $(addprefix $(ZEPHYR_LIB_DIR)/subsys/bluetooth/controller/hci/, \
	hci_driver.c \
	hci.c \
	)

# Nordic vendor HCI (reads FICR device address for stable BLE identity)
SRC_THIRDPARTY_C += $(ZEPHYR_LIB_DIR)/subsys/bluetooth/controller/ll_sw/nordic/hci/hci_vendor.c

# Upper Link Layer (ULL) — core
SRC_THIRDPARTY_C += $(addprefix $(ZEPHYR_LIB_DIR)/subsys/bluetooth/controller/ll_sw/, \
	ull.c \
	ull_adv.c \
	ull_scan.c \
	ull_conn.c \
	ull_central.c \
	ull_peripheral.c \
	ull_filter.c \
	ull_chan.c \
	ull_sched.c \
	ull_tx_queue.c \
	ll_addr.c \
	ll_feat.c \
	ll_settings.c \
	ll_tx_pwr.c \
	lll_common.c \
	lll_chan.c \
	)

# ULL Link Layer Control Procedure (LLCP) files
SRC_THIRDPARTY_C += $(addprefix $(ZEPHYR_LIB_DIR)/subsys/bluetooth/controller/ll_sw/, \
	ull_llcp.c \
	ull_llcp_cc.c \
	ull_llcp_chmu.c \
	ull_llcp_common.c \
	ull_llcp_conn_upd.c \
	ull_llcp_enc.c \
	ull_llcp_local.c \
	ull_llcp_past.c \
	ull_llcp_pdu.c \
	ull_llcp_remote.c \
	)

# Lower Link Layer (LLL) — Nordic-specific
SRC_THIRDPARTY_C += $(addprefix $(ZEPHYR_LIB_DIR)/subsys/bluetooth/controller/ll_sw/nordic/lll/, \
	lll.c \
	lll_adv.c \
	lll_scan.c \
	lll_conn.c \
	lll_central.c \
	lll_peripheral.c \
	)

# Nordic HAL
SRC_THIRDPARTY_C += $(addprefix $(ZEPHYR_LIB_DIR)/subsys/bluetooth/controller/ll_sw/nordic/hal/nrf5/, \
	radio/radio.c \
	ticker.c \
	cntr.c \
	ecb.c \
	mayfly.c \
	)

# Controller ticker
SRC_THIRDPARTY_C += $(ZEPHYR_LIB_DIR)/subsys/bluetooth/controller/ticker/ticker.c

# Controller utilities
SRC_THIRDPARTY_C += $(addprefix $(ZEPHYR_LIB_DIR)/subsys/bluetooth/controller/util/, \
	mayfly.c \
	memq.c \
	dbuf.c \
	mem.c \
	util.c \
	)

# IRQ management shims, ISR stubs, and controller kernel stubs
SRC_THIRDPARTY_C += $(addprefix $(ZEPHYR_BLE_EXTMOD_DIR)/hal/, \
	zephyr_ble_irq.c \
	zephyr_ble_isr.c \
	zephyr_ble_clock.c \
	zephyr_ble_controller_stubs.c \
	)

# Controller include paths
INC += -I$(TOP)/$(ZEPHYR_LIB_DIR)/subsys/bluetooth/controller
INC += -I$(TOP)/$(ZEPHYR_LIB_DIR)/subsys/bluetooth/controller/include
INC += -I$(TOP)/$(ZEPHYR_LIB_DIR)/subsys/bluetooth/controller/ll_sw
INC += -I$(TOP)/$(ZEPHYR_LIB_DIR)/subsys/bluetooth/controller/ll_sw/nordic
INC += -I$(TOP)/$(ZEPHYR_LIB_DIR)/subsys/bluetooth/controller/ll_sw/nordic/hal/nrf5
INC += -I$(TOP)/$(ZEPHYR_LIB_DIR)/subsys/bluetooth/controller/ll_sw/nordic/hci

# nRF SoC common headers (nrf_sys_event.h needed by radio.c)
INC += -I$(TOP)/$(ZEPHYR_LIB_DIR)/soc/nordic/common

# Suppress warnings in controller sources (third-party code)
# Force-include headers needed by controller that aren't transitively included:
# - hci_vs.h: bt_hci_vs_static_addr used in hci_internal.h
# - buf.h: bt_buf_get_evt/rx used in hci_driver.c
# - zephyr_ble_irq.h: irq_enable/disable/connect used by HAL code
# nrfx CCM event name compatibility (older nrfx uses ENDCRYPT, newer uses END)
CFLAGS_CONTROLLER_COMPAT = -DNRF_CCM_EVENT_END=NRF_CCM_EVENT_ENDCRYPT

$(BUILD)/$(ZEPHYR_LIB_DIR)/subsys/bluetooth/controller/%.o: CFLAGS += -Wno-error -Wno-format -Wno-unused-variable \
	-Wno-lto-type-mismatch \
	-include zephyr/bluetooth/hci_vs.h -include zephyr/bluetooth/buf.h \
	-include zephyr_ble_irq.h $(CFLAGS_CONTROLLER_COMPAT)

endif # MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER

endif

endif
