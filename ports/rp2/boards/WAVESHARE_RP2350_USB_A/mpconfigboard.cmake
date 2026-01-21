# cmake file for Waveshare RP2350-USB-A

set(MICROPY_PY_BTREE 1)

# MICROPY_HW_FLASH_STORAGE_BYTES must be set here (not just in mpconfigboard.h)
# so the rp2350 linker script gets __micropy_flash_storage_bytes__.
set(PICO_BOARD "waveshare_rp2350_one")
set(MICROPY_HW_FLASH_STORAGE_BYTES 3145728)  # 4MB flash - 1MB firmware = 3MB filesystem

# Enable USB Host support
set(MICROPY_HW_USB_HOST 1)