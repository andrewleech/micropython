QEMU_ARCH = arm
QEMU_MACHINE = mps2-an385

# Enable Zephyr threading for this board
MICROPY_ZEPHYR_THREADING = 1

CFLAGS += -mthumb -mcpu=cortex-m3 -mfloat-abi=soft
CFLAGS += -DQEMU_SOC_MPS2
CFLAGS += -DMICROPY_HW_MCU_NAME='"Cortex-M3"'
# Cortex-M3 has no FPU - disable FPU in Zephyr config
CFLAGS += -D__FPU_PRESENT=0

LDSCRIPT = mcu/arm/mps2.ld

SRC_BOARD_O = shared/runtime/gchelper_native.o shared/runtime/gchelper_thumb2.o

MPY_CROSS_FLAGS += -march=armv7m

# Zephyr submodule required for threading
ifeq ($(MICROPY_ZEPHYR_THREADING),1)
GIT_SUBMODULES += lib/zephyr
endif
