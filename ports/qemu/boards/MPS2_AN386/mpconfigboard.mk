QEMU_ARCH = arm
QEMU_MACHINE = mps2-an386

# Enable Zephyr threading
MICROPY_ZEPHYR_THREADING = 1

CFLAGS += -mthumb -mcpu=cortex-m4 -mfloat-abi=hard -mfpu=fpv4-sp-d16
CFLAGS += -DQEMU_SOC_MPS2
CFLAGS += -DMICROPY_HW_MCU_NAME='"Cortex-M4"'

LDSCRIPT = mcu/arm/mps2.ld

SRC_BOARD_O = shared/runtime/gchelper_native.o shared/runtime/gchelper_thumb2.o

MPY_CROSS_FLAGS += -march=armv7emsp
