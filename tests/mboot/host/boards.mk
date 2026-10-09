# Host test configurations: BOARD names the MCUboot configuration (config headers and flash
# geometry) that a test builds against.
#
#   h5        the NUCLEO-H563ZI board with the STM32H5 device description of ports/stm32/mboot/mcuboot
#   stock     h5 on a flash without ECC
#   bl        h5 with a SPI flash as device 1 and the raw and gzip fsload readers
#   bl_ver    bl with a version check instead of the security counter
#   bl_spi    h5 with the secondary slot and the filesystem in a SPI NOR flash (device 1), raw and gzip readers
#   spi       h5 with the secondary slot in a SPI NOR flash
#   spi_big   spi with a primary slot whose trailer spans both 4 KiB blocks of an erase unit
#   glue_ow   h5 with the overwrite-external policy (DFU glue tests)
#   glue_single  h5 with the single slot policy (DFU glue tests)
#   plain     a flash without ECC and DFU, 4 KiB sectors, 8 byte write unit
#   move      plain with swap using move (the rollback protection is the security counter)
#   scratch   plain with swap using scratch and a scratch area of two erase units
#   overwrite plain with the overwrite-external policy: equal slots, no revert
#   single    plain with the single slot policy: one slot, security counter, intent area for fsload
#   nrf       1 MiB flash with 4 KiB sectors, 4 byte write unit, 0x200 byte image header, littlefs2
#   rt        8 MiB flash with 4 KiB sectors, 4 byte write unit, FAT filesystem
#   f7        the PYBD_SF6 as shipped: 2 MiB internal flash in runs of 32, 128 and 256 KiB sectors, 4 byte
#             write unit, no ECC, single slot policy with a counter in flash, SPI flash as device 1
#   bl_f7     f7 for the bootloader harness: DFU into the single slot, fsload from FAT on the SPI NOR flash
#   f7_offset the PYBD_SF6 flash map with swap using offset: primary slot of three 256 KiB sectors, secondary slot of four
#   f7_scratch  the PYBD_SF6 flash map with swap using scratch: two slots of three 256 KiB sectors, one scratch sector
#   portcnt   plain with the security counter kept by the port and the high FIH profile
#   fih_high  plain with the high FIH profile
#   mbedtls   plain with Mbed TLS (lib/mbedtls) as the crypto library
#
# Sets BOARD_DIR (mpconfigboard.h), MBOOT_DEV_DIR (mboot_dev.h) and MBOOT_CPP_FLAGS for
# shared/mboot/mboot_common.mk. TOP has to be set.
#
# ports/stm32/mboot/mcuboot/mboot_dev.h reads the flash geometry from the CMSIS device header of the
# STM32H5, which the host compiler can only parse with the stub in cmsis_host. The CMSIS
# directories are system directories so that the host builds with -Wundef -Werror ignore the
# warnings of the vendor headers. The boards that use that header need these flags; the others
# ignore them.

BOARD ?= h5

MBOOT_CPP_FLAGS := -isystem $(TOP)/lib/stm32lib/CMSIS/STM32H5xx/Include \
	-isystem $(TOP)/lib/CMSIS_6/CMSIS/Core/Include -idirafter $(TOP)/tests/mboot/host/cmsis_host

ifeq ($(BOARD),h5)
BOARD_DIR := $(TOP)/ports/stm32/boards/NUCLEO_H563ZI
MBOOT_DEV_DIR := $(TOP)/ports/stm32/mboot/mcuboot
MBOOT_CPP_FLAGS += -DSTM32H573xx
else
BOARD_DIR := $(TOP)/tests/mboot/host/boards/$(BOARD)
MBOOT_DEV_DIR := $(BOARD_DIR)
endif

# The Mbed TLS build names the library sources and its configuration: the part of lib/mbedtls that
# bootutil uses for ECDSA P-256 and SHA-256.
ifeq ($(BOARD),mbedtls)
MBOOT_MBEDTLS_SRC_C := $(addprefix lib/mbedtls/library/, asn1parse.c asn1write.c oid.c bignum.c \
	bignum_core.c bignum_mod.c bignum_mod_raw.c ecp.c ecp_curves.c ecdsa.c sha256.c platform_util.c constant_time.c)
MBOOT_CPP_FLAGS += -I$(TOP)/lib/mbedtls/include -I$(TOP)/lib/mbedtls/library \
	-DMBEDTLS_CONFIG_FILE='"$(TOP)/tests/mboot/host/mbedtls/mbedtls_config_host.h"'
endif
