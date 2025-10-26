# NUCLEO_WB55 board with Zephyr threading
# Build with: make BOARD=NUCLEO_WB55 BOARD_VARIANT=ZEPHYR

# Enable Zephyr threading
MICROPY_ZEPHYR_THREADING = 1

# Pass flag to C compiler
CFLAGS += -DMICROPY_ZEPHYR_THREADING=1
