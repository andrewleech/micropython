# This board requires a bootloader, either mboot or OpenMV's bootloader.
USE_MBOOT = 1

MCU_SERIES = n6
CMSIS_MCU = STM32N657xx
AF_FILE = boards/stm32n657_af.csv
ifeq ($(BUILDING_MBOOT),1)
SYSTEM_FILE = $(STM32LIB_CMSIS_BASE)/Source/Templates/system_stm32$(MCU_SERIES)xx_fsbl.o
else
SYSTEM_FILE = $(STM32LIB_CMSIS_BASE)/Source/Templates/system_stm32$(MCU_SERIES)xx_s.o
endif
STM32_N6_HEADER_VERSION = 2.3
DKEL = $(STM32_CUBE_PROGRAMMER)/bin/ExternalLoader/MX25UM51245G_STM32N6570-NUCLEO.stldr

LD_FILES = boards/OPENMV_N6/board.ld boards/common_n6_flash.ld
TEXT0_ADDR = 0x70080000

# MicroPython settings
MICROPY_FLOAT_IMPL = float
MICROPY_PY_BLUETOOTH ?= 1
MICROPY_BLUETOOTH_NIMBLE ?= 1
MICROPY_BLUETOOTH_BTSTACK ?= 0
MICROPY_PY_LWIP ?= 1
MICROPY_PY_NETWORK_CYW43 ?= 1
MICROPY_PY_SSL ?= 1
MICROPY_SSL_MBEDTLS ?= 1
MICROPY_VFS_LFS2 ?= 1

# Board specific frozen modules
FROZEN_MANIFEST ?= $(BOARD_DIR)/manifest.py

# User C modules
USER_C_MODULES = $(TOP)/examples/usercmodule

# OpenMV bootloader compatibility mode
# Set OPENMV_BOOTLOADER=1 to:
# - Configure XSPI at 200 MHz (matches OpenMV bootloader init)
# - Set DFU VID:PID to 37c5:9206 (OpenMV's USB IDs)
OPENMV_BOOTLOADER ?= 0

ifeq ($(OPENMV_BOOTLOADER),1)
# Add preprocessor define for C code
CFLAGS += -DOPENMV_BOOTLOADER

# Set OpenMV DFU USB VID:PID
BOOTLOADER_DFU_USB_VID = 0x37c5
BOOTLOADER_DFU_USB_PID = 0x9206
endif
