# Enable/disable extra modules and features

# wiznet5k module for ethernet support; valid values are:
#   0    : no Wiznet support
#   5200 : support for W5200 module
#   5500 : support for W5500 module
# MICROPY_PY_NETWORK_WIZNET5K ?= 0 # Now controlled by Kconfig: CONFIG_MICROPY_PY_NETWORK_WIZNET5K_W5x00

# VFS FAT FS support
# MICROPY_VFS_FAT ?= 1 # Now controlled by Kconfig: CONFIG_MICROPY_VFS_FAT

# Encrypted/signed bootloader support (ensure the MBOOT_PACK_xxx values match stm32/mboot/Makefile)
# MBOOT_ENABLE_PACKING ?= 0 # Now controlled by Kconfig: CONFIG_MBOOT_ENABLE_PACKING
# MBOOT_PACK_CHUNKSIZE ?= $(CONFIG_MBOOT_PACK_CHUNKSIZE) # Value now taken directly from Kconfig in Makefile
# MBOOT_PACK_KEYS_FILE ?= $(CONFIG_MBOOT_PACK_KEYS_FILE) # Value now taken directly from Kconfig in Makefile
