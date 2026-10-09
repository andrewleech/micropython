# mboot_common.mk - build fragment for shared/mboot.
#
# Included by ports/<port>/mboot/mcuboot/Makefile (MBOOT_ROLE=bootloader) and by the port's
# app Makefile when MBOOT_BACKEND=mcuboot (MBOOT_ROLE=app):
#
#   include $(TOP)/shared/mboot/mboot_common.mk
#   INC += $(MBOOT_INC)
#   CFLAGS += $(MBOOT_CFLAGS)
#   SRC_C += $(MBOOT_SRC_C)
#   SRC_C += $(MBOOT_GEN_SRC_C)     (absolute path under $(MBOOT_GEN_DIR))
#
# The configuration is C: the board's mpconfigboard.h and the port's mboot_dev.h behind
# shared/mboot/include/mboot_layout.h. Parsing this fragment runs tools/mboot_gen.py once,
# which preprocesses that header and writes the linker symbols, the make variables
# (mboot_layout.mk, included here) and the layout for the signing tools. The fragment defines
# variables only, rules are in mboot_rules.mk.
#
# Inputs, set before including:
#   TOP                 repository root
#   BUILD               build directory
#   BOARD_DIR           board directory (mpconfigboard.h)
#   MBOOT_ROLE        bootloader or app
#   MBOOT_DEV_DIR     directory of the port's mboot_dev.h
#   MBOOT_CPP_FLAGS   options the preprocessor needs to read the board, for example -D<MCU>
#   MBOOT_PUBKEY      PEM key whose public part the bootloader accepts (default: the test key)
#   MBOOT_SIGN_KEY    private signing key of the app role (default: the test key)
#   MBOOT_PRODUCTION  1 refuses the public test keys, and MBOOT_VALIDATE_PRIMARY 0 (the C
#                       configuration sees it as -DMBOOT_PRODUCTION)
#   MBOOT_TEST_FI     1 adds the fault injection hook of the test campaigns
#   MBOOT_MBEDTLS_SRC_C  with MBOOT_CRYPTO_SEL_MBEDTLS: the Mbed TLS sources (relative to TOP) and
#                       the port's own include path and MBEDTLS_CONFIG_FILE for them
#   PYTHON              python3

ifeq ($(TOP),)
$(error mboot_common.mk: TOP is not set)
endif
ifeq ($(BUILD),)
$(error mboot_common.mk: BUILD is not set)
endif
ifeq ($(filter $(MBOOT_ROLE),bootloader app),)
$(error mboot_common.mk: MBOOT_ROLE must be bootloader or app, not '$(MBOOT_ROLE)')
endif
ifeq ($(wildcard $(BOARD_DIR)/mpconfigboard.h),)
$(error mboot_common.mk: $(BOARD_DIR)/mpconfigboard.h not found)
endif

PYTHON ?= python3
MBOOT_PRODUCTION ?= 0
MBOOT_TEST_FI ?= 0
MBOOT_PUBKEY ?= $(TOP)/tests/mboot/keys/test-ecdsa-p256.pem
ifneq ($(MBOOT_PRODUCTION),1)
MBOOT_SIGN_KEY ?= $(TOP)/tests/mboot/keys/test-ecdsa-p256.pem
endif
MBOOT_GEN_DIR ?= $(BUILD)/mboot_gen

MBOOT_DIR := $(TOP)/shared/mboot
MBOOT_LIB_DIR := $(TOP)/lib/mcuboot

# The include path of the generator is the part that the configuration headers need; the include
# path of the build gets the crypto library headers once the configuration is known.
MBOOT_GEN_INC := \
    -I$(MBOOT_DIR)/include \
    -I$(BOARD_DIR) \
    -I$(MBOOT_DEV_DIR)
MBOOT_INC := $(MBOOT_GEN_INC) \
    -I$(MBOOT_LIB_DIR)/boot/bootutil/include \
    -I$(MBOOT_LIB_DIR)/boot/bootutil/src

# Generation. The command runs when the fragment is parsed so that the include below works in a
# clean tree; mboot_rules.mk regenerates when the headers or the key change.
MBOOT_GEN_CMD = $(PYTHON) $(TOP)/tools/mboot_gen.py --out $(MBOOT_GEN_DIR) \
    --board $(notdir $(BOARD_DIR:/=)) --key $(MBOOT_PUBKEY) \
    -- $(CC) $(MBOOT_GEN_INC) $(MBOOT_CPP_FLAGS) -DMBOOT_PRODUCTION=$(MBOOT_PRODUCTION)
MBOOT_GEN_LOG := $(shell $(MBOOT_GEN_CMD) 2>&1 || echo MBOOT_GEN_FAILED)
ifneq ($(findstring MBOOT_GEN_FAILED,$(MBOOT_GEN_LOG)),)
$(error mboot_gen.py failed: $(MBOOT_GEN_LOG))
endif
ifeq ($(MBOOT_PRODUCTION),1)
$(if $(filter 0,$(shell $(PYTHON) $(TOP)/tools/mboot_sign.py check-key --production $(MBOOT_PUBKEY) >/dev/null 2>&1; echo $$?)),,\
    $(error MBOOT_PRODUCTION=1 and $(MBOOT_PUBKEY) is a public test key))
endif

# MBOOT_APP_LINK_ADDR, MBOOT_PRIMARY_ADDR, MBOOT_LAYOUT_ID, MBOOT_DFU_ENABLE,
# MBOOT_DFU_FLASH_WRITE_ALIGN, MBOOT_DFU_UPDATE_ADDR, MBOOT_BL_VERSION, MBOOT_SPIFLASH_ENABLE,
# MBOOT_IMGTOOL_SIGN_ARGS, MBOOT_IMGTOOL_INITIAL_ARGS and the choices that select sources:
# MBOOT_POLICY (swap, overwrite-external, single), MBOOT_SWAP_MODE (offset, move, scratch),
# MBOOT_CRYPTO (tinycrypt, mbedtls), MBOOT_ROLLBACK (none, version, counter-flash,
# counter-port), MBOOT_FIH (off, low, medium, high), MBOOT_CONFIRM_MODE (manual, auto),
# MBOOT_HASH_DIRECT, MBOOT_VALIDATE_PRIMARY, MBOOT_MCUBOOT_FSLOAD (words of raw, fat, lfs2, gzip)
# and MBOOT_FSLOAD_ENABLE.
include $(MBOOT_GEN_DIR)/mboot_layout.mk

MBOOT_LD_LAYOUT := $(MBOOT_GEN_DIR)/mboot_layout.ld

# The ASN.1 parser configuration is only for the sources of the bootloader role with tinycrypt:
# the app role builds bootutil_public.c, which does not use Mbed TLS (and the app may have its
# own MBEDTLS_CONFIG_FILE), and an Mbed TLS build takes its configuration and headers from the
# port.
ifeq ($(MBOOT_CRYPTO),mbedtls)
ifeq ($(MBOOT_ROLE)$(MBOOT_MBEDTLS_SRC_C),bootloader)
$(error mboot_common.mk: MBOOT_CRYPTO_SEL_MBEDTLS needs MBOOT_MBEDTLS_SRC_C from the port)
endif
else
MBOOT_INC += -I$(MBOOT_LIB_DIR)/ext/tinycrypt/lib/include \
    -I$(MBOOT_LIB_DIR)/ext/mbedtls-asn1/include
endif
ifeq ($(MBOOT_ROLE),bootloader)
MBOOT_CFLAGS := -DMBOOT_ROLE_BOOTLOADER=1
ifneq ($(MBOOT_CRYPTO),mbedtls)
MBOOT_CFLAGS += -DMBEDTLS_CONFIG_FILE='"mbedtls_config_asn1.h"'
endif
else
MBOOT_CFLAGS := -DMBOOT_ROLE_APP=1
endif
MBOOT_CFLAGS += -DMBOOT_PRODUCTION=$(MBOOT_PRODUCTION)
ifeq ($(MBOOT_TEST_FI),1)
MBOOT_CFLAGS += -DMBOOT_TEST_FI=1
endif
MBOOT_CFLAGS += -DMBOOT_BL_VERSION='"$(MBOOT_BL_VERSION)"'

MBOOT_GEN_SRC_C :=
ifeq ($(MBOOT_ROLE),bootloader)
MBOOT_GEN_SRC_C += $(MBOOT_GEN_DIR)/mboot_keys_gen.c
endif

# Sources, relative to TOP.
MBOOT_BU_DIR := lib/mcuboot/boot/bootutil/src
MBOOT_SRC_DIR := shared/mboot/src
MBOOT_DFU_DIR := shared/mboot/dfu
MBOOT_TINYUSB_SRC_C :=

# Flash map, flash access policy, logging, request handoff, update audit log and the update slot
# session, in both roles. shadow.c has no code without the ECC policy.
MBOOT_GLUE_SRC_C := $(addprefix $(MBOOT_SRC_DIR)/, \
    flash_map.c flash_map_backend.c shadow.c record_ring.c request.c updatelog.c update.c mboot_crc32.c log.c printf_lite.c)

# A SPI flash as device 1 is driven through drivers/memory/spiflash.c, which the port provides.
ifeq ($(MBOOT_SPIFLASH_ENABLE),1)
MBOOT_GLUE_SRC_C += $(MBOOT_SRC_DIR)/flash_spiflash.c
endif

ifeq ($(MBOOT_ROLE),app)
MBOOT_BOOTUTIL_SRC_C := $(MBOOT_BU_DIR)/bootutil_public.c
MBOOT_CRYPTO_SRC_C :=
MBOOT_FRONT_SRC_C := $(MBOOT_SRC_DIR)/app_api.c
else
# Image validation and the boot decision.
MBOOT_BOOTUTIL_SRC_C := $(addprefix $(MBOOT_BU_DIR)/, \
    tlv.c bootutil_find_key.c bootutil_img_hash.c bootutil_img_security_cnt.c image_validate.c \
    image_ecdsa.c bootutil_misc.c bootutil_area.c bootutil_loader.c bootutil_public.c \
    fault_injection_hardening.c)
MBOOT_GLUE_SRC_C += $(addprefix $(MBOOT_SRC_DIR)/, security_cnt.c seccnt_flash.c arm_jump.c)
ifeq ($(MBOOT_FIH),high)
MBOOT_GLUE_SRC_C += $(MBOOT_SRC_DIR)/fih_delay_rng.c
endif

# The loader and the swap algorithm: the single slot policy has no swap and takes the loader
# that boots the only slot, overwrite-external is the overwrite variant of the scratch swap file.
ifeq ($(MBOOT_POLICY),single)
MBOOT_BOOTUTIL_SRC_C += lib/mcuboot/boot/zephyr/single_loader.c
else
MBOOT_BOOTUTIL_SRC_C += $(addprefix $(MBOOT_BU_DIR)/, loader.c swap_misc.c caps.c)
ifeq ($(MBOOT_POLICY),overwrite-external)
MBOOT_BOOTUTIL_SRC_C += $(MBOOT_BU_DIR)/swap_scratch.c
else ifeq ($(MBOOT_SWAP_MODE),move)
MBOOT_BOOTUTIL_SRC_C += $(MBOOT_BU_DIR)/swap_move.c
else ifeq ($(MBOOT_SWAP_MODE),scratch)
MBOOT_BOOTUTIL_SRC_C += $(MBOOT_BU_DIR)/swap_scratch.c
else
MBOOT_BOOTUTIL_SRC_C += $(MBOOT_BU_DIR)/swap_offset.c
endif
endif

ifeq ($(MBOOT_CRYPTO),mbedtls)
MBOOT_CRYPTO_SRC_C := $(MBOOT_MBEDTLS_SRC_C)
else
MBOOT_CRYPTO_SRC_C := $(addprefix lib/mcuboot/ext/tinycrypt/lib/source/, ecc.c ecc_dsa.c sha256.c utils.c) \
    $(addprefix lib/mcuboot/ext/mbedtls-asn1/src/, asn1parse.c platform_util.c)
endif

# Boot flow, validation and the front ends.
MBOOT_FRONT_SRC_C := $(addprefix $(MBOOT_SRC_DIR)/, boot_main.c validate.c)
ifeq ($(MBOOT_DFU_ENABLE),1)
# dfu_glue.c binds the DFU core of shared/mboot/dfu. The core pads every download block to
# MBOOT_DFU_WRITE_ALIGN, which has to be the value the glue checks against.
MBOOT_FRONT_SRC_C += $(addprefix $(MBOOT_SRC_DIR)/, dfu_glue.c dfu_regions.c) \
    $(addprefix $(MBOOT_DFU_DIR)/core/, mboot_dfu.c mboot_region.c mboot_usbd.c) \
    $(MBOOT_SRC_DIR)/mboot_elem.c
MBOOT_INC += -I$(TOP)/$(MBOOT_DFU_DIR)/include
# The TinyUSB device core and the DFU class with the glue's vendor requests. The device controller
# driver and the TinyUSB include path (tusb_config.h) belong to the port.
MBOOT_TINYUSB_SRC_C := $(addprefix lib/tinyusb/src/, \
    tusb.c common/tusb_fifo.c device/usbd.c class/dfu/dfu_device.c class/vendor/vendor_device.c)
MBOOT_CFLAGS += -DMBOOT_DFU_WRITE_ALIGN=$(MBOOT_DFU_FLASH_WRITE_ALIGN)
endif
ifeq ($(MBOOT_FSLOAD_ENABLE),1)
MBOOT_FRONT_SRC_C += $(addprefix $(MBOOT_SRC_DIR)/, fsload.c fsload_stream.c)
# fsload consumes the shared element parser independently of USB.
ifneq ($(MBOOT_DFU_ENABLE),1)
MBOOT_FRONT_SRC_C += $(MBOOT_SRC_DIR)/mboot_elem.c
endif
ifneq ($(filter raw,$(MBOOT_MCUBOOT_FSLOAD)),)
MBOOT_FRONT_SRC_C += $(MBOOT_SRC_DIR)/vfs_raw.c
endif
# The readers include the library headers relative to the repository root. The library
# configuration is the same for the reader and the library (flags below), and has to stay so.
MBOOT_INC += -I$(TOP)
ifneq ($(filter fat,$(MBOOT_MCUBOOT_FSLOAD)),)
MBOOT_FRONT_SRC_C += $(MBOOT_SRC_DIR)/vfs_fat.c lib/oofatfs/ff.c lib/oofatfs/ffunicode.c
MBOOT_CFLAGS += -DFFCONF_H='"mboot_ffconf.h"'
endif
ifneq ($(filter lfs2,$(MBOOT_MCUBOOT_FSLOAD)),)
MBOOT_FRONT_SRC_C += $(MBOOT_SRC_DIR)/vfs_lfs.c lib/littlefs/lfs2.c lib/littlefs/lfs2_util.c
MBOOT_CFLAGS += -DLFS2_NO_MALLOC -DLFS2_NO_DEBUG -DLFS2_NO_WARN -DLFS2_NO_ERROR -DLFS2_NO_ASSERT -DLFS2_READONLY
endif
ifneq ($(filter gzip,$(MBOOT_MCUBOOT_FSLOAD)),)
MBOOT_FRONT_SRC_C += $(MBOOT_SRC_DIR)/gzstream.c $(addprefix lib/uzlib/, adler32.c crc32.c header.c tinflate.c)
MBOOT_CFLAGS += -DUZLIB_CONF_PARANOID_CHECKS=1
endif
endif
endif

MBOOT_SRC_C := $(MBOOT_BOOTUTIL_SRC_C) $(MBOOT_CRYPTO_SRC_C) $(MBOOT_GLUE_SRC_C) $(MBOOT_FRONT_SRC_C)
