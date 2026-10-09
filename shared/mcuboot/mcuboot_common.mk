# mcuboot_common.mk - build fragment for shared/mcuboot.
#
# Included by ports/<port>/mcuboot/Makefile (MCUBOOT_ROLE=bootloader) and by the port's
# app Makefile when MCUBOOT=1 (MCUBOOT_ROLE=app):
#
#   include $(TOP)/shared/mcuboot/mcuboot_common.mk
#   INC += $(MCUBOOT_INC)
#   CFLAGS += $(MCUBOOT_CFLAGS)
#   SRC_C += $(MCUBOOT_SRC_C)
#   SRC_C += $(MCUBOOT_GEN_SRC_C)     (absolute path under $(MCUBOOT_GEN_DIR))
#
# The configuration is C: the board's mpconfigboard.h and the port's mcuboot_dev.h behind
# shared/mcuboot/include/mcuboot_layout.h. Parsing this fragment runs tools/mcuboot_gen.py once,
# which preprocesses that header and writes the linker symbols, the make variables
# (mcuboot_layout.mk, included here) and the layout for the signing tools. The fragment defines
# variables only, rules are in mcuboot_rules.mk.
#
# Inputs, set before including:
#   TOP                 repository root
#   BUILD               build directory
#   BOARD_DIR           board directory (mpconfigboard.h)
#   MCUBOOT_ROLE        bootloader or app
#   MCUBOOT_DEV_DIR     directory of the port's mcuboot_dev.h
#   MCUBOOT_CPP_FLAGS   options the preprocessor needs to read the board, for example -D<MCU>
#   MCUBOOT_PUBKEY      PEM key whose public part the bootloader accepts (default: the test key)
#   MCUBOOT_SIGN_KEY    private signing key of the app role (default: the test key)
#   MCUBOOT_PRODUCTION  1 refuses the public test keys, and MCUBOOT_VALIDATE_PRIMARY 0 (the C
#                       configuration sees it as -DMCUBOOT_PRODUCTION)
#   MCUBOOT_TEST_FI     1 adds the fault injection hook of the test campaigns
#   MCUBOOT_MBEDTLS_SRC_C  with MCUBOOT_CRYPTO_SEL_MBEDTLS: the Mbed TLS sources (relative to TOP) and
#                       the port's own include path and MBEDTLS_CONFIG_FILE for them
#   PYTHON              python3

ifeq ($(TOP),)
$(error mcuboot_common.mk: TOP is not set)
endif
ifeq ($(BUILD),)
$(error mcuboot_common.mk: BUILD is not set)
endif
ifeq ($(filter $(MCUBOOT_ROLE),bootloader app),)
$(error mcuboot_common.mk: MCUBOOT_ROLE must be bootloader or app, not '$(MCUBOOT_ROLE)')
endif
ifeq ($(wildcard $(BOARD_DIR)/mpconfigboard.h),)
$(error mcuboot_common.mk: $(BOARD_DIR)/mpconfigboard.h not found)
endif

PYTHON ?= python3
MCUBOOT_PRODUCTION ?= 0
MCUBOOT_TEST_FI ?= 0
MCUBOOT_PUBKEY ?= $(TOP)/tests/mcuboot/keys/test-ecdsa-p256.pem
ifneq ($(MCUBOOT_PRODUCTION),1)
MCUBOOT_SIGN_KEY ?= $(TOP)/tests/mcuboot/keys/test-ecdsa-p256.pem
endif
MCUBOOT_GEN_DIR ?= $(BUILD)/mcuboot_gen

MCUBOOT_DIR := $(TOP)/shared/mcuboot
MCUBOOT_LIB_DIR := $(TOP)/lib/mcuboot

# The include path of the generator is the part that the configuration headers need; the include
# path of the build gets the crypto library headers once the configuration is known.
MCUBOOT_GEN_INC := \
    -I$(MCUBOOT_DIR)/include \
    -I$(BOARD_DIR) \
    -I$(MCUBOOT_DEV_DIR)
MCUBOOT_INC := $(MCUBOOT_GEN_INC) \
    -I$(MCUBOOT_LIB_DIR)/boot/bootutil/include \
    -I$(MCUBOOT_LIB_DIR)/boot/bootutil/src

# Generation. The command runs when the fragment is parsed so that the include below works in a
# clean tree; mcuboot_rules.mk regenerates when the headers or the key change.
MCUBOOT_GEN_CMD = $(PYTHON) $(TOP)/tools/mcuboot_gen.py --out $(MCUBOOT_GEN_DIR) \
    --board $(notdir $(BOARD_DIR:/=)) --key $(MCUBOOT_PUBKEY) \
    -- $(CC) $(MCUBOOT_GEN_INC) $(MCUBOOT_CPP_FLAGS) -DMCUBOOT_PRODUCTION=$(MCUBOOT_PRODUCTION)
MCUBOOT_GEN_LOG := $(shell $(MCUBOOT_GEN_CMD) 2>&1 || echo MCUBOOT_GEN_FAILED)
ifneq ($(findstring MCUBOOT_GEN_FAILED,$(MCUBOOT_GEN_LOG)),)
$(error mcuboot_gen.py failed: $(MCUBOOT_GEN_LOG))
endif
ifeq ($(MCUBOOT_PRODUCTION),1)
$(if $(filter 0,$(shell $(PYTHON) $(TOP)/tools/mcuboot_sign.py check-key --production $(MCUBOOT_PUBKEY) >/dev/null 2>&1; echo $$?)),,\
    $(error MCUBOOT_PRODUCTION=1 and $(MCUBOOT_PUBKEY) is a public test key))
endif

# MCUBOOT_APP_LINK_ADDR, MCUBOOT_PRIMARY_ADDR, MCUBOOT_LAYOUT_ID, MCUBOOT_DFU_ENABLE,
# MCUBOOT_DFU_WRITE_ALIGN, MCUBOOT_DFU_UPDATE_ADDR, MCUBOOT_BL_VERSION, MCUBOOT_SPIFLASH_ENABLE,
# MCUBOOT_IMGTOOL_SIGN_ARGS, MCUBOOT_IMGTOOL_INITIAL_ARGS and the choices that select sources:
# MCUBOOT_POLICY (swap, overwrite-external, single), MCUBOOT_SWAP_MODE (offset, move, scratch),
# MCUBOOT_CRYPTO (tinycrypt, mbedtls), MCUBOOT_ROLLBACK (none, version, counter-flash,
# counter-port), MCUBOOT_FIH (off, low, medium, high), MCUBOOT_CONFIRM_MODE (manual, auto),
# MCUBOOT_HASH_DIRECT, MCUBOOT_VALIDATE_PRIMARY, MCUBOOT_FSLOAD (words of raw, fat, lfs2, gzip)
# and MCUBOOT_FSLOAD_ENABLE.
include $(MCUBOOT_GEN_DIR)/mcuboot_layout.mk

MCUBOOT_LD_LAYOUT := $(MCUBOOT_GEN_DIR)/mcuboot_layout.ld

# The ASN.1 parser configuration is only for the sources of the bootloader role with tinycrypt:
# the app role builds bootutil_public.c, which does not use Mbed TLS (and the app may have its
# own MBEDTLS_CONFIG_FILE), and an Mbed TLS build takes its configuration and headers from the
# port.
ifeq ($(MCUBOOT_CRYPTO),mbedtls)
ifeq ($(MCUBOOT_ROLE)$(MCUBOOT_MBEDTLS_SRC_C),bootloader)
$(error mcuboot_common.mk: MCUBOOT_CRYPTO_SEL_MBEDTLS needs MCUBOOT_MBEDTLS_SRC_C from the port)
endif
else
MCUBOOT_INC += -I$(MCUBOOT_LIB_DIR)/ext/tinycrypt/lib/include \
    -I$(MCUBOOT_LIB_DIR)/ext/mbedtls-asn1/include
endif
ifeq ($(MCUBOOT_ROLE),bootloader)
MCUBOOT_CFLAGS := -DMCUBOOT_ROLE_BOOTLOADER=1
ifneq ($(MCUBOOT_CRYPTO),mbedtls)
MCUBOOT_CFLAGS += -DMBEDTLS_CONFIG_FILE='"mbedtls_config_asn1.h"'
endif
else
MCUBOOT_CFLAGS := -DMCUBOOT_ROLE_APP=1
endif
MCUBOOT_CFLAGS += -DMCUBOOT_PRODUCTION=$(MCUBOOT_PRODUCTION)
ifeq ($(MCUBOOT_TEST_FI),1)
MCUBOOT_CFLAGS += -DMCUBOOT_TEST_FI=1
endif
MCUBOOT_CFLAGS += -DMCUBOOT_BL_VERSION='"$(MCUBOOT_BL_VERSION)"'

MCUBOOT_GEN_SRC_C :=
ifeq ($(MCUBOOT_ROLE),bootloader)
MCUBOOT_GEN_SRC_C += $(MCUBOOT_GEN_DIR)/mcuboot_keys_gen.c
endif

# Sources, relative to TOP.
MCUBOOT_BU_DIR := lib/mcuboot/boot/bootutil/src
MCUBOOT_SRC_DIR := shared/mcuboot/src
MCUBOOT_MBOOT_DIR := shared/tinyusb/mboot
MCUBOOT_TINYUSB_SRC_C :=

# Flash map, flash access policy, logging, request handoff and the update slot
# session, in both roles. shadow.c has no code without the ECC policy.
MCUBOOT_GLUE_SRC_C := $(addprefix $(MCUBOOT_SRC_DIR)/, \
    flash_map.c flash_map_backend.c shadow.c record_ring.c request.c update.c mcuboot_crc32.c log.c printf_lite.c)

# A SPI flash as device 1 is driven through drivers/memory/spiflash.c, which the port provides.
ifeq ($(MCUBOOT_SPIFLASH_ENABLE),1)
MCUBOOT_GLUE_SRC_C += $(MCUBOOT_SRC_DIR)/flash_spiflash.c
endif

ifeq ($(MCUBOOT_ROLE),app)
MCUBOOT_BOOTUTIL_SRC_C := $(MCUBOOT_BU_DIR)/bootutil_public.c
MCUBOOT_CRYPTO_SRC_C :=
MCUBOOT_FRONT_SRC_C := $(MCUBOOT_SRC_DIR)/app_api.c
else
# Image validation and the boot decision.
MCUBOOT_BOOTUTIL_SRC_C := $(addprefix $(MCUBOOT_BU_DIR)/, \
    tlv.c bootutil_find_key.c bootutil_img_hash.c bootutil_img_security_cnt.c image_validate.c \
    image_ecdsa.c bootutil_misc.c bootutil_area.c bootutil_loader.c bootutil_public.c \
    fault_injection_hardening.c)
MCUBOOT_GLUE_SRC_C += $(addprefix $(MCUBOOT_SRC_DIR)/, security_cnt.c seccnt_flash.c arm_jump.c)
ifeq ($(MCUBOOT_FIH),high)
MCUBOOT_GLUE_SRC_C += $(MCUBOOT_SRC_DIR)/fih_delay_rng.c
endif

# The loader and the swap algorithm: the single slot policy has no swap and takes the loader
# that boots the only slot, overwrite-external is the overwrite variant of the scratch swap file.
ifeq ($(MCUBOOT_POLICY),single)
MCUBOOT_BOOTUTIL_SRC_C += lib/mcuboot/boot/zephyr/single_loader.c
else
MCUBOOT_BOOTUTIL_SRC_C += $(addprefix $(MCUBOOT_BU_DIR)/, loader.c swap_misc.c caps.c)
ifeq ($(MCUBOOT_POLICY),overwrite-external)
MCUBOOT_BOOTUTIL_SRC_C += $(MCUBOOT_BU_DIR)/swap_scratch.c
else ifeq ($(MCUBOOT_SWAP_MODE),move)
MCUBOOT_BOOTUTIL_SRC_C += $(MCUBOOT_BU_DIR)/swap_move.c
else ifeq ($(MCUBOOT_SWAP_MODE),scratch)
MCUBOOT_BOOTUTIL_SRC_C += $(MCUBOOT_BU_DIR)/swap_scratch.c
else
MCUBOOT_BOOTUTIL_SRC_C += $(MCUBOOT_BU_DIR)/swap_offset.c
endif
endif

ifeq ($(MCUBOOT_CRYPTO),mbedtls)
MCUBOOT_CRYPTO_SRC_C := $(MCUBOOT_MBEDTLS_SRC_C)
else
MCUBOOT_CRYPTO_SRC_C := $(addprefix lib/mcuboot/ext/tinycrypt/lib/source/, ecc.c ecc_dsa.c sha256.c utils.c) \
    $(addprefix lib/mcuboot/ext/mbedtls-asn1/src/, asn1parse.c platform_util.c)
endif

# Boot flow, validation and the front ends.
MCUBOOT_FRONT_SRC_C := $(addprefix $(MCUBOOT_SRC_DIR)/, boot_main.c validate.c)
ifeq ($(MCUBOOT_DFU_ENABLE),1)
# dfu_glue.c binds the DFU core of shared/tinyusb/mboot. The core pads every download block to
# MBOOT_DFU_WRITE_ALIGN, which has to be the value the glue checks against.
MCUBOOT_FRONT_SRC_C += $(addprefix $(MCUBOOT_SRC_DIR)/, dfu_glue.c dfu_regions.c) \
    $(addprefix $(MCUBOOT_MBOOT_DIR)/common/, mboot_dfu.c mboot_region.c mboot_elem.c mboot_usbd.c)
MCUBOOT_INC += -I$(TOP)/$(MCUBOOT_MBOOT_DIR)/include
# The TinyUSB device core and the DFU class with the glue's vendor requests. The device controller
# driver and the TinyUSB include path (tusb_config.h) belong to the port.
MCUBOOT_TINYUSB_SRC_C := $(addprefix lib/tinyusb/src/, \
    tusb.c common/tusb_fifo.c device/usbd.c class/dfu/dfu_device.c class/vendor/vendor_device.c)
MCUBOOT_CFLAGS += -DMBOOT_DFU_WRITE_ALIGN=$(MCUBOOT_DFU_WRITE_ALIGN)
endif
ifeq ($(MCUBOOT_FSLOAD_ENABLE),1)
MCUBOOT_FRONT_SRC_C += $(addprefix $(MCUBOOT_SRC_DIR)/, fsload.c fsload_stream.c)
# fsload parses the element stream of the request with the DFU core's parser.
ifneq ($(MCUBOOT_DFU_ENABLE),1)
MCUBOOT_FRONT_SRC_C += $(MCUBOOT_MBOOT_DIR)/common/mboot_elem.c
MCUBOOT_INC += -I$(TOP)/$(MCUBOOT_MBOOT_DIR)/include
endif
ifneq ($(filter raw,$(MCUBOOT_FSLOAD)),)
MCUBOOT_FRONT_SRC_C += $(MCUBOOT_SRC_DIR)/vfs_raw.c
endif
# The readers include the library headers relative to the repository root. The library
# configuration is the same for the reader and the library (flags below), and has to stay so.
MCUBOOT_INC += -I$(TOP)
ifneq ($(filter fat,$(MCUBOOT_FSLOAD)),)
MCUBOOT_FRONT_SRC_C += $(MCUBOOT_SRC_DIR)/vfs_fat.c lib/oofatfs/ff.c lib/oofatfs/ffunicode.c
MCUBOOT_CFLAGS += -DFFCONF_H='"mcuboot_ffconf.h"'
endif
ifneq ($(filter lfs2,$(MCUBOOT_FSLOAD)),)
MCUBOOT_FRONT_SRC_C += $(MCUBOOT_SRC_DIR)/vfs_lfs.c lib/littlefs/lfs2.c lib/littlefs/lfs2_util.c
MCUBOOT_CFLAGS += -DLFS2_NO_MALLOC -DLFS2_NO_DEBUG -DLFS2_NO_WARN -DLFS2_NO_ERROR -DLFS2_NO_ASSERT -DLFS2_READONLY
endif
ifneq ($(filter gzip,$(MCUBOOT_FSLOAD)),)
MCUBOOT_FRONT_SRC_C += $(MCUBOOT_SRC_DIR)/gzstream.c $(addprefix lib/uzlib/, adler32.c crc32.c header.c tinflate.c)
MCUBOOT_CFLAGS += -DUZLIB_CONF_PARANOID_CHECKS=1
endif
endif
endif

MCUBOOT_SRC_C := $(MCUBOOT_BOOTUTIL_SRC_C) $(MCUBOOT_CRYPTO_SRC_C) $(MCUBOOT_GLUE_SRC_C) $(MCUBOOT_FRONT_SRC_C)
