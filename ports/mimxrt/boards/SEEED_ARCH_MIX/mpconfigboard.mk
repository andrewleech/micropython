MCU_SERIES = MIMXRT1052
MCU_VARIANT = MIMXRT1052DVL6B

MICROPY_FLOAT_IMPL = double
MICROPY_HW_FLASH_TYPE = qspi_nor_flash
MICROPY_HW_FLASH_SIZE = 0x800000  # 8MB
MICROPY_HW_FLASH_CLK = kFlexSpiSerialClk_133MHz
MICROPY_HW_FLASH_QE_CMD = 0x01
MICROPY_HW_FLASH_QE_ARG = 0x40

MICROPY_HW_SDRAM_AVAIL = 1
MICROPY_HW_SDRAM_SIZE  = 0x2000000  # 32MB

MICROPY_PY_LWIP = 1
MICROPY_PY_SSL = 1
MICROPY_SSL_MBEDTLS = 1

USE_UF2_BOOTLOADER = 1

FROZEN_MANIFEST ?= $(BOARD_DIR)/manifest.py

