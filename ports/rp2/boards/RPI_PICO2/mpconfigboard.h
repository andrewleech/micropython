// Board and hardware specific configuration
#define MICROPY_HW_BOARD_NAME                   "Raspberry Pi Pico2"

#if defined(USE_MBOOT)
// Per-board mboot configuration (DFU USB identity + UI).
#define MBOOT_USB_PID         (0xDFA1u)
#define MBOOT_LED_PIN         (25)
#define MBOOT_PRODUCT_STRING  "RPI_PICO2 Bootloader"
#endif
